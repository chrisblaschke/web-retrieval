#!/usr/bin/env python3
"""End-to-end validation of a running web-retrieval stack. Standard library only.

    python3 scripts/validate.py [--base http://127.0.0.1:8890] [--json out.json]

Each case calls the live API and checks one behaviour that matters: a source type reads right, a
failure is reported the right way, or a limit holds. Uses public test sites (toscrape.com,
httpbin(go).org, badssl.com, nip.io, 1u.ms), so it needs internet access. Deterministic fixture
tests that need no internet live in app/tests.
"""

import argparse
import concurrent.futures as cf
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("WEB_RETRIEVAL_URL", "http://127.0.0.1:8890")


def call(path, body=None, headers=None, timeout=240):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json", "x-client": "validate", **(headers or {})},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="replace")


def read1(url, **kw):
    _, d = call("/v1/read", {"urls": [url], "max_chars": 2000, **kw})
    return d["results"][0]


def err(doc):
    return (doc.get("error") or {}).get("kind")


CASES = []
RENDER = False


class Skip(Exception):
    pass


def case(group, name):
    def wrap(fn):
        CASES.append((group, name, fn))
        return fn
    return wrap


# ---- search -------------------------------------------------------------------------------------


@case("search", "SearXNG answers a general query")
def _():
    _, d = call("/v1/search", {"queries": ["python asyncio semaphore"], "max_results": 5, "backend": "searxng", "fresh": True})
    q = d["queries"][0]
    assert q["outcome"] in ("ok", "degraded") and len(q["results"]) >= 3, q
    assert sum(all(t in " ".join((r["title"], r["snippet"], r["url"])).lower()
                   for t in ("asyncio", "semaphore")) for r in q["results"][:3]) >= 2, q
    return f'{q["outcome"]}, {len(q["results"])} results, engines {sorted({e for r in q["results"] for e in r["engines"]})}'


@case("search", "DDGS (explicit news backends) answers a news query")
def _():
    _, d = call("/v1/search", {"queries": ["federal reserve interest rates"], "mode": "news", "max_results": 5, "backend": "ddgs", "fresh": True})
    q = d["queries"][0]
    step = q["route"][-1]
    assert step["backend"] == "ddgs" and q["results"], q
    return f'{len(q["results"])} results from {step["engines"]} in {step["ms"]} ms'


@case("search", "news search with a time range returns dated results")
def _():
    _, d = call("/v1/search", {"queries": ["federal reserve interest rates"], "mode": "news", "time_range": "week", "max_results": 8, "fresh": True})
    q = d["queries"][0]
    dated = sum(1 for r in q["results"] if r["published_at"])
    assert len(q["results"]) >= 3 and dated >= 3, (dated, q["route"])
    return f'{q["outcome"]}: {len(q["results"])} results, {dated} dated, engines {sorted({e for r in q["results"] for e in r["engines"]})}'


@case("search", "a query carrying a credential is refused before it leaves")
def _():
    _, d = call("/v1/search", {"queries": ["error with key sk-proj-" + "A" * 30]})
    q = d["queries"][0]
    assert q["outcome"] == "failed" and q["error"]["kind"] == "sensitive_query" and q["route"] == [], q
    return "sensitive_query, no backend called"


# ---- reading --------------------------------------------------------------------------------------


@case("read", "an article comes back clean, with metadata")
def _():
    d = read1("https://en.wikipedia.org/wiki/Web_scraping", fresh=True)
    assert d["outcome"] == "ok" and d["backend"] == "trafilatura" and d["chars_total"] > 5000 and d["title"], d
    assert d["content_sha256"] and d["retrieved_at"] and d["final_url"]
    return f'{d["chars_total"]} chars, title {d["title"]!r}, published_at {d["published_at"]}'


@case("read", "a repo page keeps its README (whole-page Markdown beats the article cut)")
def _():
    d = read1("https://github.com/searxng/searxng", fresh=True, max_chars=20000)
    html = next(s for s in d["route"] if s.get("step") == "html")
    assert d["backend"] == "page-markdown" and "metasearch" in d["content"].lower(), (d["backend"], html)
    return html["why"]


@case("read", "a list page keeps its item links")
def _():
    d = read1("https://news.ycombinator.com/", fresh=True, max_chars=20000)
    links = d["content"].count("item?id=")
    assert d["backend"] == "page-markdown" and links >= 30, (d["backend"], links)
    return f"{links} item links kept"


