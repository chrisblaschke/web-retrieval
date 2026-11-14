"""Settings, all overridable by environment variables of the same name (upper case)."""

import os
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Settings:
    searxng_url: str = "http://searxng:8080"
    # Explicit allowlist, each engine checked by a relevance canary before serving general
    # searches. Several independent upstreams, so one engine's CAPTCHA can't fail a query.
    searxng_text_engines: str = "duckduckgo,duckduckgo web,gmx,yandex"
    # Optional browser rendering (the compose `render` profile). Empty = off: pages that need
    # JavaScript or a browser session come back as `needs_browser` for the agent's own browser.
    crawl4ai_url: str = ""
    crawl4ai_api_token: str = ""
    # Every outbound fetch of a user-supplied URL goes through this proxy (smokescreen), which
    # refuses private, loopback, link-local, CGNAT and other non-public addresses.
    egress_proxy: str = "http://smokescreen:4750"
    data_dir: str = "/data"

    # DDGS, news only: dates for SearXNG's undated news and a second news source when SearXNG
    # fails. Explicit backends only (no `auto`). Engines SearXNG just reported refusing us are
    # skipped. Its text backends were dropped on 2026-10-30: they returned nothing or
    # only the first word of a query, and failed general queries remain explicit failures.
    ddgs_news_backends: str = "duckduckgo,bing"
    ddgs_cooldown: float = 300.0  # after DDGS reports a rate limit, skip it this long

    # Cache lifetimes, seconds. Every result says when it was retrieved and whether it was cached;
    # fresh=true bypasses the cache.
    search_ttl: int = 300
    news_ttl: int = 300
    degraded_ttl: int = 60  # results while some engines are down: real, but worth retrying soon
    page_ttl: int = 300  # continuation reads within 5 min see the same snapshot; content_sha256 shows a change
    robots_ttl: int = 86400
    error_ttl: int = 300  # 404/410 and incomplete reads; transient failures are never cached
    cache_max_mb: int = 2048

    # Budgets. Our politeness policy, not upstream entitlements; tune from measurements.
    search_concurrency: int = 1  # avoid parallel bursts to scraper-backed engines
    search_interval: float = 1.0  # minimum seconds between SearXNG request starts
    fetch_concurrency: int = 4
    host_concurrency: int = 1  # simultaneous requests to one host
    domain_interval: float = 1.0  # minimum seconds between request starts to one host
    max_pending: int = 64  # fetches waiting beyond this fail fast with `busy`
    render_concurrency: int = 2
    retries: int = 1  # at most one retry (5xx, 429 within retry_after_cap, dropped connection)
    retry_after_cap: float = 15.0

    # Timeouts, seconds.
    search_timeout: float = 8.0
    fetch_timeout: float = 20.0
    render_timeout: float = 60.0

    max_bytes: int = 15 * 1024 * 1024
    max_urls: int = 20
    max_queries: int = 8
    max_chars: int = 40_000
    thin_chars: int = 200  # below this a page with little text and lots of script needs a browser
    article_keep_ratio: float = 0.3  # article extraction keeping less than this of the page -> use the page
    hub_link_share: float = 0.3  # page text that is this much link text is a list/hub -> use the page
    respect_robots: bool = True
    robots_token: str = "web-retrieval"


def load() -> Settings:
    values = {}
    for f in fields(Settings):
        raw = os.environ.get(f.name.upper())
        if raw is None:
            continue
        if f.type is bool or f.type == "bool":
            values[f.name] = raw.strip().lower() in ("1", "true", "yes", "on")
        elif f.type in (int, "int"):
            values[f.name] = int(raw)
        elif f.type in (float, "float"):
            values[f.name] = float(raw)
        else:
            values[f.name] = raw
    return Settings(**values)
