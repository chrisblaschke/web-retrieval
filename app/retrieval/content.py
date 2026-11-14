"""Turning bytes into clean text: native Markdown/JSON/text kept as is, Trafilatura (article) and a
whole-page Markdown conversion for HTML, pdfium for PDFs, and a small BM25 ranker that keeps only
the passages relevant to an objective. Functions are synchronous; callers offload unbounded
CPU work to a worker thread, while looks_blocked uses cheap text scans inline."""

from html import unescape
from html.parser import HTMLParser
import json
import math
import re
import threading
from collections import Counter
from email.message import Message

import html_to_markdown
import lxml.html
import pypdfium2 as pdfium
import trafilatura
from trafilatura.metadata import extract_metadata

MARKDOWN_TYPES = ("text/markdown", "text/x-markdown")
JSON_TYPES = ("application/json", "application/ld+json")
HTML_TYPES = ("text/html", "application/xhtml+xml")
# Bot-detection scripts under challenge-platform/scripts also run on complete pages.
CHALLENGE = re.compile(
    r"cf-chl-|_cf_chl_opt|challenge-platform/h/[^/\s\"'<>]+/orchestrate|captcha-delivery|px-captcha",
    re.I,
)
CHALLENGE_TEXT = re.compile(
    r"enable javascript(?: and cookies)?|checking your browser|verify you are human", re.I,
)
CHALLENGE_HEADING = re.compile(
    rf"(?:please\s+)?(?:{CHALLENGE_TEXT.pattern})(?:\s+to (?:continue|proceed))?[.!]*",
    re.I,
)
CHALLENGE_TITLE = re.compile(
    r"^(?:just a moment\.{0,3}|access denied|attention required!(?:\s*\|\s*cloudflare)?)[.!\s]*$",
    re.I,
)
XML_DECLARATION = re.compile(r"^\ufeff?\s*<\?xml\b[^>]*\?>", re.I)
TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)
HTML_SPACE = re.compile(r"\s+")
CHALLENGE_HIDDEN = frozenset(("script", "style", "template", "noscript"))
# Real interstitials are tens of KB; headings past 1 MiB aren't credible, so cap event-loop work.
CHALLENGE_SCAN_BYTES = 1 << 20


def kind_of(content_type: str, body: bytes, url: str) -> str:
    """What the bytes are. Magic bytes beat the label: a PDF served as octet-stream is a PDF, and
    an error page or JSON served as application/pdf is not."""
    ct = content_type or ""
    head = body[:1024].lstrip().lower()
    if body[:5] == b"%PDF-":
        return "pdf"
    if ct in HTML_TYPES or head.startswith((b"<!doctype html", b"<html")) or (not ct and b"<html" in head):
        return "html"
    if ct in MARKDOWN_TYPES:
        return "markdown"
    if ct in JSON_TYPES or ct.endswith("+json"):
        return "json"
    if ct.startswith("text/") or ct.endswith("+xml") or ct == "application/xml":
        return "text"
    if ct == "application/pdf":
        return "malformed"
    if ct in ("", "application/octet-stream", "binary/octet-stream") and b"\0" not in body[:8192]:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return "binary"
        if head[:1] in (b"{", b"["):
            try:
                json.loads(text)
                return "json"
            except ValueError:
                pass
        return "text"
    return "binary"


def decode(body: bytes) -> str:
    for enc in ("utf-8",):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            pass
    return body.decode("latin-1", "replace")


def decode_html(body: bytes | str, content_type: str = "") -> str:
    """Honor the HTTP charset; otherwise detect from the document/bytes."""
    if isinstance(body, str):
        return body
    header = Message()
    header["content-type"] = content_type
    if encoding := header.get_content_charset():
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            pass
    return trafilatura.utils.decode_file(body)


def _feed_challenge_prefix(parser: HTMLParser, raw: str) -> None:
    """Parse a one-shot prefix, including text buffered for an unfinished charref."""
    parser.feed(raw)
    # close() would expose unfinished tags/comments as text. Only flush plain
    # text, keeping CDATA buffered for the existing unclosed-script fallback.
    if parser.rawdata and not parser.rawdata.startswith("<") and not parser.cdata_elem:
        parser.handle_data(unescape(parser.rawdata))


