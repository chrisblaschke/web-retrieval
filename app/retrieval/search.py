"""Search backends: SearXNG (primary) and DDGS (news dates and news fallback)."""

import asyncio
from datetime import datetime, timezone

import httpx
from ddgs import DDGS
from ddgs.engines import ENGINES
from ddgs.exceptions import RatelimitException, TimeoutException

from .config import Settings
from .urls import URLRejected, host_of, normalize

TIME_RANGES = {"day": "d", "week": "w", "month": "m", "year": "y"}


class SearchError(Exception):
    def __init__(self, message: str, kind: str = "backend_error"):
        super().__init__(message)
        self.kind = kind


def iso_date(value) -> str | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat(timespec="seconds")
    except ValueError:
        return text[:40]


def result(url: str, title: str, snippet: str, backend: str, engines: list[str], published, publisher: str | None = None) -> dict | None:
    try:
        canon = normalize(url)
    except URLRejected:
        return None
    return {
        **({"publisher": publisher} if publisher else {}),
        "url": url,
        "canonical_url": canon,
        "title": (title or "").strip(),
        "snippet": (snippet or "").strip(),
        "source": host_of(canon).removeprefix("www."),
        "published_at": iso_date(published),
        "backend": backend,
        "engines": engines,
    }


# Multi-term canaries catch an engine silently answering only one of the words.
CANARIES = (
    ("python asyncio semaphore", ("asyncio", "semaphore")),
    ("kubernetes pod disruption budget", ("disruption", "budget")),
)
HEALTHY_RECHECK = 900.0  # seconds between canaries for an engine that passed
BENCH_MIN, BENCH_MAX = 60.0, 900.0  # a failing engine's recheck backs off from 1 to 15 minutes


