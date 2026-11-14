"""Focused regressions for the gateway review; no network or native PDF concurrency."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from retrieval import content
from retrieval.config import Settings
from retrieval.net import Fetcher, Response
import pytest


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retry_after_cap_applies_to_all_retryable_statuses(monkeypatch, status):
    async def run():
        fetcher = Fetcher(Settings(egress_proxy="", domain_interval=0), None)
        calls = []
        response = Response("https://example.com", "https://example.com", status, "text/plain", b"busy",
                            {"retry-after": "3600"})

        async def once(*args):
            calls.append(args)
            return response

        async def sleep(wait):
            pytest.fail(f"should return response, not sleep {wait}s")

        monkeypatch.setattr(fetcher, "_once", once)
        monkeypatch.setattr("retrieval.net.asyncio.sleep", sleep)
        try:
            assert await fetcher.get(response.url, paced=False) is response
            assert len(calls) == 1
            assert response.headers["retry-after"] == "3600"
        finally:
            await fetcher.close()

    asyncio.run(run())


@pytest.mark.parametrize("encoding,phrase", [("utf-8", "日本語 café résumé"), ("windows-1252", "café résumé €")])
def test_http_charset_preserved_in_page_and_raw(gateway, monkeypatch, encoding, phrase):
    url = "https://example.com/encoding"
    body = f"<html><body><p>{phrase}</p></body></html>".encode(encoding)

    async def fetch(*args, **kwargs):
        return Response(url, url, 200, "text/html", body, {"content-type": f"text/html; charset={encoding}"})

    monkeypatch.setattr(gateway.fetcher, "get", fetch)
    doc = asyncio.run(gateway._read_uncached(url, "never"))
    assert phrase in doc["variants"]["page"]
    assert phrase in gateway.cache.get("raw", url + "|0")["html"]
    raw, _, _ = asyncio.run(gateway._raw(url, False, True, [], "never"))
    assert phrase in raw


def test_queued_ddgs_queries_honor_rate_limit(monkeypatch):
    from retrieval.search import DDGSearch, RatelimitException, SearchError
    search = DDGSearch(Settings())
    calls = []

    def run(*args):
        calls.append(args)
        raise RatelimitException("rate limited")

    monkeypatch.setattr(search, "_run", run)

    async def batch():
        await search.sem.acquire()
        tasks = [asyncio.create_task(
            search.search(str(i), time_range=None, region="us-en", max_results=1,
                          names=["duckduckgo"])) for i in range(4)]
        await asyncio.sleep(0)  # all callers reach the locked semaphore before the first result
        search.sem.release()
        return await asyncio.gather(*tasks, return_exceptions=True)

    errors = asyncio.run(batch())
    assert all(isinstance(e, SearchError) and e.kind == "rate_limited" for e in errors)
    assert len(calls) == 1


def test_page_markdown_detects_utf8_without_header():
    phrase = "日本語 café résumé"
    assert phrase in content.page_markdown(f"<html><body><p>{phrase}</p></body></html>".encode(), "https://example.com")


def test_pdfium_operations_are_serialized(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()
    calls = 0

    class Document:
        def __init__(self, body):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                assert release.wait(2)
            else:
                second_entered.set()

        def get_metadata_dict(self):
            return {}

        def __len__(self):
            return 0

        def close(self):
            pass

    monkeypatch.setattr(content.pdfium, "PdfDocument", Document)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(content.pdf_doc, b"first")
        assert entered.wait(1)
        second = pool.submit(content.pdf_doc, b"second")
        try:
            assert not second_entered.wait(0.1), "PDFium entered concurrently"
        finally:
            release.set()
        first.result()
        second.result()
    assert calls == 2


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    from retrieval.service import Service
    svc = Service(Settings(data_dir=str(tmp_path), egress_proxy="", respect_robots=False))
    yield svc
    asyncio.run(svc.close())


def test_long_challenge_is_not_success(gateway, monkeypatch):
    body = ("<html><body><article><h1>Verify you are human</h1>" +
            "<p>Please complete verification before continuing to our website.</p>" * 20 +
            "</article></body></html>").encode()

    async def fetch(*args, **kwargs):
        return Response(args[0], args[0], 200, "text/html", body)

    monkeypatch.setattr(gateway.fetcher, "get", fetch)
    doc = asyncio.run(gateway._read_uncached("https://example.com/check", "never"))
    assert content.text_len(doc["content"]) > 200
    assert doc.get("incomplete") is True
    assert "browser" in doc["warning"]

    async def render(*args):
        return {"html": "<html><body><p>Real evidence.</p></body></html>",
                "markdown": "Real evidence.", "final_url": "https://example.com/check", "status": 200}

    gateway.renderer = object()
    monkeypatch.setattr(gateway, "_render", render)
    rendered = asyncio.run(gateway._read_uncached("https://example.com/check", "auto"))
    assert "Real evidence" in rendered["content"]
    assert "Verify you are human" not in rendered["content"]


@pytest.mark.parametrize("as_bytes", [True, False])
def test_xml_declared_xhtml_keeps_page_evidence(gateway, monkeypatch, as_bytes):
    url = "https://example.com/xhtml"
    phrase = "日本語 café résumé"
    html = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Plans</title></head>'
            f'<body><main><h1>Plans</h1><p>{phrase}</p>'
            '<table><tr><th>Plan</th><th>Price</th></tr>'
            '<tr><td>Research</td><td>$42</td></tr></table>'
            '<pre>retain_exact_evidence()</pre></main></body></html>')
    body = html.encode() if as_bytes else html
    page = content.page_markdown(body, url)
    assert phrase in page
    assert "retain_exact_evidence()" in page
    assert "$42" in page

    async def fetch(*args, **kwargs):
        return Response(url, url, 200, "application/xhtml+xml", html.encode())

    monkeypatch.setattr(gateway.fetcher, "get", fetch)
    doc = asyncio.run(gateway.read([url], format="page", render="never"))["results"][0]
    assert doc["outcome"] == "ok"
    assert doc["content"] == page
    assert doc["backend"] == "page-markdown"
    # Both Trafilatura and Scrapling receive decoded Unicode in the service too.
    assert phrase in content.html_doc(body, url)["content"]
    extracted = asyncio.run(gateway.extract(url, fields={"evidence": "pre"}, render="never"))
    assert extracted["outcome"] == "ok"
    assert extracted["fields"]["evidence"] == ["retain_exact_evidence()"]


@pytest.mark.parametrize("render", ["never", "auto"])
@pytest.mark.parametrize("incidental", [
    "<noscript>Please enable JavaScript to use interactive features.</noscript>",
    ("<noscript>Please enable JavaScript to use interactive features.</noscript>"
     "<script>" + "x" * (270 * 1024) + "</script>"),
    '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>',
    '<script src="/cdn-cgi/challenge-platform/scripts/precursor/main.js"></script>',
    "<p>The interface may say ‘verify you are human’ or ‘checking your browser’.</p>",
    "<p>The interface may say ‘please enable javascript to continue’.</p>",
    "<!-- <h1>Verify you are human</h1> -->",
    "<!--" + "<h1>x" * 16_000 + "-->",
    "<template><template>unused</template><h1>Verify you are human</h1></template>",
    *[f"<{tag}><h1>Verify you are human</h1><title>Access denied</title></{tag}>"
      for tag in ("script", "style", "template", "noscript")],
])
def test_complete_article_is_not_a_challenge(gateway, monkeypatch, render, incidental):
    url = "https://example.com/article"
    body = ("<html><head><title>Research findings</title></head><body><article>"
            "<h1>Research findings</h1>" + incidental + "".join(
                f"<p>Experiment {i} measured a different sample. The results provide "
                "evidence for reproducible research, with detailed observations and analysis.</p>"
                for i in range(12)) + "</article></body></html>").encode()

    async def fetch(*args, **kwargs):
        return Response(url, url, 200, "text/html", body)

    async def unexpected_render(*args):
        pytest.fail("complete server-rendered evidence must not invoke the browser")

    gateway.renderer = object()
    monkeypatch.setattr(gateway.fetcher, "get", fetch)
    monkeypatch.setattr(gateway, "_render", unexpected_render)
    doc = asyncio.run(gateway.read([url], render=render))["results"][0]
    assert doc["outcome"] == "ok"
    assert "warning" not in doc
    assert "Experiment 11" in doc["content"]
    assert content.text_len(doc["content"]) > gateway.s.thin_chars
    assert not any(step.get("challenge") for step in doc["route"])


@pytest.mark.parametrize("marker", [
    "<h1>Verify <span>you are human</span></h1>",
    "<title>Access&#32;denied</title>",
    "<style>" + " " * 65_000 + "</style><h1>Verify you are human</h1>",
    "<script>" + " " * (300 * 1024) + "</script><h1>Verify you are human</h1>",
    "<h1>Please enable JavaScript and cookies</h1>",
    "<h1>Please verify you are human</h1>",
    "<h1>Please enable JavaScript and cookies to continue.</h1>",
    "<title>Please verify you are human to continue!</title>",
    "<title>Just a moment...</title>",
    "<title>Access denied</title>",
    '<title>Attention Required! | Cloudflare</title>',
    '<title>Attention Required!</title>',
    '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1?ray=123"></script>',
    '<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/managed/v1"></script>',
    '<script>window._cf_chl_opt = {};</script>',
    '<div id="cf-chl-widget">Waiting</div>',
    '<script src="https://captcha-delivery.com/challenge.js"></script>',
    '<div id="px-captcha"></div>',
])
def test_cloudflare_style_interstitial_stays_incomplete(gateway, monkeypatch, marker):
    url = "https://example.com/challenge"
    body = ("<html><head>" + marker + "</head><body><main>" +
            "<p>This site protects its visitors. Wait for the security check before proceeding.</p>" * 20 +
            "</main></body></html>").encode()

    async def fetch(*args, **kwargs):
        return Response(url, url, 200, "text/html", body)

    monkeypatch.setattr(gateway.fetcher, "get", fetch)
    doc = asyncio.run(gateway.read([url], render="never"))["results"][0]
    assert content.text_len(doc["content"]) > gateway.s.thin_chars
    assert doc["outcome"] == "incomplete"
    assert "anti-bot challenge" in doc["warning"]
    assert "browser" in doc["warning"]
    assert any(step.get("challenge") for step in doc["route"])


@pytest.mark.parametrize("as_bytes", [True, False])
def test_large_normal_page_skips_dom_and_charset_detection(monkeypatch, as_bytes):
    html = ("<html><head><title>Research findings</title></head><body>" +
            "<p>Measurements support these research findings.</p>" * 100_000 +
            "</body></html>")
    body = html.encode() if as_bytes else html

    def unexpected(*args, **kwargs):
        pytest.fail("ordinary pages must skip charset detection and DOM parsing")

    monkeypatch.setattr(content.lxml.html, "fromstring", unexpected)
    monkeypatch.setattr(content, "decode_html", unexpected)
    assert content.looks_blocked(body) is False


@pytest.mark.parametrize("as_bytes", [True, False])
def test_large_unclosed_title_is_not_a_challenge(monkeypatch, as_bytes):
    html = "<html><head><title>" + "x" * 15_000_000
    body = html.encode() if as_bytes else html

    def unexpected(*args, **kwargs):
        pytest.fail("an unclosed ordinary title must not need a DOM or charset detection")

    monkeypatch.setattr(content.lxml.html, "fromstring", unexpected)
    monkeypatch.setattr(content, "decode_html", unexpected)
    assert content.looks_blocked(body) is False


@pytest.mark.parametrize("as_bytes", [True, False])
@pytest.mark.parametrize("container", ["script", "style", "template"])
def test_article_after_large_inline_payload_is_not_thin(as_bytes, container):
    html = ("<p>Please enable JavaScript to use interactive features.</p>"
            f"<{container}>" + "x" * (1_100_000) + f"</{container}>"
            "<article><h1>Research findings</h1>" +
            "<p>Measurements support these research findings.</p>" * 20 + "</article>")
    body = html.encode() if as_bytes else html
    assert content.looks_blocked(body) is False


@pytest.mark.parametrize("as_bytes", [True, False])
@pytest.mark.parametrize("padding, expected", [(65_000, True), (1_100_000, False)])
def test_challenge_heading_after_stylesheet(as_bytes, padding, expected):
    html = "<style>" + " " * padding + "</style><h1>Verify you are human</h1>"
    body = html.encode() if as_bytes else html
    assert content.looks_blocked(body) is expected


@pytest.mark.parametrize("as_bytes", [True, False])
@pytest.mark.parametrize("html, expected", [
    ('<noscript>Please enable JavaScript</noscript><div title="<template>">'
     + '<article>Research findings support this conclusion. </article>' * 20, False),
    ('<p title=">' + 'x' * 400 + '">Checking your browser</p>', True),
    ('<p>Checking your browser</p><div data-padding=' + 'x' * 1_000_000 + '></div>', True),
    *[(f'<{tag}>{code}</{tag}><h1>Verify you are human</h1>'
       + '<p>Boilerplate supporting the challenge interface.</p>' * 20, True)
      for tag in ("script", "style")
      for code in ('var example="<!--";', 'const lt = "<";')],
], ids=["attribute-template", "attribute-text", "long-attribute",
        "script-comment", "script-less-than", "style-comment", "style-less-than"])
def test_challenge_html_parser_boundaries(as_bytes, html, expected):
    body = html.encode() if as_bytes else html
    assert content.looks_blocked(body) is expected


@pytest.mark.parametrize("as_bytes", [True, False])
@pytest.mark.parametrize("html, expected", [
    ("<p>Please enable JavaScript.</p><article>"
     + "Research findings support this conclusion. " * 20 + "&", False),
    ("<html><body>Checking your browser &", True),
])
def test_challenge_visible_tail_with_unfinished_charref(as_bytes, html, expected):
    body = html.encode() if as_bytes else html
    assert content.looks_blocked(body) is expected


@pytest.mark.parametrize("as_bytes", [True, False])
@pytest.mark.parametrize("unfinished", ['<div title="', '<!--'])
def test_truncated_challenge_scan_keeps_markup_opaque(as_bytes, unfinished):
    prefix = "<html><body>" + unfinished
    html = prefix + "x" * (content.CHALLENGE_SCAN_BYTES - len(prefix))
    html += "<h1>Verify you are human</h1>"
    body = html.encode() if as_bytes else html
    parser = content._ChallengePage(200)
    # The exact capped prefix must not count attribute/comment payload as text.
    content._feed_challenge_prefix(parser, html[:content.CHALLENGE_SCAN_BYTES])
    assert parser.length == 0
    assert content.looks_blocked(body) is False


def test_challenge_scan_prefix_can_split_utf8():
    prefix = b"<p>The interface may say verify you are human.</p>"
    body = prefix + b"x" * ((1 << 20) - len(prefix) - 1) + "€".encode()
    assert content.looks_blocked(body) is False


@pytest.mark.parametrize("body", [
    b"<p>Please enable JavaScript&#32;and cookies</p>",
    b"<p>Please enable <span>JavaScript</span> and cookies</p>",
    b"<p>Checking your browser</p>",
    b"<p>Checking your browser</p><!--" + b"not visible " * 100 + b"-->",
    b"<p>Checking your browser</p><script>" + b"not visible " * 100 + b"</script>",
    (b"<p>Checking your browser</p><template><template>unused</template>"
     + b"not visible " * 100 + b"</template>"),
])
def test_thin_challenge_phrase_is_cleaned_before_precheck(body):
    assert content.looks_blocked(body) is True


@pytest.mark.parametrize("as_bytes", [True, False])
def test_large_article_with_challenge_prose_skips_dom(monkeypatch, as_bytes):
    prefix = ("<html><head><title>Browser research</title></head><body><article>"
              "<p>The interface may say ‘verify you are human’.</p>")
    # Multibyte text in a large article must remain safe to decode.
    prefix += "x" * (300 * 1024)
    html = prefix + "€" + "<p>Measurements support these research findings.</p>" * 100_000
    html += "</article></body></html>"
    body = html.encode() if as_bytes else html

    def unexpected(*args, **kwargs):
        pytest.fail("weak challenge prose must not need a DOM or charset detection")

    monkeypatch.setattr(content.lxml.html, "fromstring", unexpected)
    monkeypatch.setattr(content, "decode_html", unexpected)
    assert content.looks_blocked(body) is False


@pytest.mark.parametrize("body", [
    b"<p>Checking your browser</p><script>" + b"x" * 1000,
    b"<p>Checking your browser</p>" + b"<h1>x" * 16_000,
    b"<p>Checking your browser</p>" + b"<template>" * 16_000 + b"</template>" * 16_000 + b"article " * 100,
])
def test_malformed_large_page_is_not_a_challenge(body):
    assert content.looks_blocked(body) is False


@pytest.mark.parametrize("interstitial", [
    "<p>Please enable JavaScript and cookies to continue.</p>",
    "<p>Checking your browser</p>",
    "<p>Checking your browser</p><!--" + "<!--" * 16_000,
    ("<div data-state='{" + '"op":">","padding":"' + "x" * 400
     + '"}' + "'>Checking your browser</div>"),
    '<div data-state="' + "< > " * 100 + '">Checking your browser</div>',
    ("<p>Checking your browser</p><template><template>inner</template>"
     + "hidden " * 100 + "</template>"),
])
def test_thin_javascript_interstitial_still_needs_browser(gateway, monkeypatch, interstitial):
    url = "https://example.com/thin"
    body = f"<html><body>{interstitial}</body></html>".encode()
    assert content.looks_blocked(body, gateway.s.thin_chars) is True

    async def fetch(*args, **kwargs):
        return Response(url, url, 200, "text/html", body)

    monkeypatch.setattr(gateway.fetcher, "get", fetch)
    doc = asyncio.run(gateway.read([url], render="never"))["results"][0]
    assert doc["outcome"] == "incomplete"
    assert "anti-bot challenge" in doc["warning"]


@pytest.mark.parametrize("first_read", [True, False])
def test_no_store_html_never_populates_shared_cache(gateway, monkeypatch, first_read):
    calls = []
    url = "https://example.com/no-store"

    async def fetch(*args, **kwargs):
        calls.append(args)
        return Response(url, url, 200, "text/html", b"<html><body><p>Never cache this.</p></body></html>",
                        {"cache-control": "no-store"})

    monkeypatch.setattr(gateway.fetcher, "get", fetch)

    async def run():
        if first_read:
            await gateway._read_uncached(url, "never")
        else:
            await gateway._raw(url, False, False, [], "never")
        assert gateway.cache.get("doc", url) is None
        assert gateway.cache.get("raw", url + "|0") is None
        html, final, cached = await gateway._raw(url, False, False, [], "never")
        assert not cached
        assert len(calls) == 2

    asyncio.run(run())
