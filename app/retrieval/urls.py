"""URL validation and normalization.

The egress proxy is the real network control (it resolves names itself and refuses non-public
addresses, so DNS rebinding and redirects into the private network fail there). This module
rejects what is obviously wrong early, with a clear message, and gives every URL one canonical
form for caching and deduplication.
"""

import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING = re.compile(
    r"^(utm_[a-z_]+|gclid|dclid|fbclid|msclkid|yclid|mc_cid|mc_eid|igshid|_hsenc|_hsmi|ref_src|si)$",
    re.I,
)
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".ts.net", ".onion")


class URLRejected(ValueError):
    pass


def normalize(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise URLRejected("empty URL")
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as e:
        raise URLRejected(f"malformed URL: {e}") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise URLRejected(f"unsupported scheme {scheme!r}; only http and https")
    if parts.username or parts.password:
        raise URLRejected("URLs with credentials are not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise URLRejected("URL has no host")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise URLRejected("invalid international host name") from None
    check_host(host)

    netloc = f"[{host}]" if ":" in host else host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc += f":{port}"
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)]
    )
    return urlunsplit((scheme, netloc, parts.path or "/", query, ""))


def check_host(host: str) -> None:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host == "localhost" or host.endswith(BLOCKED_SUFFIXES) or "." not in host:
            raise URLRejected(f"host {host!r} is not a public internet name") from None
        return
    if not ip.is_global or ip.is_multicast:
        raise URLRejected(f"address {host} is not a public internet address")


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"
