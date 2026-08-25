# url-scrape-resi

Lightweight URL scraper with proxy support and JS rendering via Playwright. Drop-in replacement for firecrawl in self-hosted setups.

## Endpoints

- `GET /health` — `{status, version, proxy_enabled, chromium}`
- `GET /version` — `{name, version}`
- `POST /scrape` — `{url|urls, format|formats, wait_after_load?, timeout?}` → `{ok, success, results: [{success, url, data: {markdown, metadata}}]}`
- `POST /v2/scrape` — single result v2 contract → `{success, data, url}`
- `POST /v2/batch/scrape` — batch v2 contract → `{success, data, status}`

## Stack

- **Base image:** `mcr.microsoft.com/playwright:v1.55.0-jammy` (Node 22 + Chromium pre-installed)
- **Runtime deps:** `playwright@1.55.0` (just one)
- **Memory footprint:** ~150 MB (vs ~1.5 GB for full Python + FastAPI + uvicorn)
- **Code size:** 524 lines total (server.js + tests + Dockerfile + compose)

## Environment

| Var | Default | Description |
|-----|---------|-------------|
| `PORT` | `3003` | HTTP listen port |
| `PROXY_URL` | — | HTTP/SOCKS5 proxy for chromium (e.g. `http://user:pass@host:port`) |
| `CHROMIUM_PATH` | auto | Override chromium binary path (e.g. `/ms-playwright/chromium-1187/chrome-linux/chrome`) |
| `MAX_CONCURRENT` | `2` | Max parallel scrapes per request |
| `WAIT_AFTER_LOAD_MS` | `2000` | Extra wait after `networkidle` (JS-rendered sites, SPAs) |
| `TIMEOUT_MS` | `30000` | Page navigation timeout |
| `LOG_LEVEL` | `info` | Node log level |

## Local development

```bash
npm install
npx playwright install chromium  # one-time, ~100 MB
npm test                         # 8/8 tests
PORT=3003 npm start              # run server
```

## Docker

```bash
docker compose up --build        # builds image + runs container on :3003
docker compose down
```

CI builds the image and pushes to `ghcr.io/m0rtal/url-scrape-resi:3.0.0`. Watchtower or manual Portainer StackUpdate redeploys.

## Why this rewrite (v3.0.0 vs v2.x)

- v2.x used Python + FastAPI + uvicorn with `chromium --dump-dom` subprocess. Returned Chromium error pages instead of real content.
- v3.0.0 uses **Playwright Python API via Node** (`playwright` npm package), which properly waits for `networkidle` and returns real rendered content.
- Single dependency (`playwright`), zero pip install at build, zero uvicorn async headaches.
- Chromium shipped in the base image — no `apt install chromium`, no `playwright install` at runtime when `CHROMIUM_PATH` is set.

## License

MIT.