@case("read", "native Markdown is used when the publisher offers it")
def _():
    d = read1("https://developers.cloudflare.com/workers/", fresh=True)
    assert d["backend"] == "native-markdown" and d["content_type"] == "text/markdown", d
    return f'{d["chars_total"]} chars of publisher Markdown'


@case("read", "StackExchange questions come from the API with answers and dates")
def _():
    d = read1("https://stackoverflow.com/questions/1732348/regex-match-open-tags-except-xhtml-self-contained-tags", fresh=True)
    assert d["backend"] == "stackexchange-api" and d["published_at"] and "answer" in d["content"].lower(), d
    return f'{d["chars_total"]} chars, asked {d["published_at"]}, {d["route"][0].get("quota_remaining")} API calls left today'


@case("read", "GitHub file pages are read from the raw file")
def _():
    d = read1("https://github.com/searxng/searxng/blob/master/README.rst", fresh=True)
    assert d["route"][0]["step"] == "rewrite" and d["backend"].startswith("native") and "searxng" in d["content"].lower(), d
    return f'{d["backend"]} from {d["final_url"]}'


@case("read", "JSON is preserved")
def _():
    d = read1("https://hacker-news.firebaseio.com/v0/item/1.json", fresh=True)
    assert d["backend"] == "native-json" and '"by": "pg"' in d["content"], d
    return d["content"][:60].replace("\n", " ")


@case("read", "PDFs with a text layer are read with pdfium")
def _():
    d = read1("https://arxiv.org/pdf/1706.03762")
    assert d["backend"] == "pdfium" and "attention" in d["content"].lower(), d
    return f'{d["chars_total"]} chars'


@case("read", "a JavaScript-only page is flagged incomplete with a browser hint (rendering off)")
def _():
    d = read1("https://quotes.toscrape.com/js/", fresh=True)
    assert d["outcome"] == "incomplete" and "browser" in d["warning"], d
    return d["warning"][:90]


@case("read", "render=always says rendering is unavailable instead of pretending")
def _():
    if RENDER:
        raise Skip("render profile is on")
    d = read1("https://example.com/", render="always")
    assert err(d) == "render_unavailable", d
    return d["error"]["message"][:80]


@case("read", "long pages continue with next_offset, same content hash")
def _():
    u = "https://docs.python.org/3/library/asyncio-task.html"
    a = read1(u, max_chars=4000)
    b = read1(u, max_chars=4000, offset=a["next_offset"])
    assert a["truncated"] and b["offset"] == 4000 and a["content_sha256"] == b["content_sha256"] and a["headings"], (a.get("next_offset"), b.get("offset"))
    return f'{a["chars_total"]} chars, {len(a["headings"])} headings, chunk 2 starts at {b["offset"]}'


@case("read", "a section is returned by heading")
def _():
    d = read1("https://docs.python.org/3/library/asyncio-task.html", section="Task Groups", max_chars=8000)
    first = d["content"].split("\n", 1)[0].lower()
    assert first.startswith("## ") and "task groups" in first and d["chars_total"] < 20000, d["content"][:80]
    return f'{d["chars_total"]} chars under the heading'


@case("read", "objective keeps only relevant passages")
def _():
    d = read1("https://en.wikipedia.org/wiki/Web_scraping", objective="legal issues court cases", max_chars=1500)
    assert d["focused"] and len(d["content"]) <= 1500 and ("court" in d["content"].lower() or "legal" in d["content"].lower()), d
    return f'{len(d["content"])} of {d["chars_total"]} chars kept'


# ---- extraction ----------------------------------------------------------------------------------


@case("extract", "Scrapling extracts CSS, ::attr and XPath fields")
def _():
    _, d = call("/v1/extract", {"url": "https://quotes.toscrape.com/", "fresh": True, "fields": {
        "quotes": ".quote .text::text", "authors": "//small[@class='author']", "links": ".quote a::attr(href)"}})
    f = d["fields"]
    assert len(f["quotes"]) == 10 and len(f["authors"]) == 10 and f["links"][0].startswith("https://"), d
    return f'{d["backend"]}: {len(f["quotes"])} quotes, first author {f["authors"][0]!r}'


