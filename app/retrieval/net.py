"""Outbound HTTP: one shared session through the egress proxy, with a global concurrency cap and
bounded queue, one request at a time per host with a minimum gap between starts, at most one
retry, Retry-After handling, a size cap, no cookie jar, and robots.txt checks."""

import asyncio
import email.utils
import random
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.robotparser import RobotFileParser

from curl_cffi.requests import AsyncSession
from curl_cffi.requests import exceptions as cx

from .cache import Cache
from .config import Settings
from .urls import host_of, origin_of

RETRY_STATUS = {429, 500, 502, 503, 504}
# Ask for native Markdown first (publishers that support it, such as Cloudflare's Markdown for
# Agents, answer with text/markdown); everyone else ignores it and sends HTML as usual.
ACCEPT = "text/markdown, text/html;q=0.9, application/xhtml+xml;q=0.9, application/pdf;q=0.8, */*;q=0.7"


class FetchError(Exception):
    def __init__(self, message: str, *, kind: str = "fetch", status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retry_after = retry_after


@dataclass
class Response:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    headers: dict = field(default_factory=dict)
    elapsed_ms: int = 0


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


PROXY_STATUS = re.compile(r"CONNECT tunnel failed, response (\d{3})", re.I)


def proxy_failure(status: int, message: str = "") -> FetchError:
    """Map an egress-proxy refusal or failure (smokescreen) to an error kind."""
    low = message.lower()
    if status == 407 or "denied" in low:
        return FetchError("refused by the egress policy: the destination is not a public internet address", kind="blocked")
    if "resolve" in low:
        return FetchError("host name did not resolve", kind="dns")
    if status == 504 or "timed out" in low:
        return FetchError("timed out connecting to the site", kind="timeout")
    detail = message[:160] if message else "DNS failure or connection refused"
    return FetchError(f"could not connect to the site ({detail})", kind="unreachable")


def describe(err: Exception) -> FetchError:
    """FetchError for a curl_cffi exception."""
    text = str(err)
    low = text.lower()
    if m := PROXY_STATUS.search(text):
        return proxy_failure(int(m.group(1)))
    if isinstance(err, cx.ProxyError) or "proxy" in low:
        return FetchError(f"egress proxy error: {text[:200]}", kind="proxy")
    if isinstance(err, (cx.Timeout, cx.ConnectTimeout, cx.ReadTimeout)) or "timed out" in low:
        return FetchError("timed out", kind="timeout")
    if isinstance(err, cx.DNSError) or "resolve" in low:
        return FetchError("host name did not resolve", kind="dns")
    if isinstance(err, cx.CertificateVerifyError) or "certificate" in low or "ssl" in low:
        return FetchError(f"TLS error: {text[:160]}", kind="tls")
    if isinstance(err, cx.TooManyRedirects):
        return FetchError("too many redirects", kind="redirects")
    return FetchError(text[:200], kind="network")


class Fetcher:
    def __init__(self, s: Settings, cache: Cache):
        self.s = s
        self.cache = cache
        self.session = AsyncSession(
            impersonate="chrome",
            proxy=s.egress_proxy or None,
            timeout=s.fetch_timeout,
            allow_redirects=True,
            max_redirects=8,
            max_clients=s.fetch_concurrency + 4,
        )
        self.sem = asyncio.Semaphore(s.fetch_concurrency)
        self.pending = 0
        self.host_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.host_slots: defaultdict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(s.host_concurrency))
        self.host_next: dict[str, float] = {}
        self.requests = 0
        self.retries = 0

    async def close(self):
        await self.session.close()

    async def pace(self, host: str):
        """At most one request start per host per domain_interval, across all clients."""
        async with self.host_locks[host]:
            now = time.monotonic()
            wait = self.host_next.get(host, 0.0) - now
            if wait > 0:
                await asyncio.sleep(wait)
            self.host_next[host] = max(now, self.host_next.get(host, 0.0)) + self.s.domain_interval
        if len(self.host_next) > 5000:
            cutoff = time.monotonic() - 60
            for h in [h for h, t in self.host_next.items() if t < cutoff]:
                self.host_next.pop(h, None)
                self.host_locks.pop(h, None)
                self.host_slots.pop(h, None)

    async def get(
        self,
        url: str,
        *,
        timeout: float | None = None,
        retries: int | None = None,
        max_bytes: int | None = None,
        paced: bool = True,
    ) -> Response:
        retries = self.s.retries if retries is None else retries
        host = host_of(url)
        last: FetchError | None = None
        if self.pending >= self.s.max_pending:
            raise FetchError(f"{self.pending} fetches already queued; try again shortly", kind="busy", retry_after=5)
        for attempt in range(retries + 1):
            if attempt:
                self.retries += 1
            self.pending += 1
            try:
                async with self.host_slots[host]:  # one request at a time per host, across all clients
                    if paced or attempt:
                        await self.pace(host)
                    resp = await self._once(url, timeout or self.s.fetch_timeout, max_bytes or self.s.max_bytes)
            except FetchError as e:
                last = e
                # A timeout already spent the whole budget; retrying it would multiply the wait.
                if e.kind in ("network", "proxy") and attempt < retries:
                    await asyncio.sleep(0.5 * 2**attempt + random.random() * 0.3)
                    continue
                raise
            finally:
                self.pending -= 1
            if resp.status in RETRY_STATUS and attempt < retries:
                wait = retry_after_seconds(resp.headers.get("retry-after"))
                if wait is not None and wait > self.s.retry_after_cap:
                    return resp  # caller reports it with the server's Retry-After
                await asyncio.sleep(wait if wait is not None else 0.5 * 2**attempt + random.random() * 0.3)
                continue
            return resp
        raise last or FetchError("fetch failed")

    async def _once(self, url: str, timeout: float, max_bytes: int) -> Response:
        start = time.monotonic()
        async with self.sem:
            self.requests += 1
            r = None
            try:
                r = await self.session.get(url, stream=True, timeout=timeout, headers={"Accept": ACCEPT}, discard_cookies=True)
                if proxy_error := r.headers.get("x-smokescreen-error"):  # plain-http hop refused or failed
                    raise proxy_failure(r.status_code, proxy_error)
                declared = int(r.headers.get("content-length") or 0)
                if declared > max_bytes:
                    raise FetchError(f"response is {declared} bytes, over the {max_bytes} byte cap", kind="too_large")
                chunks, size = [], 0
                async for chunk in r.aiter_content():
                    size += len(chunk)
                    if size > max_bytes:
                        raise FetchError(f"response exceeded the {max_bytes} byte cap", kind="too_large")
                    chunks.append(chunk)
                headers = {k.lower(): v for k, v in r.headers.items()}
                return Response(
                    url=url,
                    final_url=str(r.url),
                    status=r.status_code,
                    content_type=headers.get("content-type", "").split(";")[0].strip().lower(),
                    body=b"".join(chunks),
                    headers=headers,
                    elapsed_ms=int((time.monotonic() - start) * 1000),
                )
            except FetchError:
                raise
            except Exception as e:  # curl_cffi raises its own hierarchy plus CurlError
                raise describe(e) from None
            finally:
                if r is not None:
                    try:
                        await r.aclose()
                    except Exception:
                        pass

    async def robots_allows(self, url: str) -> bool:
        """robots.txt check for our token (falls back to the * group).

        Missing or unreadable robots.txt allows the fetch: this service reads pages an agent asked
        for, one at a time, rather than crawling.
        """
        if not self.s.respect_robots:
            return True
        origin = origin_of(url)
        text = self.cache.get("robots", origin)
        if text is None:
            try:
                # Unpaced: a tiny file, cached for a day, that shouldn't delay the page behind it.
                r = await self.get(origin + "/robots.txt", timeout=8, retries=0, max_bytes=512 * 1024, paced=False)
                text = r.body.decode("utf-8", "replace") if r.status == 200 else ""
            except FetchError as e:
                # If the site can't even serve robots.txt, the page fetch would fail the same way.
                if e.kind in ("blocked", "dns", "unreachable", "timeout"):
                    raise
                text = ""
            self.cache.set("robots", origin, text, self.s.robots_ttl)
        if not text.strip():
            return True
        rp = RobotFileParser()
        rp.parse(text.splitlines())
        return rp.can_fetch(self.s.robots_token, url)
