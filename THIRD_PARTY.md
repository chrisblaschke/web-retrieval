# Third-party licenses

Our Python source, tests and deployment configuration are MIT. Dependencies
remain under their own licenses. This repository contains no vendored dependency
source or binaries. Package managers fetch dependencies when you build.

SearXNG runs unmodified in its own container and is AGPL-3.0. We supply only
`searxng/settings.yml`. Calling its HTTP API does not copy SearXNG into our app.
Keep its license and source access intact, especially if you distribute an image
or modify SearXNG. The separate socat process also retains its GPL terms.

The Python dependency inventory below covers all registry packages in
`app/uv.lock`, including platform-specific packages. License expressions come
from the exact releases' PyPI metadata, checked on 2026-11-14. A license list is
not a legal opinion or a complete operating-system image SBOM.

MPL-licensed files in certifi, orjson and tld remain under the MPL. For tld we use
the offered MPL-1.1 option rather than its GPL/LGPL alternatives. These unmodified
libraries do not require relicensing our separate source as copyleft. Keep their
notices and satisfy their source obligations if distributing their binaries.
PDFium has additional bundled dependency notices; retain the wheel's license
files. Smokescreen's image retains its own and its Go modules' license notices
under `/licenses`, including its unmodified MPL-2.0 modules. Their source is
available at the pinned Smokescreen revision. Python wheels retain their
installed license metadata.

Do not label a combined container image as entirely MIT. Before publishing images,
review notices and corresponding-source obligations for the exact image contents.
This preparation publishes neither images nor packages.

## Processes and build tools