@case("extract", "a field that matches nothing stays empty, with a warning")
def _():
    _, d = call("/v1/extract", {"url": "https://quotes.toscrape.com/js/", "fresh": True, "fields": {"quotes": ".quote .text"}})
    assert d["fields"]["quotes"] == [] and "warning" in d, d
    return d["warning"][:80]


@case("extract", "tables come back as rows")
def _():
    _, d = call("/v1/extract", {"url": "https://en.wikipedia.org/wiki/List_of_HTTP_status_codes", "tables": True})
    assert d.get("tables"), d
    return f'{len(d["tables"])} tables'


# ---- security --------------------------------------------------------------------------------------
# Reserved, unused and loopback addresses only: nothing here touches a real metadata endpoint or
# another internal service. The private targets never answer either way.

BAD_URLS = [
    "file:///etc/passwd",
    "http://user:pass@" + "example.com/",  # synthetic credentials, not an email address
    "http://localhost:9/",
    "http://127.0.0.1:9/",
    "http://[::1]:9/",
    "http://[::ffff:127.0.0.1]:9/",
    "http://0x7f000001:9/",
    "http://2130706433:9/",
    "http://169.254.1.1/",
    "http://10.255.255.1/",
    "http://192.168.255.254/",
    "http://[::ffff:6440:1]/",  # CGNAT test address
    "http://searxng:8080/",
    "http://fixture.internal/",
    "http://fixture.ts.net:9/",
]


@case("security", "private, local and malformed URLs are refused up front")
def _():
    _, d = call("/v1/read", {"urls": BAD_URLS})
    leaks = [r["url"] for r in d["results"] if not r.get("error")]
    assert not leaks, leaks
    return ", ".join(sorted({err(r) for r in d["results"]}))


@case("security", "public names that resolve to private addresses are refused by the egress proxy")
def _():
    urls = ["http://127.0.0.1.nip.io:9/", "http://169.254.1.1.nip.io/", "http://10.255.255.1.nip.io/", "http://192.168.255.254.nip.io/"]
    _, d = call("/v1/read", {"urls": urls, "fresh": True})
    leaks = [r["url"] for r in d["results"] if err(r) != "blocked"]
    assert not leaks, d
    return "all blocked"


@case("security", "redirects into private space are refused on the hop")
def _():
    targets = ["http://127.0.0.1:9/", "http://10.255.255.1/", "http://169.254.1.1.nip.io/"]
    # httpbin.org and its clones now refuse private redirect targets themselves (HTTP 403), which
    # would test them rather than us. Postman Echo still redirects anywhere.
    docs = [read1("https://postman-echo.com/redirect-to?url=" + urllib.parse.quote(t, safe=""), fresh=True) for t in targets]
    assert all(err(d) == "blocked" for d in docs), [(d["url"], err(d)) for d in docs]
    return "loopback, private and link-local-by-DNS redirects all blocked"


@case("security", "DNS rebinding (public/loopback round robin) never reaches loopback")
def _():
    host = "make-1.1.1.1-rebind-127.0.0.1-rr.1u.ms"
    seen = []
    for i in range(6):
        d = read1(f"http://{host}/?n={i}", fresh=True)
        seen.append(err(d) or d["outcome"])
        assert not (d.get("content") and "web-retrieval" in d["content"]), d
    assert "blocked" in seen, seen
    return f"answers per try: {seen}"


@case("security", "foreign Host headers and cross-site origins are refused")
def _():
    s1, _ = call("/v1/stats", headers={"host": "evil.example"})
    s2, _ = call("/v1/search", {"queries": ["x"]}, headers={"origin": "https://evil.example"})
    assert s1 == 421 and s2 == 403, (s1, s2)
    return f"Host -> {s1}, Origin -> {s2}"


# ---- failure handling ---------------------------------------------------------------------------------


@case("failure", "403 is reported as access_denied with a browser hint")
def _():
    d = read1(f"https://httpbingo.org/status/403?n={secrets.token_hex(3)}")
    assert err(d) in ("access_denied", "needs_browser") and "browser" in d["error"]["message"], d
    return err(d)


@case("failure", "404 is reported as not_found and cached briefly")
def _():
    u = f"https://httpbingo.org/status/404?n={secrets.token_hex(3)}"
    a = read1(u)
    b = read1(u)
    assert err(a) == "not_found" and err(b) == "not_found" and b.get("cached"), (a, b)
    return "not_found, second call served from cache"


