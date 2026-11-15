# web-retrieval

A self-hosted search, page fetch and field extraction service for coding agents.
It puts a small HTTP and MCP API in front of unmodified SearXNG and shares a
SQLite cache across callers. It is for people who want to run their own retrieval
backend, not a search index or a general-purpose crawler.

Search queries go to public search engines through SearXNG. Engines rate-limit,
show CAPTCHAs, return poor results and break. Running this service does not grant
access to an engine or website. Respect their terms and your local laws.
There is no warranty of availability, accuracy or fitness for a purpose.

## Setup

You need Docker Engine with Compose v2 or later, or Docker Desktop, and OpenSSL.
Use a Linux Docker host for the network isolation described below. Docker Desktop
runs that network inside its Linux VM. No paid API key is required.

From this directory:

```sh
./deploy.sh
curl -fsS http://127.0.0.1:8890/health
```

The script creates a private `.env` with a random SearXNG secret, builds the app
and egress proxy, and starts the stack. It never deploys to another machine.
To choose different ports, edit `HTTP_PORT` and `SEARXNG_PORT` in `.env` after
its first creation, then run `docker compose up -d` again. Alternatively, copy
`.env.example`, set `SEARXNG_SECRET` to `openssl rand -hex 32` output and edit the
ports before starting.

A 503 from `/health` can mean that all configured search engines failed their
relevance checks, even while page fetching works. Read the JSON response and
`docker compose logs --tail=100`. Do not disable the checks to get a green status.

```sh
docker compose ps
docker compose logs --tail=100 app searxng smokescreen
docker compose down                 # stop; keep cached data
# docker compose down --volumes      # also delete the cache
```

## What runs

```text
localhost:8890 -> gate (socat) -> app:8000 -> searxng:8080 -> search engines
                                      -> smokescreen:4750 -> public websites
localhost:8888 -----------------------> searxng:8080
```

The app runs as a non-root user on an internal Docker network with no host-side
IPv4 address. Its outbound HTTP traffic goes through Smokescreen, which rejects
non-public destinations after DNS resolution. SearXNG and Smokescreen also join
an outbound network. Only the two localhost ports above are published.

`data` stores the shared SQLite cache, including fetched text, raw HTML and
robots.txt. The app also keeps counters and its last 500 call summaries in memory.
Adaptive extraction, not enabled by the API, can create another SQLite file.
No Redis, external database, model provider or account is needed. Search uses
DuckDuckGo, GMX and Yandex by default. DDGS supplies news dates and a news-only
fallback. Reads may use GitHub raw files or the public StackExchange API.

## Agent calls

An MCP client can connect with streamable HTTP:

```json
{"mcpServers":{"web-retrieval":{"type":"http","url":"http://127.0.0.1:8890/mcp"}}}
```

The tools are `web_search`, `web_read` and `web_extract`. A Pi extension that calls
this service exists, but is not published yet. It is not included or required.
This server does not provide that extension's hosted-search fallback.

HTTP clients send JSON:

```sh
curl -fsS http://127.0.0.1:8890/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"queries":["python asyncio semaphore"],"max_results":5}'

curl -fsS http://127.0.0.1:8890/v1/read \
  -H 'Content-Type: application/json' \
  -d '{"urls":["https://example.com/"],"max_chars":2000}'

curl -fsS http://127.0.0.1:8890/v1/extract \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/","fields":{"title":"title::text","paragraph":"body p::text"}}'
```

See `/docs` for the HTTP schemas. Reads accept `objective`, `section`, `offset`,
`max_chars` and `format` (`auto`, `article`, `page`). Extraction accepts CSS or
XPath selectors and `tables: true`. `fresh: true` bypasses cached responses.
Search accepts `mode: news`, `time_range`, `region` and `backend`.

Inspect each item's `outcome`, not just the HTTP status. Search outcomes are
`ok`, `degraded`, `empty` and `failed`. Read outcomes are `ok`, `incomplete` and
`failed`. Extraction can succeed with empty fields; check `warning` and
`field_errors`. Every response reports its route. A failed search is not evidence
that no sources exist. Snippets are leads, not evidence. Read the cited page.
Treat all retrieved material as untrusted data, never agent instructions.