| Component | Pin | License and source |
|---|---|---|
| SearXNG | `2026.11.2-cccbc9e15`, digest in Compose | [AGPL-3.0](https://github.com/searxng/searxng/blob/master/LICENSE), unmodified separate container |
| Smokescreen | `609eb8931420453daf5893509be0b25b21bd9edb` | [MIT](https://github.com/stripe/smokescreen/blob/609eb8931420453daf5893509be0b25b21bd9edb/LICENSE.txt), built unmodified |
| socat / alpine-docker wrapper | `1.8.1.3`, digest in Compose | socat GPL-2.0-only WITH OpenSSL-Exception (installed Alpine package metadata); wrapper [GPL-3.0](https://github.com/alpine-docker/socat/blob/master/LICENSE); separate container |
| Crawl4AI (optional) | `0.9.5`, digest in Compose | [Apache-2.0 with additional attribution requirement](https://github.com/unclecode/crawl4ai/blob/v0.9.5/LICENSE); see NOTICE |
| CoreDNS (optional) | `1.15.0`, digest in Compose | [Apache-2.0](https://github.com/coredns/coredns/blob/master/LICENSE) |
| Python | `3.13-slim`, digest in Dockerfile | [PSF-2.0 and included licenses](https://docs.python.org/3/license.html) |
| uv | `0.13.7` | [MIT OR Apache-2.0](https://github.com/astral-sh/uv#license) |
| Go / Alpine builder | `1.26.10-alpine` | [Go BSD-3-Clause](https://go.dev/LICENSE); Alpine packages have separate licenses; builder is not shipped in the final image |
| pytest (test-only) | `9.0.3` | [MIT](https://pypi.org/project/pytest/9.0.3/); test dependencies are not part of the runtime lock |

The base images include operating-system packages with their own licenses.
Pulling these upstream images is not the same as vendoring them into this repo.

## Python dependency inventory

| Dependency | Version | Declared license |
|---|---|---|
| [aiofile](https://pypi.org/project/aiofile/3.12.3/) | 3.12.3 | Apache-2.0 |
| [annotated-doc](https://pypi.org/project/annotated-doc/0.0.5/) | 0.0.5 | MIT |
| [annotated-types](https://pypi.org/project/annotated-types/0.8.0/) | 0.8.0 | MIT |
| [anyio](https://pypi.org/project/anyio/4.15.1/) | 4.15.1 | MIT |
| [attrs](https://pypi.org/project/attrs/26.1.0/) | 26.1.0 | MIT |
| [authlib](https://pypi.org/project/authlib/1.9.1/) | 1.9.1 | BSD-3-Clause |
| [babel](https://pypi.org/project/babel/2.18.0/) | 2.18.0 | BSD-3-Clause |
| [beartype](https://pypi.org/project/beartype/0.23.1/) | 0.23.1 | MIT |
| [cachetools](https://pypi.org/project/cachetools/7.2.2/) | 7.2.2 | MIT |
| [caio](https://pypi.org/project/caio/0.12.9/) | 0.12.9 | Apache-2.0 |
| [certifi](https://pypi.org/project/certifi/2026.7.22/) | 2026.7.22 | MPL-2.0 |
| [cffi](https://pypi.org/project/cffi/2.1.1/) | 2.1.1 | MIT-0 |
| [charset-normalizer](https://pypi.org/project/charset-normalizer/3.5.2/) | 3.5.2 | MIT |
| [click](https://pypi.org/project/click/8.5.0/) | 8.5.0 | BSD-3-Clause |
| [courlan](https://pypi.org/project/courlan/1.4.0/) | 1.4.0 | Apache-2.0 |
| [cryptography](https://pypi.org/project/cryptography/50.0.2/) | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| [cssselect](https://pypi.org/project/cssselect/1.6.0/) | 1.6.0 | BSD-3-Clause |
| [curl-cffi](https://pypi.org/project/curl-cffi/0.16.3/) | 0.16.3 | MIT |
| [cyclopts](https://pypi.org/project/cyclopts/5.2.0/) | 5.2.0 | Apache-2.0 |
| [dateparser](https://pypi.org/project/dateparser/1.4.3/) | 1.4.3 | BSD-3-Clause |
| [ddgs](https://pypi.org/project/ddgs/9.16.0/) | 9.16.0 | MIT |
| [dnspython](https://pypi.org/project/dnspython/2.9.0/) | 2.9.0 | ISC |
| [docstring-parser](https://pypi.org/project/docstring-parser/0.18.0/) | 0.18.0 | MIT |
| [email-validator](https://pypi.org/project/email-validator/2.3.0/) | 2.3.0 | Unlicense |
| [exceptiongroup](https://pypi.org/project/exceptiongroup/1.3.1/) | 1.3.1 | MIT |
| [fastapi](https://pypi.org/project/fastapi/0.143.2/) | 0.143.2 | MIT |
| [fastmcp](https://pypi.org/project/fastmcp/4.1.0/) | 4.1.0 | Apache-2.0 |
| [fastmcp-slim](https://pypi.org/project/fastmcp-slim/4.1.0/) | 4.1.0 | Apache-2.0 |
| [griffelib](https://pypi.org/project/griffelib/2.3.2/) | 2.3.2 | ISC |
| [h11](https://pypi.org/project/h11/0.16.0/) | 0.16.0 | MIT |
| [html-to-markdown](https://pypi.org/project/html-to-markdown/3.18.1/) | 3.18.1 | MIT |
| [htmldate](https://pypi.org/project/htmldate/1.11.0/) | 1.11.0 | Apache-2.0 |
| [httpcore](https://pypi.org/project/httpcore/1.0.9/) | 1.0.9 | BSD-3-Clause |
| [httpcore2](https://pypi.org/project/httpcore2/2.13.1/) | 2.13.1 | BSD-3-Clause |
| [httptools](https://pypi.org/project/httptools/0.9.0/) | 0.9.0 | MIT |
| [httpx](https://pypi.org/project/httpx/0.28.1/) | 0.28.1 | BSD-3-Clause |
| [httpx2](https://pypi.org/project/httpx2/2.13.1/) | 2.13.1 | BSD-3-Clause |
| [httpx2-jsfetch](https://pypi.org/project/httpx2-jsfetch/1.0/) | 1.0 | BSD-3-Clause |
| [idna](https://pypi.org/project/idna/3.20/) | 3.20 | BSD-3-Clause |
| [jaraco-classes](https://pypi.org/project/jaraco-classes/3.4.0/) | 3.4.0 | MIT |
| [jaraco-context](https://pypi.org/project/jaraco-context/6.1.2/) | 6.1.2 | MIT |
| [jaraco-functools](https://pypi.org/project/jaraco-functools/4.6.0/) | 4.6.0 | MIT |
| [jeepney](https://pypi.org/project/jeepney/0.9.0/) | 0.9.0 | MIT |
| [joserfc](https://pypi.org/project/joserfc/1.7.5/) | 1.7.5 | BSD-3-Clause |
| [jsonref](https://pypi.org/project/jsonref/1.1.0/) | 1.1.0 | MIT |
| [jsonschema](https://pypi.org/project/jsonschema/4.26.0/) | 4.26.0 | MIT |
| [jsonschema-path](https://pypi.org/project/jsonschema-path/0.5.0/) | 0.5.0 | Apache-2.0 |
| [jsonschema-specifications](https://pypi.org/project/jsonschema-specifications/2025.9.1/) | 2025.9.1 | MIT |
| [justext](https://pypi.org/project/justext/3.0.2/) | 3.0.2 | BSD-2-Clause |
| [keyring](https://pypi.org/project/keyring/25.7.0/) | 25.7.0 | MIT |
| [lxml](https://pypi.org/project/lxml/6.1.3/) | 6.1.3 | BSD-3-Clause |
| [lxml-html-clean](https://pypi.org/project/lxml-html-clean/0.4.5/) | 0.4.5 | BSD-3-Clause |
| [markdown-it-py](https://pypi.org/project/markdown-it-py/4.2.0/) | 4.2.0 | MIT |
| [mcp](https://pypi.org/project/mcp/2.3.0/) | 2.3.0 | MIT |
| [mcp-types](https://pypi.org/project/mcp-types/2.3.0/) | 2.3.0 | MIT |
| [mdurl](https://pypi.org/project/mdurl/0.1.2/) | 0.1.2 | MIT |
| [more-itertools](https://pypi.org/project/more-itertools/11.2.1/) | 11.2.1 | MIT |
| [openapi-pydantic](https://pypi.org/project/openapi-pydantic/0.6.0/) | 0.6.0 | MIT |
| [opentelemetry-api](https://pypi.org/project/opentelemetry-api/1.45.1/) | 1.45.1 | Apache-2.0 |
| [orjson](https://pypi.org/project/orjson/3.13.0/) | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) |
| [packaging](https://pypi.org/project/packaging/26.3/) | 26.3 | Apache-2.0 OR BSD-2-Clause |
| [pathable](https://pypi.org/project/pathable/0.6.0/) | 0.6.0 | Apache-2.0 |
| [platformdirs](https://pypi.org/project/platformdirs/4.13.0/) | 4.13.0 | MIT |
| [primp](https://pypi.org/project/primp/2.0.1/) | 2.0.1 | MIT |
| [py-key-value-aio](https://pypi.org/project/py-key-value-aio/0.4.6/) | 0.4.6 | Apache-2.0 |
| [pycparser](https://pypi.org/project/pycparser/3.11/) | 3.11 | BSD-3-Clause |
| [pydantic](https://pypi.org/project/pydantic/2.14.1/) | 2.14.1 | MIT |
| [pydantic-core](https://pypi.org/project/pydantic-core/2.50.1/) | 2.50.1 | MIT |
| [pydantic-settings](https://pypi.org/project/pydantic-settings/2.16.0/) | 2.16.0 | MIT |
| [pygments](https://pypi.org/project/pygments/2.21.0/) | 2.21.0 | BSD-2-Clause |
| [pyjwt](https://pypi.org/project/pyjwt/2.15.1/) | 2.15.1 | MIT |
| [pypdfium2](https://pypi.org/project/pypdfium2/5.14.0/) | 5.14.0 | Apache-2.0 / BSD-3-Clause; PDFium and bundled dependencies retain their licenses |
| [pyperclip](https://pypi.org/project/pyperclip/1.11.0/) | 1.11.0 | BSD-3-Clause |
| [python-dateutil](https://pypi.org/project/python-dateutil/2.9.0.post0/) | 2.9.0.post0 | Apache-2.0 OR BSD-3-Clause |
| [python-dotenv](https://pypi.org/project/python-dotenv/1.2.4/) | 1.2.4 | BSD-3-Clause |
| [python-multipart](https://pypi.org/project/python-multipart/0.0.32/) | 0.0.32 | Apache-2.0 |
| [pytz](https://pypi.org/project/pytz/2026.5/) | 2026.5 | MIT |
| [pywin32](https://pypi.org/project/pywin32/312/) | 312 | PSF-2.0 |
| [pywin32-ctypes](https://pypi.org/project/pywin32-ctypes/0.2.3/) | 0.2.3 | BSD-3-Clause |
| [pyyaml](https://pypi.org/project/pyyaml/6.0.3/) | 6.0.3 | MIT |
| [referencing](https://pypi.org/project/referencing/0.37.0/) | 0.37.0 | MIT |
| [regex](https://pypi.org/project/regex/2026.9.29/) | 2026.9.29 | Apache-2.0 AND CNRI-Python |
| [rich](https://pypi.org/project/rich/15.0.0/) | 15.0.0 | MIT |
| [rich-rst](https://pypi.org/project/rich-rst/2.2.0/) | 2.2.0 | MIT |
| [rpds-py](https://pypi.org/project/rpds-py/2026.9.1/) | 2026.9.1 | MIT |
| [scrapling](https://pypi.org/project/scrapling/0.4.15/) | 0.4.15 | BSD-3-Clause |
| [secretstorage](https://pypi.org/project/secretstorage/3.5.0/) | 3.5.0 | BSD-3-Clause |
| [six](https://pypi.org/project/six/1.17.0/) | 1.17.0 | MIT |
| [sse-starlette](https://pypi.org/project/sse-starlette/3.5.0/) | 3.5.0 | BSD-3-Clause |
| [starlette](https://pypi.org/project/starlette/1.8.0/) | 1.8.0 | BSD-3-Clause |
| [tld](https://pypi.org/project/tld/0.13.2/) | 0.13.2 | MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later |
| [trafilatura](https://pypi.org/project/trafilatura/2.3.1/) | 2.3.1 | Apache-2.0 |
| [truststore](https://pypi.org/project/truststore/0.10.5/) | 0.10.5 | MIT |
| [typing-extensions](https://pypi.org/project/typing-extensions/4.16.0/) | 4.16.0 | PSF-2.0 |
| [typing-inspection](https://pypi.org/project/typing-inspection/0.4.4/) | 0.4.4 | MIT |
| [tzdata](https://pypi.org/project/tzdata/2026.5/) | 2026.5 | Apache-2.0 |
| [tzlocal](https://pypi.org/project/tzlocal/5.4.4/) | 5.4.4 | MIT |
| [uncalled-for](https://pypi.org/project/uncalled-for/0.4.1/) | 0.4.1 | MIT |
| [urllib3](https://pypi.org/project/urllib3/2.8.0/) | 2.8.0 | MIT |
| [uvicorn](https://pypi.org/project/uvicorn/0.54.0/) | 0.54.0 | BSD-3-Clause |
| [uvloop](https://pypi.org/project/uvloop/0.23.0/) | 0.23.0 | MIT / Apache-2.0 |
| [w3lib](https://pypi.org/project/w3lib/2.5.0/) | 2.5.0 | BSD-3-Clause |
| [watchfiles](https://pypi.org/project/watchfiles/1.3.0/) | 1.3.0 | MIT |
| [websockets](https://pypi.org/project/websockets/17.2/) | 17.2 | BSD-3-Clause |

## Optional renderer attribution

This product includes software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai).

## Website content

Fetched pages remain third-party content. They are not covered by this project's
MIT license. Historical HTML snapshots are not included in the repository.
