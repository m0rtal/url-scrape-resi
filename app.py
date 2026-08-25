from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import logging, os, re, markdownify

log = logging.getLogger("url-scrape-resi")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

PROXY_URL = os.getenv("PROXY_URL", "").strip()
BLOCK_MEDIA = os.getenv("BLOCK_MEDIA", "True").lower() in ("1","true","yes")
DEFAULT_TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "30000"))
DEFAULT_WAIT_AFTER_LOAD = int(os.getenv("WAIT_AFTER_LOAD", "1500"))

app = FastAPI(title="url-scrape-resi", version="1.0.0")

def _build_chromium_args():
    args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
    if BLOCK_MEDIA:
        args.append("--block-media")
    if PROXY_URL:
        args.append(f"--proxy-server={PROXY_URL}")
        log.info("chromium proxy enabled")
    return args

async def _scrape_one(url, wait_after_load, timeout_ms):
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=_build_chromium_args())
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            ignore_https_errors=True,
        )
        page = await ctx.new_page()
        if BLOCK_MEDIA:
            await ctx.route("**/*.{png,jpg,jpeg,webp,gif,svg,ico,woff,woff2,ttf,otf}", lambda r: r.abort())
        try:
            r = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            status = r.status if r else 0
            if wait_after_load > 0:
                await page.wait_for_timeout(wait_after_load)
            html = await page.content()
            title = await page.title()
            final_url = page.url
        finally:
            await ctx.close()
            await browser.close()
    md = markdownify.markdownify(html, heading_style="ATX", bullets="-", strip=["script", "style", "noscript"])
    md = re.sub(r"\\n{3,}", "\\n\\n", md).strip()
    return {"markdown": md, "metadata": {"title": title, "url": final_url, "statusCode": status}}

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
        return JSONResponse(status_code=400, content={"success": False, "error": "invalid JSON"})

    urls = body.get("urls") or ([body.get("url")] if body.get("url") else [])
    if not urls:
        return JSONResponse(status_code=400, content={"success": False, "error": "missing url(s)"})
    formats = body.get("formats", ["markdown"])
    wait_after_load = int(body.get("wait_after_load") or DEFAULT_WAIT_AFTER_LOAD)
    timeout_ms = int(body.get("timeout") or DEFAULT_TIMEOUT_MS)

    results = []
    for url in urls:
        try:
            r = await _scrape_one(url, wait_after_load, timeout_ms)
            item = {}
            if "markdown" in formats:
                item["markdown"] = r["markdown"]
            item["metadata"] = r["metadata"]
            results.append({"success": True, "url": url, "data": item})
        except Exception as e:
            log.exception("scrape %s failed: %s", url, e)
            results.append({"success": False, "url": url, "error": str(e)})

    all_ok = all(r["success"] for r in results)
    return JSONResponse(status_code=200 if all_ok else 207, content={"success": all_ok, "results": results})

@app.get("/")
async def root():
    return await health()