@case("failure", "429 is reported as rate_limited after one retry")
def _():
    t = time.time()
    d = read1(f"https://httpbingo.org/status/429?n={secrets.token_hex(3)}")
    assert err(d) == "rate_limited", d
    return f"rate_limited after {time.time() - t:.1f}s"


@case("failure", "a persistent 503 is retried exactly once")
def _():
    _, before = call("/v1/stats")
    d = read1(f"https://httpbingo.org/status/503?n={secrets.token_hex(3)}")
    _, after = call("/v1/stats")
    retries = after["http"]["retries"] - before["http"]["retries"]
    assert err(d) == "http_error" and retries == 1, (retries, d)
    return f"http_error after {retries} retry (Retry-After timing is covered by the fixture tests)"


@case("failure", "TLS errors are reported, not bypassed")
def _():
    d = read1("https://expired.badssl.com/", fresh=True)
    assert err(d) == "tls", d
    return d["error"]["message"][:80]


@case("failure", "binary content is refused")
def _():
    d = read1("https://httpbingo.org/image/png", fresh=True)
    assert err(d) == "unsupported_content", d
    return d["error"]["message"]


@case("failure", "a misleading content type is caught by sniffing")
def _():
    d = read1(f"https://httpbingo.org/response-headers?Content-Type=application/pdf&n={secrets.token_hex(3)}", fresh=True)
    assert err(d) == "malformed_content", d
    return d["error"]["message"][:80]


@case("failure", "oversized downloads stop at the byte cap")
def _():
    t = time.time()
    d = read1("https://www.python.org/ftp/python/3.13.0/Python-3.13.0.tgz", fresh=True)
    assert err(d) == "too_large", d
    return f"{err(d)} in {time.time() - t:.1f}s"


@case("failure", "unknown hosts fail fast")
def _():
    d = read1(f"https://no-such-host-{secrets.token_hex(4)}.com/", fresh=True)
    assert err(d) in ("dns", "unreachable") and d["duration_ms"] < 3000, d
    return f'{err(d)} in {d["duration_ms"]} ms'


@case("failure", "unreachable ports time out once, without retry storms")
def _():
    t = time.time()
    d = read1(f"https://example.com:81/?n={secrets.token_hex(3)}", fresh=True)
    took = time.time() - t
    assert err(d) == "timeout" and took < 20, (d, took)
    return f"timeout after {took:.1f}s"


@case("failure", "robots.txt disallow is honoured")
def _():
    d = read1("https://www.google.com/search?q=web+retrieval", fresh=True)
    assert err(d) == "robots_disallowed", d
    return "robots_disallowed"


@case("failure", "bad requests get 400 with a reason")
def _():
    s, d = call("/v1/read", {"urls": [f"https://example.com/{i}" for i in range(21)]})
    s2, _ = call("/v1/search", {"queries": [""]})
    assert s == 400 and s2 == 400, (s, d, s2)
    return f"{s}: {d['error'][:60]}"


# ---- cache, concurrency, politeness ------------------------------------------------------------------


@case("concurrency", "repeat searches are served from the shared cache")
def _():
    q = {"queries": ["rust async runtime comparison"], "max_results": 5}
    call("/v1/search", q)
    t = time.time()
    _, d = call("/v1/search", q)
    assert d["queries"][0]["cached"], d
    return f"cached in {(time.time() - t) * 1000:.0f} ms"


@case("concurrency", "simultaneous reads of one URL share one fetch")
def _():
    u = f"https://quotes.toscrape.com/page/2/?wr={secrets.token_hex(4)}"
    _, before = call("/v1/stats")
    with cf.ThreadPoolExecutor(6) as ex:
        docs = list(ex.map(lambda _: read1(u), range(6)))
    _, after = call("/v1/stats")
    fetched = after["http"]["requests"] - before["http"]["requests"]
    assert all(d["outcome"] == "ok" for d in docs) and fetched <= 2, (fetched, [d["outcome"] for d in docs])
    return f"6 callers, {fetched} HTTP requests (page + robots at most)"


