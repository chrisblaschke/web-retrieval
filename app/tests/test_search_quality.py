"""A live search returning HTTP 200 can still be broken."""
import asyncio

import httpx
import pytest

from retrieval.config import Settings
from retrieval.search import DDGSearch, SearchError, SearXNG
from retrieval.service import Service


def rows(engine, relevant=True, query="python asyncio semaphore"):
    return [
        {"url": f"https://example.com/{i}",
         "title": query if relevant else "Download Python",
         "content": "Synchronization primitives" if relevant else "Welcome to Python.org",
         "engines": [engine]}
        for i in range(3)
    ]


def test_quarantines_irrelevant_engine_and_refreshes_after_ttl():
    async def run():
        requests = []
        repaired = False

        def respond(request):
            engines = request.url.params["engines"].split(",")
            assert "categories" not in request.url.params
            query = request.url.params["q"]
            requests.append((query, tuple(engines)))
            return httpx.Response(200, json={
                "results": [row for engine in engines for row in rows(
                    engine, engine == "duckduckgo" or repaired or query == "python asyncio semaphore", query)],
                "unresponsive_engines": [],
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            search = SearXNG(Settings(searxng_text_engines="duckduckgo,bing", search_interval=0), client)
            result, errors = await search.search("real user query", news=False, time_range=None, language=None)
            assert result and all(r["engines"] == ["duckduckgo"] for r in result)
            assert any("bing: quarantined: irrelevant results" in e for e in errors)
            assert search.quality_status["ok"] and search.quality_status["benched"] == ["bing"]
            # One request per canary covers every engine being checked.
            assert requests[:2] == [("python asyncio semaphore", ("duckduckgo", "bing")),
                                    ("kubernetes pod disruption budget", ("duckduckgo", "bing"))]
            await search.search("second query", news=False, time_range=None, language=None)
            assert ("second query", ("duckduckgo",)) in requests and len(requests) == 4
            repaired = True
            search.recheck_at["bing"] = 0  # only the benched engine is due
            status = await search.quality()
            assert status["ok"] and not status["benched"]
            assert requests[-2:] == [("python asyncio semaphore", ("bing",)),
                                     ("kubernetes pod disruption budget", ("bing",))]

    asyncio.run(run())


def test_captcha_in_one_engine_keeps_the_others_serving_and_backs_off():
    async def run():
        requests = []

        def respond(request):
            engines = request.url.params["engines"].split(",")
            query = request.url.params["q"]
            requests.append((query, tuple(engines)))
            blocked = query == "user query" and "duckduckgo" in engines
            return httpx.Response(200, json={
                "results": [row for e in engines if not (blocked and e == "duckduckgo") for row in rows(e, query=query)],
                "unresponsive_engines": [["duckduckgo", "CAPTCHA"]] if blocked else [],
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            search = SearXNG(Settings(searxng_text_engines="duckduckgo,gmx", search_interval=0), client)
            result, errors = await search.search("user query", news=False, time_range=None, language=None)
            assert result and {e for r in result for e in r["engines"]} == {"gmx"}
            assert errors == ["duckduckgo: CAPTCHA"]
            status = await search.quality()
            assert status["ok"] and status["benched"] == ["duckduckgo"]
            await search.search("next query", news=False, time_range=None, language=None)
            assert requests[-1] == ("next query", ("gmx",)), "the rest of a batch skips the blocked engine"
            first = search.recheck_at["duckduckgo"]
            # Still blocked at its recheck: the bench doubles instead of probing every minute.
            search._judge("duckduckgo", {"ok": False, "errors": ["duckduckgo: CAPTCHA"]})
            assert search.recheck_at["duckduckgo"] - first > 50

    asyncio.run(run())


@pytest.mark.parametrize("response", [
    {"results": rows("duckduckgo", False), "unresponsive_engines": []},
    {"results": [], "unresponsive_engines": []},
    {"results": rows("yahoo"), "unresponsive_engines": []},
    {"not": "search results"},
])
def test_bad_probe_never_sends_user_query(response):
    async def run():
        queries = []

        def respond(request):
            queries.append(request.url.params["q"])
            return httpx.Response(200, json=response)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            search = SearXNG(Settings(searxng_text_engines="duckduckgo", search_interval=0), client)
            result, errors = await search.search("private user query", news=False, time_range=None, language=None)
            assert result == [] and errors
            assert queries == ["python asyncio semaphore"]
            assert not (await search.quality())["ok"]

    asyncio.run(run())


@pytest.mark.parametrize("region, expected", [("us-en", "en-US"), ("uk-en", "en-GB"), ("wt-wt", "all")])
def test_region_reaches_searxng_without_losing_country(tmp_path, monkeypatch, region, expected):
    async def run():
        svc = Service(Settings(data_dir=str(tmp_path), egress_proxy="", search_interval=0))
        languages = []

        async def search(query, *, language, **kwargs):
            languages.append(language)
            return [], []

        monkeypatch.setattr(svc.searxng, "search", search)
        try:
            await svc.search(["query"], region=region)
            assert languages == [expected]
        finally:
            await svc.close()

    asyncio.run(run())


def test_ddgs_exhaustion_is_failure_not_evidence_of_no_matches(monkeypatch):
    search = DDGSearch(Settings())

    def exhausted(*args):
        raise Exception("No results found.")

    monkeypatch.setattr(search, "_run", exhausted)
    with pytest.raises(SearchError, match="No results found"):
        asyncio.run(search.search("query", time_range=None, region="us-en",
                                  max_results=5, names=["duckduckgo"]))


def test_persistent_engine_failure_keeps_backoff_capped():
    async def run():
        async with httpx.AsyncClient() as client:
            search = SearXNG(Settings(), client)
            loop = asyncio.get_running_loop()
            for _ in range(1100):
                before = loop.time()
                search._judge("duckduckgo", {"ok": False, "errors": ["CAPTCHA"]})
                assert search.recheck_at["duckduckgo"] <= loop.time() + 900
            assert search.recheck_at["duckduckgo"] >= before + 900
            search._judge("duckduckgo", {"ok": True})
            before = loop.time()
            search._judge("duckduckgo", {"ok": False, "errors": ["CAPTCHA"]})
            assert before + 60 <= search.recheck_at["duckduckgo"] <= loop.time() + 60

    asyncio.run(run())


def test_live_engine_failure_invalidates_cached_health():
    async def run():
        requests = []

        def respond(request):
            query = request.url.params["q"]
            requests.append(query)
            if query == "user query":
                return httpx.Response(200, json={"results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]})
            return httpx.Response(200, json={"results": rows("duckduckgo", query=query), "unresponsive_engines": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            search = SearXNG(Settings(searxng_text_engines="duckduckgo", search_interval=0), client)
            assert (await search.quality())["ok"]
            _, (result, errors) = await asyncio.gather(
                search.search("user query", news=False, time_range=None, language=None),
                search.search("next query", news=False, time_range=None, language=None),
            )
            assert not (await search.quality())["ok"]
            assert result == [] and "quarantined" in errors[0]
            assert "next query" not in requests

    asyncio.run(run())


@pytest.mark.parametrize("response", [
    {"results": rows("duckduckgo", False), "unresponsive_engines": []},
    {"results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]},
    {"results": rows("yahoo"), "unresponsive_engines": []},
])
def test_quality_failure_is_unhealthy_and_cannot_escape_through_ddgs(tmp_path, monkeypatch, response):
    async def run():
        svc = Service(Settings(data_dir=str(tmp_path), egress_proxy="", search_interval=0, searxng_text_engines="duckduckgo"))
        calls = []

        async def respond(request):
            if request.url.path == "/healthz":
                return httpx.Response(200)
            calls.append(request.url.params["q"])
            return httpx.Response(200, json=response)

        async def forbidden(*args, **kwargs):
            pytest.fail("A quarantined engine must not be retried through DDGS")

        monkeypatch.setattr(svc.internal, "_transport", httpx.MockTransport(respond))
        monkeypatch.setattr(svc.ddgs, "search", forbidden)
        try:
            health = await svc.health()
            assert not health["ok"] and not health["backends"]["search_quality"]["ok"]
            result = await svc._search_uncached(
                "user query", news=False, max_results=5, time_range=None,
                region="us-en", lang=None, backend="auto",
            )
            assert result["outcome"] == "failed"
            assert [step["backend"] for step in result["route"]] == ["searxng"]
            assert calls == ["python asyncio semaphore"]
        finally:
            await svc.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["connect", "invalid_json", "missing_results"])
def test_canary_execution_failure_fails_the_query_without_ddgs(tmp_path, monkeypatch, failure):
    async def run():
        svc = Service(Settings(data_dir=str(tmp_path), egress_proxy="", search_interval=0, searxng_text_engines="duckduckgo"))
        canary_calls, ddgs_calls = [], []

        def respond(request):
            if request.url.path == "/healthz":
                return httpx.Response(200)
            canary_calls.append(request.url.params["q"])
            assert request.url.params["engines"] == "duckduckgo"
            assert "categories" not in request.url.params
            if failure == "connect":
                raise httpx.ConnectError("SearXNG unavailable", request=request)
            if failure == "invalid_json":
                return httpx.Response(200, text="not JSON")
            return httpx.Response(200, json={"not": "search results"})

        def healthy_ddgs(query, time_range, region, max_results, names):
            ddgs_calls.append((query, names))
            return [{"href": "https://example.com/answer", "title": "User query answer",
                     "body": "A relevant answer to the user query"}]

        monkeypatch.setattr(svc.internal, "_transport", httpx.MockTransport(respond))
        monkeypatch.setattr(svc.ddgs, "_run", healthy_ddgs)
        try:
            health = await svc.health()
            assert not health["ok"] and not health["backends"]["search_quality"]["ok"]
            result = await svc._search_uncached(
                "user query", news=False, max_results=5, time_range=None,
                region="us-en", lang=None, backend="auto",
            )
            assert result["outcome"] == "failed" and result["error"]["kind"] == "search_failed"
            assert [step["backend"] for step in result["route"]] == ["searxng"]
            assert "canary failed" in result["route"][0]["unresponsive_engines"][0]
            assert ddgs_calls == []
            assert canary_calls == ["python asyncio semaphore"]
            assert not (await svc.health())["ok"]
        finally:
            await svc.close()

    asyncio.run(run())
