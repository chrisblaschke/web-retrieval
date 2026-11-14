#!/usr/bin/env bash
# Probe result relevance, not just HTTP status/count:
#   bash scripts/probe-searxng.sh [base-url] [engine ...]
# Disabled engines can be probed explicitly before changing the gateway allowlist.
set -euo pipefail
base=${1:-${SEARXNG_URL:-http://127.0.0.1:8888}}
uv run --no-project python - "$base" "${@:2}" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

base, *engines = sys.argv[1:]
failed = False
for engine in engines or ["duckduckgo", "duckduckgo web", "gmx", "yandex"]:
    for query, terms in (
        ("python asyncio semaphore", ("asyncio", "semaphore")),
        ("kubernetes pod disruption budget", ("disruption", "budget")),
    ):
        params = urllib.parse.urlencode({
            "q": query, "format": "json", "engines": engine, "language": "en-US",
        })
        try:
            with urllib.request.urlopen(base.rstrip("/") + "/search?" + params, timeout=15) as response:
                data = json.load(response)
            rows = data["results"][:3]
            hits = sum(
                all(term in " ".join((r.get("title", ""), r.get("content", ""), r.get("url", ""))).lower()
                    for term in terms)
                and set(r.get("engines", [])) == {engine}
                for r in rows
            )
            ok = hits >= 2 and not data.get("unresponsive_engines")
            print(json.dumps({"engine": engine, "query": query, "ok": ok, "relevant_top3": hits,
                              "titles": [r["title"] for r in rows],
                              "errors": data.get("unresponsive_engines", [])}))
        except Exception as exc:
            ok = False
            print(json.dumps({"engine": engine, "query": query, "ok": False, "error": str(exc)}))
        failed |= not ok
sys.exit(1 if failed else 0)
PY
