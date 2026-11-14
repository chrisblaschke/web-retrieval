"""Known sources read through their native representation instead of their HTML page.

Deterministic URL rules, each visible in the route:
- github.com/<owner>/<repo>/blob/<ref>/<path>  ->  raw.githubusercontent.com (the file itself)
- StackOverflow / StackExchange question pages  ->  the public StackExchange API (question plus the
  top answers). The HTML pages sit behind a Cloudflare challenge that no plain HTTP client passes.
"""

import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import html_to_markdown

from .net import Fetcher, FetchError

GITHUB_BLOB = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$")
SE_SITES = {
    "stackoverflow.com": "stackoverflow",
    "superuser.com": "superuser",
    "serverfault.com": "serverfault",
    "askubuntu.com": "askubuntu",
    "mathoverflow.net": "mathoverflow.net",
    "stackapps.com": "stackapps",
}
SE_QUESTION = re.compile(r"^/(?:questions|q)/(\d+)")
SE_API = "https://api.stackexchange.com/2.3"


def github_raw(url: str) -> str | None:
    if m := GITHUB_BLOB.match(url.split("?", 1)[0].split("#", 1)[0]):
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return None


def stackexchange_target(url: str) -> tuple[str, str] | None:
    """(site, question id) for a StackExchange question URL."""
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    site = SE_SITES.get(host) or (host.removesuffix(".stackexchange.com") if host.endswith(".stackexchange.com") and host.count(".") == 2 else None)
    m = SE_QUESTION.match(parts.path)
    return (site, m.group(1)) if site and m else None


def _date(ts) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def _md(html: str) -> str:
    return html_to_markdown.convert(html or "").content.strip()


async def stackexchange(fetcher: Fetcher, site: str, qid: str, answers: int = 8) -> dict:
    """Question and top answers as Markdown. Raises FetchError when the API can't answer."""
    q = await _api(fetcher, f"{SE_API}/questions/{qid}?site={site}&filter=withbody")
    if not q.get("items"):
        raise FetchError("the StackExchange API has no such question", kind="not_found")
    item = q["items"][0]
    a = await _api(fetcher, f"{SE_API}/questions/{qid}/answers?site={site}&filter=withbody&sort=votes&order=desc&pagesize={answers}")
    parts = [
        f"# {_unescape(item.get('title', ''))}",
        f"Asked {_date(item.get('creation_date'))} - score {item.get('score')} - tags: {', '.join(item.get('tags', []))}",
        _md(item.get("body", "")),
    ]
    for ans in a.get("items", []):
        flag = ", accepted" if ans.get("is_accepted") else ""
        parts.append(f"## Answer (score {ans.get('score')}{flag}, {_date(ans.get('creation_date'))})\n\n{_md(ans.get('body', ''))}")
    total = item.get("answer_count", 0)
    if total > len(a.get("items", [])):
        parts.append(f"[{total - len(a.get('items', []))} lower-voted answers not shown]")
    return {
        "title": _unescape(item.get("title")),
        "author": (item.get("owner") or {}).get("display_name"),
        "published_at": _date(item.get("creation_date")),
        "updated_at": _date(item.get("last_activity_date")),
        "content": "\n\n".join(parts),
        "final_url": item.get("link"),
        "quota_remaining": a.get("quota_remaining", q.get("quota_remaining")),
    }


async def _api(fetcher: Fetcher, url: str) -> dict:
    r = await fetcher.get(url, timeout=12)
    try:
        data = json.loads(r.body)
    except ValueError:
        raise FetchError(f"StackExchange API returned HTTP {r.status} without JSON", kind="http_error") from None
    if r.status != 200 or data.get("error_id"):
        kind = "rate_limited" if data.get("error_id") == 502 or r.status == 429 else "http_error"
        raise FetchError(f"StackExchange API: {data.get('error_message') or r.status}", kind=kind)
    return data


def _unescape(text: str | None) -> str | None:
    return html.unescape(text) if text else text