@case("concurrency", "requests to one site are paced")
def _():
    urls = [f"https://quotes.toscrape.com/page/{i}/?wr={secrets.token_hex(3)}" for i in range(1, 7)]
    t = time.time()
    _, d = call("/v1/read", {"urls": urls, "max_chars": 500})
    took = time.time() - t
    ok = sum(r["outcome"] == "ok" for r in d["results"])
    assert ok == 6 and took >= 4.5, (ok, took)
    return f"6 pages from one host in {took:.1f}s (>= 5 x 1s interval)"


@case("concurrency", "four agents reading at once all succeed")
def _():
    sets = [
        ["https://en.wikipedia.org/wiki/HTTP", "https://en.wikipedia.org/wiki/Transmission_Control_Protocol", "https://docs.python.org/3/library/asyncio.html", "https://www.rfc-editor.org/rfc/rfc9110.html", "https://go.dev/doc/effective_go"],
        ["https://quotes.toscrape.com/tag/love/", "https://books.toscrape.com/", "https://httpbingo.org/html", "https://example.com/", "https://www.iana.org/help/example-domains"],
        ["https://en.wikipedia.org/wiki/Docker_(software)", "https://docs.docker.com/engine/network/", "https://kubernetes.io/docs/concepts/overview/", "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429", "https://blog.rust-lang.org/"],
        ["https://news.ycombinator.com/", "https://lobste.rs/", "https://en.wikipedia.org/wiki/SQLite", "https://sqlite.org/wal.html", "https://www.postgresql.org/docs/current/mvcc-intro.html"],
    ]
    t = time.time()
    with cf.ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda s: call("/v1/read", {"urls": s, "max_chars": 1500, "fresh": True}), sets))
    took = time.time() - t
    docs = [r for _, d in res for r in d["results"]]
    ok = sum(r["outcome"] == "ok" for r in docs)
    assert [s for s, _ in res] == [200] * 4 and ok >= 18, [(r["url"], r["outcome"], err(r)) for r in docs if r["outcome"] != "ok"]
    fails = {r["url"]: err(r) or r["outcome"] for r in docs if r["outcome"] != "ok"}
    return f"{ok}/20 pages in {took:.1f}s; not ok: {fails or 'none'}"


@case("mcp", "MCP lists three tools and runs web_search and web_read")
def _():
    hdr = {"accept": "application/json, text/event-stream"}
    s, d = call("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, headers=hdr)
    names = sorted(t["name"] for t in d["result"]["tools"])
    assert names == ["web_extract", "web_read", "web_search"], names

    def tool(name, args):
        _, d = call("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args}}, headers=hdr)
        return d["result"].get("structuredContent") or json.loads(d["result"]["content"][0]["text"])

    out = tool("web_search", {"queries": ["sqlite wal mode"], "max_results": 3})
    page = tool("web_read", {"urls": ["https://sqlite.org/wal.html"], "section": "Overview", "max_chars": 2000})
    assert out["queries"][0]["results"] and page["results"][0]["outcome"] == "ok", (out, page)
    return f"tools {names}; search -> {len(out['queries'][0]['results'])} results; read -> {page['results'][0]['chars_total']} chars"


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--json")
    ap.add_argument("-k", help="only cases whose name contains this")
    a = ap.parse_args()
    BASE = a.base.rstrip("/")
    global RENDER
    s, h = call("/health")
    RENDER = h["backends"].get("crawl4ai", {}).get("enabled", True)
    print(f"health {s}: " + ", ".join(f"{k}={'ok' if v['ok'] else 'DOWN'}" for k, v in h["backends"].items()))
    results = []
    for group, name, fn in CASES:
        if a.k and a.k.lower() not in name.lower():
            continue
        t = time.time()
        try:
            detail, ok = fn(), True
        except Skip as e:
            print(f"SKIP  {group:<11} {name:<70}        {e}", flush=True)
            continue
        except AssertionError as e:
            detail, ok = f"FAILED: {str(e)[:400]}", False
        except Exception as e:
            detail, ok = f"ERROR: {type(e).__name__}: {str(e)[:300]}", False
        took = time.time() - t
        results.append({"group": group, "case": name, "ok": ok, "seconds": round(took, 1), "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'}  {group:<11} {name:<70} {took:5.1f}s  {detail}", flush=True)
    _, stats = call("/v1/stats")
    passed = sum(r["ok"] for r in results)
    print(f"\n{passed}/{len(results)} passed")
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"health": h, "results": results, "stats": stats}, f, indent=2)
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
