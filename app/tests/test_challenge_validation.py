"""Challenge and renderer regressions with no network or browser."""
import asyncio
from types import SimpleNamespace

import pytest

from retrieval.config import Settings
from retrieval.net import Response
from retrieval.service import Service

URL = "https://example.com/article"
CHALLENGE = "<html><body><h1>Verify you are human</h1></body></html>"
ARTICLE = ("<html><body><article><h1>Captcha research</h1>"
           "<p>A captcha may ask users to verify you are human.</p>" +
           "<p>We measured accessibility and completion rates across many participants.</p>" * 20 +
           "</article></body></html>")
SHELL = '<html><body><div id="app"></div><script src="/app.js"></script></body></html>'


@pytest.fixture
def gateway(tmp_path):
    svc = Service(Settings(data_dir=str(tmp_path), egress_proxy="", respect_robots=False))
    yield svc
    asyncio.run(svc.close())


def fake_http(gateway, monkeypatch, html):
    async def fetch(*args, **kwargs):
        return Response(URL, URL, 200, "text/html", html.encode())
    monkeypatch.setattr(gateway.fetcher, "get", fetch)


def fake_renderer(gateway, html, status=200, markdown=""):
    calls = []

    async def render(url):
        calls.append(url)
        return {"html": html, "markdown": markdown, "status": status, "final_url": url}

    gateway.renderer = SimpleNamespace(render=render)
    return calls


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("render", ["never", "auto"])
def test_extract_rejects_http_challenge_before_matching(gateway, monkeypatch, cached, render):
    fake_http(gateway, monkeypatch, CHALLENGE)
    if cached:
        gateway.cache.set("raw", URL + "|0", {"html": CHALLENGE, "final_url": URL}, 60)
    calls = fake_renderer(gateway, ARTICLE)
    result = asyncio.run(gateway.extract(URL, fields={"heading": "h1::text"}, render=render))
    if render == "never":
        assert result["outcome"] == "failed"
        assert result["error"]["kind"] == "needs_browser"
        assert calls == []
    else:
        assert result["outcome"] == "ok"
        assert result["fields"]["heading"] == ["Captcha research"]
        assert calls == [URL]


@pytest.mark.parametrize("status,html,expected", [
    (403, CHALLENGE, "failed"),
    (200, CHALLENGE, "failed"),
    (403, ARTICLE, "failed"),
    (500, ARTICLE, "failed"),
    (200, SHELL, "incomplete"),
])
def test_rendered_read_validates_before_caching(gateway, status, html, expected):
    fake_renderer(gateway, html, status)
    result = asyncio.run(gateway.read([URL], render="always"))["results"][0]
    assert result["outcome"] == expected
    assert result.get("warning") or result.get("error")
    cached = gateway.cache.get("doc", URL)
    assert cached is None or cached.get("incomplete")
    assert gateway.cache.get("raw", URL + "|1") is None


@pytest.mark.parametrize("status", [200, 403])
def test_rendered_extract_rejects_challenge(gateway, status):
    fake_renderer(gateway, CHALLENGE, status)
    result = asyncio.run(gateway.extract(URL, fields={"heading": "h1::text"}, render="always"))
    assert result["outcome"] == "failed"
    assert gateway.cache.get("raw", URL + "|1") is None


def test_cached_rendered_challenge_is_rejected(gateway):
    gateway.cache.set("raw", URL + "|1", {"html": CHALLENGE, "final_url": URL}, 60)
    fake_renderer(gateway, ARTICLE)
    result = asyncio.run(gateway.extract(URL, fields={"heading": "h1::text"}, render="always"))
    assert result["outcome"] == "failed"
    assert result["error"]["kind"] == "needs_browser"


def test_longer_http_shell_does_not_become_a_good_document(gateway, monkeypatch):
    fake_http(gateway, monkeypatch, SHELL.replace('<div id="app">', '<p>Loading evidence.</p><div id="app">'))
    fake_renderer(gateway, SHELL)
    result = asyncio.run(gateway.read([URL]))["results"][0]
    assert result["outcome"] == "incomplete"
    assert result["warning"]
    assert gateway.cache.get("doc", URL)["incomplete"]


@pytest.mark.parametrize("render", ["never", "always"])
def test_captcha_article_is_still_readable(gateway, monkeypatch, render):
    fake_http(gateway, monkeypatch, ARTICLE)
    fake_renderer(gateway, ARTICLE)
    result = asyncio.run(gateway.read([URL], render=render))["results"][0]
    assert result["outcome"] == "ok"
    assert "warning" not in result
    assert "verify you are human" in result["content"]
    assert gateway.cache.get("doc", URL)
    extracted = asyncio.run(gateway.extract(URL, fields={"heading": "h1::text"}, render=render))
    assert extracted["outcome"] == "ok"
    assert extracted["fields"]["heading"] == ["Captcha research"]