class SearXNG:
    def __init__(self, s: Settings, client: httpx.AsyncClient):
        self.s = s
        self.client = client
        self.sem = asyncio.Semaphore(s.search_concurrency)
        self.pace = asyncio.Lock()
        self.query_lock = asyncio.Lock()
        self.next_request = 0.0
        self.quality_lock = asyncio.Lock()
        self.engines = list(dict.fromkeys(e.strip() for e in s.searxng_text_engines.split(",") if e.strip()))
        self.verdicts: dict[str, dict] = {}  # engine -> its latest canary or live verdict
        self.recheck_at: dict[str, float] = {}  # engine -> loop time its canary is due
        self.strikes: dict[str, int] = {}  # consecutive failures, for the backoff
        self.checked_at: str | None = None

    def _judge(self, engine: str, verdict: dict, backoff: bool = True):
        """Record a verdict. A failing engine is benched with exponential backoff, so its own
        canary doesn't keep hitting an upstream that is refusing us; the others keep serving."""
        now = asyncio.get_running_loop().time()
        self.verdicts[engine] = verdict
        if verdict["ok"]:
            self.strikes[engine] = 0
            self.recheck_at[engine] = now + HEALTHY_RECHECK
        elif not backoff:
            self.recheck_at[engine] = now + BENCH_MIN
        else:
            self.strikes[engine] = self.strikes.get(engine, 0) + 1
            # 2**4 already reaches the 15-minute cap; don't grow an unbounded power.
            self.recheck_at[engine] = now + min(BENCH_MIN * 2 ** min(self.strikes[engine] - 1, 4), BENCH_MAX)

    @property
    def quality_status(self) -> dict:
        engines = {e: self.verdicts[e] for e in self.engines if e in self.verdicts}
        return {
            "ok": any(v["ok"] for v in engines.values()),  # search works while any engine passes
            "engines": engines,
            "benched": sorted(e for e, v in engines.items() if not v["ok"]),
            "checked_at": self.checked_at,
        }

    async def quality(self) -> dict:
        """Check meaning, not HTTP availability. Never bless a failed or empty probe.

        Only engines whose recheck is due are probed, together in one request per canary, each
        judged on its own top three. Refreshes are serialized so agent batches don't stampede.
        """
        loop = asyncio.get_running_loop()
        async with self.quality_lock:
            due = [e for e in self.engines if loop.time() >= self.recheck_at.get(e, 0)]
            if due:
                for engine, (verdict, backoff) in (await self._canary(due)).items():
                    self._judge(engine, verdict, backoff)
                self.checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return self.quality_status

    async def _canary(self, engines: list[str]) -> dict[str, tuple[dict, bool]]:
        hits = dict.fromkeys(engines, 3)
        failed: dict[str, list[str]] = {}
        for query, terms in CANARIES:
            pending = [e for e in engines if e not in failed]
            if not pending:
                break
            try:
                rows, unresponsive = await self._search(
                    query, news=False, time_range=None, language="en-US", engines=pending,
                )
            except SearchError as e:
                # A failed canary request says nothing about an upstream's quality: retry soon,
                # without backoff. Only an allowlist violation warrants quarantine here.
                bad = e.kind == "untrusted_engine"
                verdict = {"ok": False, "errors": [str(e)], "quarantined": bad}
                return {**{x: (dict(verdict), bad) for x in pending},
                        **{x: ({"ok": False, "hits": hits[x], "errors": failed[x]}, True) for x in failed}}
            for u in unresponsive:
                name = u.split(":", 1)[0].strip()
                if name in pending:
                    failed.setdefault(name, []).append(u)
            for engine in pending:
                own = [r for r in rows if engine in r["engines"]][:3]
                hits[engine] = min(hits[engine], sum(
                    all(term in " ".join((r["title"], r["snippet"], r["url"])).lower() for term in terms)
                    for r in own
                ))
                if hits[engine] < 2 and engine not in failed:
                    failed[engine] = [f"irrelevant results: query terms missing for {query!r}"]
        return {e: ({"ok": e not in failed, "hits": hits[e], "errors": failed.get(e, [])}, True) for e in engines}

    async def search(self, query: str, *, news: bool, time_range: str | None, language: str | None, page: int = 1) -> tuple[list[dict], list[str]]:
        if news:
            return await self._search(query, news=True, time_range=time_range, language=language, page=page)
        # Check the circuit after waiting, so a CAPTCHA in one query stops the
        # rest of a queued batch instead of sending all of them into the same block.
        async with self.query_lock:
            return await self._general_search(query, time_range=time_range, language=language, page=page)

    async def _general_search(self, query: str, *, time_range: str | None, language: str | None, page: int) -> tuple[list[dict], list[str]]:
        quality = await self.quality()
        engines = [e for e, v in quality["engines"].items() if v["ok"]]
        errors = [f"{e}: {'quarantined' if v.get('quarantined', True) else 'canary failed'}: {'; '.join(v['errors'])}"
                  for e, v in quality["engines"].items() if not v["ok"]]
        if not engines:
            return [], errors or ["search: no general engines configured"]
        rows, unresponsive = await self._search(
            query, news=False, time_range=time_range, language=language, page=page, engines=engines,
        )
        # An engine can start refusing us after its canary passed. Bench it now, so the rest of
        # a queued batch skips it, rather than advertising stale success for 15 minutes.
        for engine in engines:
            failures = [e for e in unresponsive if e.split(":", 1)[0].strip() == engine]
            if failures:
                self._judge(engine, {"ok": False, "errors": failures})
        return rows, errors + unresponsive

    async def _search(self, query: str, *, news: bool, time_range: str | None, language: str | None, page: int = 1, engines: list[str] | None = None) -> tuple[list[dict], list[str]]:
        params = {"q": query, "format": "json", "pageno": page}
        if engines is not None:
            # SearXNG unions categories and engines, rather than intersecting them.
            # Sending both would silently re-enable the untrusted default engines.
            params["engines"] = ",".join(engines)
        else:
            params["categories"] = "news" if news else "general"
        if time_range in TIME_RANGES:
            params["time_range"] = time_range
        if language:
            params["language"] = language
        async with self.sem:
            async with self.pace:
                loop = asyncio.get_running_loop()
                await asyncio.sleep(max(0, self.next_request - loop.time()))
                self.next_request = loop.time() + self.s.search_interval
            try:
                r = await self.client.get(f"{self.s.searxng_url}/search", params=params, timeout=self.s.search_timeout + 2)
            except httpx.HTTPError as e:
                raise SearchError(f"searxng unreachable: {type(e).__name__}", "backend_down") from None
        if r.status_code != 200:
            raise SearchError(f"searxng HTTP {r.status_code}", "backend_down" if r.status_code >= 500 else "backend_error")
        try:
            data = r.json()
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError("missing results list")
        except ValueError:
            raise SearchError("searxng returned malformed search data") from None
        out = []
        for item in data.get("results", []):
            sources = item.get("engines") or [item.get("engine", "")]
            if engines is not None and not set(sources).issubset(engines):
                raise SearchError("searxng ignored the requested engine allowlist", "untrusted_engine")
            res = result(
                item.get("url", ""),
                item.get("title", ""),
                item.get("content", ""),
                "searxng",
                item.get("engines") or [item.get("engine", "")],
                item.get("publishedDate") or item.get("pubdate"),
            )
            if res:
                out.append(res)
        unresponsive = [f"{e}: {why}" for e, why in data.get("unresponsive_engines", [])]
        return out, unresponsive


