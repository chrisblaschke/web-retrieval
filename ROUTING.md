# Routing

The service uses fixed rules, not a model. Every result includes the steps taken
in `route`. See README.md for all configuration defaults.

## Search

General search uses the configured SearXNG engine allowlist. Each engine must pass
two multi-term relevance probes before serving queries. Failing engines are
benched with backoff. Healthy engines keep serving; partial failures produce
`degraded`. The probe checks engine-wide behavior, not every query's relevance.

A failed general search stays failed. No hosted search provider is called.
For news, DDGS gets one attempt when SearXNG fails or returns undated results.
Engines that just refused a SearXNG request are excluded from that attempt.
Credential-looking queries are rejected before a backend is called.

Results are deduplicated by normalized URL. Searches cache for five minutes by
default, degraded results for one minute, and failures are not cached.

## Read

1. Reject non-public URLs, credentials in URLs and non-HTTP schemes.
2. Use the StackExchange API for its question pages, with HTTP fallback on failure.
   Rewrite GitHub blob URLs to the raw file.
3. Check robots.txt and fetch through the egress proxy. The proxy resolves and
   checks destinations, including redirect targets.
4. Preserve native Markdown, JSON and plain text. Read PDF text with PDFium.
5. For HTML, make an article extraction and whole-page Markdown from the same
   bytes. Keep the whole page if article extraction loses too much text, links,
   code, table rows or prices.
6. Flag script-built pages and bot challenges. If configured, try the optional
   renderer; otherwise report `incomplete` or an explicit error.
7. Apply section selection, passage ranking for an objective, and offset/budget.
   `content_sha256` identifies the full text representation before truncation.

There is no OCR or authenticated browsing. Native metadata dates are not verified.
A failed read says nothing about a page's contents.

Pages, raw HTML and short-lived errors share a SQLite cache. `no-store` responses
are not stored. Anonymous `private` responses use the short error TTL. Concurrent
identical reads share an upstream call. Cache keys include a source-code hash.

## Extract

Reuse cached HTML when available, otherwise fetch with the same network policy.
Run Scrapling CSS/XPath selectors and optionally return HTML tables. Missing
selectors stay empty; values are never guessed. With rendering enabled, a complete
miss can try the renderer. Adaptive selector relocation is not exposed by the API.

The MCP extraction tool exposes fields, tables and fresh. The HTTP extraction
endpoint also exposes the render choice.
