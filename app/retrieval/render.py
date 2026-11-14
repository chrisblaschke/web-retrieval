"""Crawl4AI client: renders pages that need a real browser (JavaScript, anti-bot interstitials)."""

import asyncio

import httpx

from .config import Settings


class RenderError(Exception):
    def __init__(self, message: str, kind: str = "render", status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


class Crawl4AI:
    def __init__(self, s: Settings, client: httpx.AsyncClient):
        self.s = s
        self.client = client
        self.sem = asyncio.Semaphore(s.render_concurrency)
        self.renders = 0

    async def render(self, url: str) -> dict:
        body = {
            "urls": [url],
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {
                    "cache_mode": "bypass",
                    "page_timeout": int(self.s.render_timeout * 1000) - 5000,
                    "wait_until": "domcontentloaded",
                    "delay_before_return_html": 1.5,
                    "remove_overlay_elements": True,
                    "scan_full_page": False,
                },
            },
        }
        headers = {"Authorization": f"Bearer {self.s.crawl4ai_api_token}"} if self.s.crawl4ai_api_token else {}
        async with self.sem:
            self.renders += 1
            try:
                r = await self.client.post(
                    f"{self.s.crawl4ai_url}/crawl", json=body, headers=headers, timeout=self.s.render_timeout + 10
                )
            except httpx.TimeoutException:
                raise RenderError("crawl4ai timed out", "timeout") from None
            except httpx.HTTPError as e:
                raise RenderError(f"crawl4ai unreachable: {type(e).__name__}", "backend_down") from None
        if r.status_code != 200:
            blocked = r.status_code == 400 and "blocked" in r.text.lower()
            raise RenderError(f"crawl4ai HTTP {r.status_code}: {r.text[:200]}", "blocked" if blocked else "backend")
        data = r.json()
        results = data.get("results") or []
        if not results:
            raise RenderError("crawl4ai returned no result", "backend")
        res = results[0]
        if not res.get("success"):
            msg = res.get("error_message") or "render failed"
            kind = "blocked" if "blocked" in msg.lower() or "egress" in msg.lower() or "not allowed" in msg.lower() else "render"
            raise RenderError(msg[:300], kind, res.get("status_code"))
        md = res.get("markdown") or {}
        if isinstance(md, str):
            md = {"raw_markdown": md}
        return {
            "final_url": res.get("redirected_url") or res.get("url") or url,
            "status": res.get("status_code") or 200,
            "html": res.get("html") or "",
            "markdown": (md.get("raw_markdown") or "").strip(),
            "metadata": res.get("metadata") or {},
        }