def backends(spec: str, category: str) -> list[str]:
    """Validated explicit DDGS backend list. DDGS silently treats unknown names as `auto`, so a
    typo here would quietly fan out to every engine; fail at startup instead."""
    names = [b.strip() for b in spec.split(",") if b.strip()]
    unknown = [b for b in names if b not in ENGINES[category]]
    if unknown or not names or "auto" in names:
        raise ValueError(f"DDGS {category} backends must be an explicit list from {sorted(ENGINES[category])}; got {spec!r}")
    return names


class DDGSearch:
    def __init__(self, s: Settings):
        self.s = s
        self.news_backends = backends(s.ddgs_news_backends, "news")
        self.sem = asyncio.Semaphore(1)
        self.cooldown_until = 0.0

    def plan(self, skip: set[str]) -> list[str]:
        """Backends to try, minus engines SearXNG just reported refusing us: the same upstream
        would refuse us again through another path."""
        return [b for b in self.news_backends if b not in skip]

    def _run(self, query: str, time_range: str | None, region: str, max_results: int, names: list[str]) -> list[dict]:
        ddgs = DDGS(proxy=self.s.egress_proxy or None, timeout=int(self.s.search_timeout))
        return ddgs.news(
            query,
            region=region,
            safesearch="moderate",
            timelimit=TIME_RANGES.get(time_range or ""),
            max_results=max_results,
            backend=",".join(names),
        )

    async def search(self, query: str, *, time_range: str | None, region: str, max_results: int, names: list[str]) -> list[dict]:
        loop = asyncio.get_running_loop()
        async with self.sem:
            if loop.time() < self.cooldown_until:
                raise SearchError(f"ddgs cooling down for {self.cooldown_until - loop.time():.0f}s after a rate limit", "rate_limited")
            try:
                rows = await asyncio.wait_for(
                    asyncio.to_thread(self._run, query, time_range, region, max_results, names),
                    timeout=self.s.search_timeout * 2,
                )
            except (asyncio.TimeoutError, TimeoutException):
                raise SearchError("ddgs timed out", "timeout") from None
            except RatelimitException as e:
                self.cooldown_until = loop.time() + self.s.ddgs_cooldown
                raise SearchError(f"ddgs rate limited: {str(e)[:120]}", "rate_limited") from None
            except Exception as e:
                # DDGS also raises "No results found" when its backends fail or
                # return a challenge. That is not evidence of an empty search.
                raise SearchError(f"ddgs: {str(e)[:200]}") from None
        out = []
        for row in rows:
            res = result(
                row.get("href") or row.get("url", ""),
                row.get("title", ""),
                row.get("body", ""),
                "ddgs",
                names,
                row.get("date"),
                row.get("source"),
            )
            if res:
                out.append(res)
        return out