class _ChallengeSample(HTMLParser):
    """Weak phrases may occur in noscript, but never inside attributes/comments."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []

    def handle_data(self, data):
        self.text.append(data)


class _ChallengeFound(Exception):
    """Stop parsing as soon as a closed challenge heading settles the result."""


class _ChallengePage(HTMLParser):
    def __init__(self, thin):
        super().__init__(convert_charrefs=True)
        self.thin = thin
        self.hidden = []
        self.heading_tag = None
        self.heading = ""
        self.length = 0
        self.space = False

    def handle_starttag(self, tag, attrs):
        if tag in CHALLENGE_HIDDEN:
            self.hidden.append(tag)
        elif not self.hidden and tag in ("title", "h1"):
            self.heading_tag, self.heading = tag, ""

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag == self.heading_tag:
            text = self.heading.strip()
            if CHALLENGE_HEADING.fullmatch(text) or (
                tag == "title" and CHALLENGE_TITLE.fullmatch(text)
            ):
                raise _ChallengeFound
            self.heading_tag = None

    def count_text(self, data):
        if self.length >= self.thin:
            return
        for token in re.finditer(r"\s+|\S+", data):
            if token[0].isspace():
                self.space = self.length > 0
            else:
                self.length += token.end() - token.start() + self.space
                self.space = False
                if self.length >= self.thin:
                    return

    def handle_data(self, data):
        if self.hidden:
            return
        self.count_text(data)
        if self.heading_tag and len(self.heading) <= 256:
            self.heading += HTML_SPACE.sub(" ", data)[:257]
            self.heading = HTML_SPACE.sub(" ", self.heading)


def looks_blocked(html: bytes | str, thin: int = 200) -> bool:
    """Strong interstitial markers always count; incidental prose/noscript does not.

    A challenge heading still counts on a long boilerplate interstitial. Weak phrases
    elsewhere only count when the visible page is thin, not in a complete article.
    """
    head = html[:60_000]
    sample = decode(head) if isinstance(head, bytes) else head
    if CHALLENGE.search(sample):
        return True
    sample_parser = _ChallengeSample()
    _feed_challenge_prefix(sample_parser, sample)
    weak = bool(CHALLENGE_TEXT.search("".join(sample_parser.text)))
    prefix = html[:CHALLENGE_SCAN_BYTES]
    truncated = len(html) > CHALLENGE_SCAN_BYTES
    raw = prefix.decode("utf-8", errors="replace") if isinstance(prefix, bytes) else prefix
    parser = _ChallengePage(thin if weak and not truncated else 0)
    try:
        _feed_challenge_prefix(parser, raw)
    except _ChallengeFound:
        return True
    # Preserve the conservative fallback for an unclosed script. Its raw text
    # remains buffered by HTMLParser until a matching closing tag arrives.
    if parser.cdata_elem == "script":
        parser.count_text(parser.rawdata)
    return not truncated and weak and parser.length < thin


# Page chrome that never carries the evidence: dropped before the whole-page conversion.
CHROME = (
    "//script|//style|//noscript|//svg|//template|//iframe|//nav|//header|//footer|//aside|//form"
    "|//*[@role='navigation' or @role='banner' or @role='contentinfo' or @aria-hidden='true' or @hidden]"
    "|//a[contains(concat(' ', @class, ' '), ' headerlink ') or normalize-space()='¶']"  # heading permalinks
)
FENCE = re.compile(r"^\s*```", re.M)
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)
MONEY = re.compile(r"[$€£¥]\s?\d|\b\d[\d,.]*\s?(?:USD|EUR|GBP)\b")
LINK_TARGET = re.compile(r"\]\([^)]*\)")
BLANKS = re.compile(r"\n{3,}")


# Trafilatura and lxml.html parse with module-level lxml parsers, which are unsafe across threads:
# concurrent reads corrupted the heap and aborted the whole gateway. Pages are parsed one at a
# time; the GIL limited parallel parsing anyway.
LXML_LOCK = threading.Lock()


def page_markdown(html: bytes | str, url: str) -> str:
    """The whole main region (or body) as Markdown with absolute links and tables. Broader than
    the article extraction: keeps lists, link hubs, READMEs and pricing grids that Trafilatura
    treats as boilerplate."""
    with LXML_LOCK:
        try:
            tree = lxml.html.fromstring(XML_DECLARATION.sub("", decode_html(html), count=1))
        except (lxml.etree.ParserError, ValueError):
            return ""
        for node in tree.xpath(CHROME):
            node.drop_tree()
        tree.make_links_absolute(url, resolve_base_href=True, handle_failures="discard")
        mains = tree.xpath("//main|//*[@role='main']")
        root = mains[0] if len(mains) == 1 else tree.find("body")
        if root is None:
            root = tree
        page = lxml.html.tostring(root, encoding="unicode")
    md = html_to_markdown.convert(page).content
    return BLANKS.sub("\n\n", md).strip()


PAGE_LINK = re.compile(r"\[([^\]]*)\]\((?!#)[^)]*\)")


def link_share(md: str) -> float:
    """Fraction of the readable text that is link text. Articles sit near 0.0-0.2 (Wikipedia
    0.21); front pages, release lists and link hubs at 0.35-0.8, where the links are the content."""
    return sum(len(t) for t in PAGE_LINK.findall(md)) / max(1, text_len(md))


def evidence(md: str) -> dict[str, int]:
    """Countable structure an agent may need verbatim: code blocks, table rows and prices."""
    return {"code blocks": len(FENCE.findall(md)) // 2, "table rows": len(TABLE_ROW.findall(md)), "prices": len(MONEY.findall(md))}


def text_len(md: str) -> int:
    """Characters of readable text, not counting link targets or table pipes."""
    return len(re.sub(r"[|\-\s]+", " ", LINK_TARGET.sub("]", md)))


MOUNT = re.compile(rb"""<(div|main|section)\b[^>]*\bid=["']?(root|app|__next|__nuxt|svelte|main-app)\b[^>]*>\s*</\1>|<app-root""", re.I)
NOSCRIPT_JS = re.compile(rb"<noscript[\s\S]{0,400}?javascript", re.I)
INLINE_SCRIPT = re.compile(rb"<script\b[^>]*>([\s\S]*?)</script>", re.I)


