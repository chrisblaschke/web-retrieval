"""Structured extraction with Scrapling: named CSS/XPath fields and HTML tables."""

import re

from scrapling import Selector

PSEUDO = re.compile(r"^(?P<base>.*?)(?:::(?P<text>text)|::attr\((?P<attr>[^)]+)\))?\s*$", re.S)
MAX_ITEMS = 200
MAX_ITEM_CHARS = 4000


def is_xpath(sel: str) -> bool:
    return sel.lstrip().startswith(("/", "./", "(", "..")) or sel.startswith("xpath:")


def clean(value) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", str(value)).strip()[:MAX_ITEM_CHARS]


def select(page: Selector, selector: str, adaptive: bool) -> list[str]:
    selector = selector.strip()
    if is_xpath(selector):
        xp = selector.removeprefix("xpath:")
        nodes = page.xpath(xp, adaptive=adaptive, auto_save=adaptive)
        out = []
        for n in nodes[:MAX_ITEMS]:
            if hasattr(n, "get_all_text"):
                out.append(clean(n.get_all_text(separator=" ", strip=True)))
            else:
                out.append(clean(n))
        return [v for v in out if v]
    m = PSEUDO.match(selector)
    base, text, attr = m.group("base").strip(), m.group("text"), m.group("attr")
    nodes = page.css(base, adaptive=adaptive, auto_save=adaptive)
    out = []
    for n in nodes[:MAX_ITEMS]:
        if attr:
            v = n.attrib.get(attr.strip().strip("'\""))
            if v and attr.strip() in ("href", "src"):
                v = page.urljoin(v)
        elif text:
            v = n.text
        else:
            v = n.get_all_text(separator=" ", strip=True)
        if v:
            out.append(clean(v))
    return out


def tables(page: Selector, limit: int = 10, max_rows: int = 200) -> list[dict]:
    out = []
    for t in page.css("table")[:limit]:
        rows = []
        for tr in t.css("tr")[:max_rows]:
            cells = [clean(c.get_all_text(separator=" ", strip=True)) for c in tr.css("th, td")]
            if any(cells):
                rows.append(cells)
        if rows:
            cap = t.css("caption")
            out.append({"caption": clean(cap[0].get_all_text(strip=True)) if cap else None, "rows": rows})
    return out


def run(html: bytes | str, url: str, fields: dict[str, str], want_tables: bool, adaptive: bool, storage_file: str, host: str) -> dict:
    page = Selector(
        html,
        url=url,
        adaptive=adaptive,
        **({"storage_args": {"storage_file": storage_file, "url": host}} if adaptive else {}),
    )
    values, errors = {}, {}
    for name, sel in fields.items():
        try:
            values[name] = select(page, sel, adaptive)
        except Exception as e:  # bad selector syntax and the like
            values[name] = []
            errors[name] = f"{type(e).__name__}: {str(e)[:200]}"
    return {
        "fields": values,
        "field_errors": errors,
        "tables": tables(page) if want_tables else None,
        "title": clean(page.css("title::text").get() or "") or None,
    }
