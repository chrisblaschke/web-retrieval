"""Optional historical snapshot tests. See tests/fixtures/html/README.md.

Snapshots are not distributed. A current download may differ from these expectations.
"""

import gzip
import json
from pathlib import Path

import pytest

from retrieval import content
from retrieval.config import Settings
from retrieval.service import Service

HERE = Path(__file__).parent / "fixtures" / "html"
URLS = json.loads((HERE / "urls.json").read_text())
pytestmark = pytest.mark.skipif(
    not all((HERE / f"{name}.html.gz").is_file() for name in URLS),
    reason="optional historical HTML snapshots are not distributed; see fixtures/html/README.md",
)

# name -> (picked representation, {evidence: minimum occurrences in the returned text})
EXPECT = {
    # docs: every code block, with its line breaks
    "python-asyncio-task": ("page", {"async with asyncio.TaskGroup() as tg:": 1, "async def say_after(delay, what):\n": 1, "```": 64}),
    "effective-go": ("page", {"gofmt": 1, "```": 306}),
    "sqlite-wal": ("article", {"PRAGMA journal_mode=WAL": 1}),
    "wikipedia-web-scraping": ("article", {"hiQ": 1, "Craigslist": 1, "](https://en.wikipedia.org/wiki/": 100}),  # links kept
    "vercel-pricing": ("page", {"Pro\n\nPopular\n\n$20/mo.": 1, "Hobby\n\n$0/mo.": 1, "Enterprise": 1}),  # each plan with its price
    "hn-front": ("page", {"item?id=": 30}),  # a list: the item links are the evidence
    "bbc-technology": ("page", {"/news/articles/": 10}),
    "rust-blog": ("page", {"Announcing Rust 1.": 20}),
    "github-bun-releases": ("page", {"/oven-sh/bun/releases/tag/": 5, "Bun v1.": 20}),
    "github-searxng-repo": ("page", {"metasearch": 1, "docs.searxng.org": 1}),  # the README
}


def picker() -> Service:
    svc = Service.__new__(Service)  # only _html is used: no network, cache or clients
    svc.s = Settings()
    return svc


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_frozen_page_keeps_its_evidence(name):
    html = gzip.open(HERE / f"{name}.html.gz").read()
    doc, step = picker()._html(html, URLS[name])
    want, markers = EXPECT[name]
    assert step["picked"] == want, step
    for marker, n in markers.items():
        assert doc["content"].count(marker) >= n, (marker, doc["content"].count(marker))
    assert not content.CHALLENGE.search(doc["content"][:2000])
    assert "Link to this heading" not in doc["content"]


def test_rereading_a_page_returns_the_same_article():
    """Trafilatura's deduplicate cache is process-wide: with it on, the third read of a page lost
    a quarter of its paragraphs, in a gateway that every agent shares."""
    name = "python-asyncio-task"
    html = gzip.decompress((HERE / f"{name}.html.gz").read_bytes())
    lengths = {len(content.html_doc(html, URLS[name])["content"]) for _ in range(4)}
    assert len(lengths) == 1, lengths