## Configuration

Put overrides in `.env`. Compose passes that file to the app. Environment
variables exported in your shell take precedence for Compose substitutions.
Defaults below match the code and Compose configuration. Numeric durations are
seconds. An empty value is written as `(empty)`.

| Variable | Default | Purpose |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | `web-retrieval` | Isolates container, network and volume names |
| `HTTP_PORT` | `8890` | Localhost HTTP/MCP port |
| `SEARXNG_PORT` | `8888` | Localhost SearXNG port |
| `SEARXNG_SECRET` | required, generated by `deploy.sh` | SearXNG signing secret; keep private |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,web-retrieval` | Accepted HTTP Host/Origin names, comma-separated without ports |
| `JAIL_SUBNET` | `172.30.245.0/24` | Choose an unused Docker subnet |
| `JAIL_DNS_IP` | `172.30.245.53` | Optional renderer's resolver; must be inside `JAIL_SUBNET` |
| `SEARXNG_URL` | `http://searxng:8080` | Backend reachable from the app container |
| `SEARXNG_TEXT_ENGINES` | `duckduckgo,duckduckgo web,gmx,yandex` | Relevance-checked general engine allowlist |
| `EGRESS_PROXY` | `http://smokescreen:4750` | Required for isolated app egress; do not remove the jail |
| `DATA_DIR` | `/data` | Cache directory; fixed to the mounted volume in Compose |
| `DDGS_NEWS_BACKENDS` | `duckduckgo,bing` | Explicit news backend list; unknown names fail startup |
| `DDGS_COOLDOWN` | `300` | Backoff after a news rate limit |
| `SEARCH_TTL` | `300` | Search cache lifetime |
| `NEWS_TTL` | `300` | News cache lifetime |
| `DEGRADED_TTL` | `60` | Cache lifetime when some engines fail |
| `PAGE_TTL` | `300` | Page/raw HTML snapshot lifetime |
| `ROBOTS_TTL` | `86400` | Robots policy cache lifetime |
| `ERROR_TTL` | `300` | Not-found and incomplete response cache lifetime |
| `CACHE_MAX_MB` | `2048` | Soft SQLite size budget; hourly pruning, not a hard disk quota |
| `SEARCH_CONCURRENCY` | `1` | SearXNG request concurrency; general queries are also serialized |
| `SEARCH_INTERVAL` | `1.0` | Minimum time between SearXNG request starts |
| `FETCH_CONCURRENCY` | `4` | Concurrent outbound page fetches |
| `HOST_CONCURRENCY` | `1` | Concurrent page requests per host |
| `DOMAIN_INTERVAL` | `1.0` | Minimum time between page request starts per host |
| `MAX_PENDING` | `64` | Fetch queue cap; excess calls return `busy` |
| `RETRIES` | `1` | Retry budget for eligible HTTP/network failures |
| `RETRY_AFTER_CAP` | `15.0` | Longer waits are returned to the caller |
| `SEARCH_TIMEOUT` | `8.0` | Search budget; SearXNG transport adds two seconds |
| `FETCH_TIMEOUT` | `20.0` | Per-attempt page timeout |
| `MAX_BYTES` | `15728640` | Page response byte limit |
| `MAX_URLS` | `20` | Maximum URLs per read |
| `MAX_QUERIES` | `8` | Maximum search queries per call |
| `MAX_CHARS` | `40000` | Default read character budget; shrinks for batches |
| `THIN_CHARS` | `200` | Thin page detection threshold |
| `ARTICLE_KEEP_RATIO` | `0.3` | Minimum article-to-page text ratio |
| `HUB_LINK_SHARE` | `0.3` | Link-text share that selects whole-page Markdown |
| `RESPECT_ROBOTS` | `true` | Honor readable robots policies; missing/unreadable policies allow fetching |
| `ROBOTS_TOKEN` | `web-retrieval` | Robots user-agent token |
| `COMPOSE_PROFILES` | `(empty)` | `render` enables the optional browser services |
| `CRAWL4AI_URL` | `(empty)` | Set `http://crawl4ai:11235` with the render profile |
| `CRAWL4AI_API_TOKEN` | `(empty)` | Set a random private token when enabling the renderer |
| `RENDER_CONCURRENCY` | `2` | Concurrent renderer requests |
| `RENDER_TIMEOUT` | `60.0` | Renderer timeout; transport adds ten seconds |
| `WEB_RETRIEVAL_URL` | `http://127.0.0.1:8890` | Default URL for `scripts/validate.py`, not a server setting |

