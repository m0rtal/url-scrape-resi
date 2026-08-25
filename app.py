"""url-scrape-resi: minimalist web scraper using chromium --dump-dom + markdownify.

Endpoints (compatible with both yandex plugin and firecrawl v2 contracts):
  - POST /scrape     - native: {url, urls, formats, wait_after_load, timeout}
  - POST /v2/scrape  - firecrawl v2: {url, formats}
  - POST /v2/batch/scrape - firecrawl v2 batch
  - GET  /health
  - GET  /version
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import logging, os, re, subprocess, asyncio, markdownify

log = logging.getLogger("url-scrape-resi")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

PROXY_URL = os.getenv("PROXY_URL", "").strip()
BLOCK_MEDIA = os.getenv("BLOCK_MEDIA", "True").lower() in ("1", "true", "yes")
DEFAULT_TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "30000"))
DEFAULT_WAIT_AFTER_LOAD = int(os.getenv("WAIT_AFTER_LOAD", "1500"))
CHROMIUM_BIN = "/usr/bin/chromium"

app = FastAPI(title="url-scrape-resi", version="1.0.0")


def _chrome_args():
    args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if BLOCK_MEDIA:
        args.append("--block-media")
    if PROXY_URL:
        args.append(f"--proxy-server={PROXY_URL}")
        log.info("chromium proxy enabled")
    return args


async def _scrape_one(url, wait_after_load, timeout_ms):
    args = _chrome_args() + [
        f"--virtual-time-budget={wait_after_load}",
        f"--timeout={timeout_ms // 1000}",
        "--dump-dom",
        url,
    ]
    proc = await asyncio.create_subprocess_exec(
        CHROMIUM_BIN, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_ms / 1000 + 5
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("chrome timeout")
    return stdout.decode("utf-8", errors="replace")


async def _extract_md(url, wait_after_load, timeout_ms):
    html = await _scrape_one(url, wait_after_load, timeout_ms)
    md = markdownify.markdownify(
        html, heading_style="ATX", bullets="-", strip=["script", "style", "noscript"]
    )
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    title_match = re.search(r"<title>([^<]*)</title>", html)
    title = title_match.group(1) if title_match else ""
    return {
        "markdown": md,
        "metadata": {"title": title, "url": url, "statusCode": 200},
    }


@app.get("/health")
async def health():
    return {"status": "ok", "proxy_enabled": bool(PROXY_URL)}


@app.get("/version")
async def version():
    return {"name": "url-scrape-resi", "version": "1.0.0"}


@app.post("/scrape")
async def scrape(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400, content={"success": False, "error": "invalid JSON"}
        )

    urls = body.get("urls") or ([body.get("url")] if body.get("url") else [])
    if not urls:
        return JSONResponse(
            status_code=400, content={"success": False, "error": "missing url(s)"}
        )

    raw_formats = body.get("formats")
    if raw_formats is None:
        single_fmt = body.get("format")
        raw_formats = (
            [single_fmt]
            if isinstance(single_fmt, str)
            else (single_fmt if isinstance(single_fmt, list) else ["markdown"])
        )
    formats = raw_formats or ["markdown"]
    wait_after_load = int(body.get("wait_after_load") or DEFAULT_WAIT_AFTER_LOAD)
    timeout_ms = int(body.get("timeout") or DEFAULT_TIMEOUT_MS)

    results = []
    for url in urls:
        try:
            r = await _extract_md(url, wait_after_load, timeout_ms)
            item = {}
            if "markdown" in formats:
                item["markdown"] = r["markdown"]
            item["metadata"] = r["metadata"]
            results.append({"success": True, "url": url, "data": item})
        except Exception as e:
            log.exception("scrape %s failed: %s", url, e)
            results.append({"success": False, "url": url, "error": str(e)})

    all_ok = all(r["success"] for r in results)
    return JSONResponse(
        status_code=200 if all_ok else 207,
        content={"ok": all_ok, "success": all_ok, "results": results},
    )


@app.post("/v2/scrape")
async def scrape_v2(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400, content={"success": False, "error": "invalid JSON"}
        )
    url = body.get("url")
    if not url:
        return JSONResponse(
            status_code=400, content={"success": False, "error": "missing url"}
        )
    formats = body.get("formats", ["markdown"])
    wait_after_load = int(body.get("wait_after_load") or DEFAULT_WAIT_AFTER_LOAD)
    timeout_ms = int(body.get("timeout") or DEFAULT_TIMEOUT_MS)
    try:
        r = await _extract_md(url, wait_after_load, timeout_ms)
    except Exception as e:
        log.exception("v2 scrape %s failed: %s", url, e)
        return JSONResponse(
            status_code=500, content={"success": False, "error": str(e)}
        )
    data = {"markdown": r["markdown"]} if "markdown" in formats else {}
    data["metadata"] = r["metadata"]
    return JSONResponse({"success": True, "data": data, "url": url})


@app.post("/v2/batch/scrape")
async def batch_scrape_v2(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400, content={"success": False, "error": "invalid JSON"}
        )
    urls = body.get("urls", [])
    if not urls:
        return JSONResponse(
            status_code=400, content={"success": False, "error": "missing urls"}
        )
    formats = body.get("formats", ["markdown"])
    wait_after_load = int(body.get("wait_after_load") or DEFAULT_WAIT_AFTER_LOAD)
    timeout_ms = int(body.get("timeout") or DEFAULT_TIMEOUT_MS)
    out = []
    for u in urls:
        try:
            r = await _extract_md(u, wait_after_load, timeout_ms)
            data = {"markdown": r["markdown"]} if "markdown" in formats else {}
            data["metadata"] = r["metadata"]
            out.append({"url": u, "success": True, "data": data})
        except Exception as e:
            out.append({"url": u, "success": False, "error": str(e)})
    return JSONResponse({"success": True, "data": out, "status": "completed"})


@app.get("/")
async def root():
    return await health()