def looks_js_shell(html: bytes | str, text_chars: int, thin: int = 200) -> bool:
    """Almost no readable text and the marks of a page that builds itself in the browser: an empty
    app mount point, a noscript plea, or more script than page. A short page with an analytics
    tag (example.com) is just short, and complete."""
    if text_chars >= thin:
        return False
    raw = html.encode() if isinstance(html, str) else html
    inline = sum(len(m) for m in INLINE_SCRIPT.findall(raw[:2_000_000]))
    return bool(MOUNT.search(raw) or NOSCRIPT_JS.search(raw) or inline >= 2000 or raw.lower().count(b"<script") >= 4)


def markdown_doc(body: bytes) -> dict:
    text = decode(body).strip()
    m = re.search(r"^#\s+(.+)$", text[:5000], re.M) or re.search(r"^title:\s*(.+)$", text[:2000], re.M)
    return {"title": m.group(1).strip().strip('"') if m else None, "content": text}


def json_doc(body: bytes) -> dict:
    text = decode(body)
    try:
        text = json.dumps(json.loads(text), indent=1, ensure_ascii=False)
    except ValueError:
        pass
    return {"title": None, "content": f"```json\n{text.strip()}\n```"}


HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.M)


def headings(text: str, limit: int = 80) -> list[dict]:
    """Markdown headings with their character offsets, for picking a section or an offset."""
    return [{"level": len(m.group(1)), "title": plain(m.group(2))[:120], "offset": m.start()} for m in HEADING.finditer(text)][:limit]


def plain(md: str) -> str:
    """Heading text without link targets or permalink marks: "[Task groups](https://…)¶" -> "Task groups"."""
    return LINK_TARGET.sub("]", md).replace("[", "").replace("]", "").rstrip("¶ ").strip()


def section(text: str, name: str) -> str | None:
    """The text under the first heading containing `name` (case-insensitive), up to the next
    heading of the same or a higher level."""
    want = name.strip().lower().lstrip("#").strip()
    marks = list(HEADING.finditer(text))
    for i, m in enumerate(marks):
        if want and want in plain(m.group(2)).lower():
            level = len(m.group(1))
            end = next((n.start() for n in marks[i + 1:] if len(n.group(1)) <= level), len(text))
            return text[m.start():end].strip()
    return None