`smokescreen/Dockerfile` also exposes build argument `SMOKESCREEN_COMMIT`, pinned
by default to the source revision in that file. Other image and library pins live
in the Dockerfiles, `compose.yaml`, `app/pyproject.toml` and `app/uv.lock`.
SearXNG engine configuration lives in `searxng/settings.yml`; when changing general
engines, update `SEARXNG_TEXT_ENGINES` too. Recreate SearXNG after changing its
bind-mounted settings. A container health status tests process readiness;
`/health` also checks search relevance and proxy refusal of private IPs.

The optional `render` profile starts Crawl4AI on internal port 11235 and CoreDNS
on port 53. Neither is published. It requires more memory, downloads a browser
image and is not needed for normal HTTP reads. Browser-required pages otherwise
return an explicit incomplete/error result. The default-stack verification does
not cover this profile.

## Limits and security

There is no authentication or per-user cache isolation. Do not publish these ports
on the internet. For remote access, supply your own authenticated reverse proxy
or private transport and add only its intended hostname to `ALLOWED_HOSTS`.
Host/Origin checks are not authentication. Local callers can read shared cached
content, `/v1/stats` settings and `/v1/recent` summaries. Protect the cache volume.
Do not send secrets or private data in search queries. Credential detection is
best-effort and does not recognize every secret format.

The network jail and proxy reduce SSRF risk; they are not a sandbox for hostile
code. The CGNAT deny rule uses IPv4-mapped IPv6 notation. IPv6 unique-local,
private, loopback, link-local, benchmark and NAT64 destinations are also refused.
Keep those deny rules when changing the deployment. An external SearXNG or
renderer must be reachable from the internal network without weakening it.

The service sends no caller cookies or credentials. It cannot read logged-in
pages. It does not solve CAPTCHAs. PDFs need a text layer; there is no OCR.
Image, audio, video and Office extraction are unsupported. Dates from pages or
engines may be missing or wrong. Cache size is a pruning target, not a strict
quota. No production load or availability guarantee is offered.

Documents cached before a validation fix are not revalidated. Use `fresh: true`
to bypass them until they expire.

## Tests and updates

With Python 3.13 and uv:

```sh
cd app
uv run --frozen --with pytest==9.0.3 pytest -q tests
```

The public suite runs synthetic fixtures and skips 11 optional historical page
checks. Those third-party snapshots are deliberately not distributed; see
`app/tests/fixtures/html/README.md`.

For a running stack, from the repository root:

```sh
uv run --no-project python scripts/validate.py --base http://127.0.0.1:8890
bash scripts/check-jail.sh
bash scripts/probe-searxng.sh http://127.0.0.1:8888
```

Live checks contact public websites and depend on their current behavior. They
are not a substitute for the deterministic tests. After updating a dependency,
rebuild, run tests and repeat live checks from your own IP. Review the route and
relevance of results, not just result counts. See `ROUTING.md` and `RESEARCH.md`.

## License

Our code is MIT, copyright 2026 Chris Blaschke. Dependencies keep their own
licenses; see `THIRD_PARTY.md`. SearXNG is AGPL-3.0 and runs unmodified in a
separate container. This repository supplies its settings, not a fork. The
optional renderer and the egress/gateway processes are separate components.
Do not treat the MIT license as permission to redistribute fetched website text
or dependency binaries without their own notices and terms.