def html_doc(html: bytes | str, url: str) -> dict:
    if isinstance(html, str):
        html = XML_DECLARATION.sub("", html, count=1)
    # deduplicate stays off: its cache is process-wide, so a page read a third time lost every
    # paragraph seen before.
    with LXML_LOCK:
        content = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_formatting=True,
            include_links=True,
            include_comments=False,
            favor_recall=True,
            deduplicate=False,
        ) or ""
        meta = None
        try:
            meta = extract_metadata(
                html,
                default_url=url,
                date_config={"original_date": True, "extensive_search": False},
            )
        except Exception:
            pass
    title = getattr(meta, "title", None)
    if not title:
        raw = html.encode() if isinstance(html, str) else html
        m = TITLE.search(raw[:200_000])
        title = re.sub(r"\s+", " ", decode(m.group(1))).strip() if m else None
    return {
        "title": title,
        "author": getattr(meta, "author", None),
        "published_at": getattr(meta, "date", None),
        "site_name": getattr(meta, "sitename", None),
        "description": getattr(meta, "description", None),
        "canonical_url": getattr(meta, "url", None),
        "content": content.strip(),
    }


# PDFium is not thread-safe, even for independent documents. Include construction,
# text extraction and native-object teardown in the same process-wide critical section.
PDFIUM_LOCK = threading.Lock()


def pdf_doc(body: bytes, max_pages: int = 300) -> dict:
    with PDFIUM_LOCK:
        return _pdf_doc(body, max_pages)


def _pdf_doc(body: bytes, max_pages: int) -> dict:
    pdf = pdfium.PdfDocument(body)
    try:
        meta = pdf.get_metadata_dict() or {}
        pages = []
        for i in range(min(len(pdf), max_pages)):
            page = pdf[i]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_bounded().replace("\r\n", "\n").replace("\r", "\n").strip()
                finally:
                    textpage.close()
                if text:
                    pages.append(f"[page {i + 1}]\n{text}")
            finally:
                page.close()
        total = len(pdf)
    finally:
        pdf.close()
    content = "\n\n".join(pages)
    if total > max_pages:
        content += f"\n\n[stopped after {max_pages} of {total} pages]"
    return {
        "title": (meta.get("Title") or "").strip() or None,
        "author": (meta.get("Author") or "").strip() or None,
        "published_at": None,
        "content": content,
        "pages": total,
    }


def text_doc(body: bytes) -> dict:
    return {"title": None, "content": decode(body).strip()}


# ---- focus: keep the passages that answer an objective -------------------------------------

STOP = set(
    "a an and are as at be but by for from has have how i if in into is it its of on or that the "
    "their there these this to was were what when where which who why will with you your does do "
    "can about than then them they our we us".split()
)
WORD = re.compile(r"\w+", re.U)


def tokens(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP and len(w) > 1]


def chunks(text: str, target: int = 700) -> list[str]:
    """Paragraph-ish chunks of about `target` chars; a heading stays with the text below it."""
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        only_heading = cur.startswith("#") and "\n" not in cur
        if cur and len(cur) + len(para) > target and not only_heading:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def focus(text: str, objective: str, budget: int) -> tuple[str, bool]:
    """BM25-rank chunks against the objective; return the best ones, in document order, within
    `budget` chars. Second value says whether anything matched."""
    parts = chunks(text)
    if not parts:
        return "", False
    q = set(tokens(objective))
    docs = [Counter(tokens(p)) for p in parts]
    n = len(docs)
    avg = sum(sum(d.values()) for d in docs) / n or 1
    df = Counter(t for d in docs for t in q if t in d)
    k1, b = 1.4, 0.75
    scores = []
    for i, d in enumerate(docs):
        length = sum(d.values()) or 1
        s = 0.0
        for t in q:
            tf = d.get(t, 0)
            if tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg))
        scores.append((s, i))
    ranked = [i for s, i in sorted(scores, reverse=True) if s > 0]
    if not ranked:
        return text[:budget], False
    sep = "\n\n…\n\n"
    keep, used = [], 0
    for i in ranked:
        piece = parts[i]
        if used + len(piece) > budget:
            if not keep:
                keep.append(i)
                parts[i] = piece[:budget]
            continue
        keep.append(i)
        used += len(piece) + len(sep)
    return sep.join(parts[i] for i in sorted(keep))[:budget], True
