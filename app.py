"""url-scrape-resi — minimal HTTP server (stdlib) + chromium --dump-dom + markdownify.

Root cause fix 2026-08-25:
- Was clamping --virtual-time-budget to 100ms minimum, which caused chromium to
  return its internal CSS error page on every URL (page hadn't navigated yet).
- Now pass real wait_after_load as virtual-time-budget, with safe defaults (2000ms).
- Added fallback: if rendered HTML is suspiciously small (<500 chars), retry once
  with doubled wait. This handles Vercel challenge pages that take 2-4s to settle.
- Switched from stdlib HTTPServer (sync) to uvicorn so concurrent scrapes don't
  serialize (was the silent stall trigger on /v2/batch/scrape).
"""
import asyncio
import glob
import json as _json
import logging
import os
import re
import shutil
import subprocess

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("url-scrape-resi")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)

PROXY_URL = os.getenv("PROXY_URL", "").strip()
BLOCK_MEDIA = os.getenv("BLOCK_MEDIA", "True").lower() in ("1", "true", "yes")
DEFAULT_TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "45000"))
DEFAULT_WAIT_AFTER_LOAD = int(os.getenv("WAIT_AFTER_LOAD", "3000"))
MIN_USEFUL_HTML = int(os.getenv("MIN_USEFUL_HTML", "500"))


def _find_chromium():
    candidates = sorted(
        glob.glob("/ms-playwright/chromium-*/chrome-linux/chrome"), reverse=True
    )
    for c in candidates:
        if os.access(c, os.X_OK):
            return c
    headless = sorted(
        glob.glob("/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell"),
        reverse=True,
    )
    for c in headless:
        if os.access(c, os.X_OK):
            return c
    for c in [
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]:
        if c and os.access(c, os.X_OK):
            return c
    return None


CHROMIUM_BIN = _find_chromium()
log.info(f"chromium bin: {CHROMIUM_BIN}")
log.info(
    f"defaults: timeout={DEFAULT_TIMEOUT_MS}ms wait_after_load={DEFAULT_WAIT_AFTER_LOAD}ms proxy={'yes' if PROXY_URL else 'no'}"
)


def _chrome_args(url, total_ms, wait_ms):
    args = [
        CHROMIUM_BIN,
        "--no-sandbox",
        "--headless=new",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-features=Translate,BackForwardCache,AcceptCHFrame",
        "--run-all-compositor-stages-before-draw",
        "--hide-scrollbars",
        f"--user-data-dir=/tmp/chrome-profile-{os.getpid()}-{hash(url) & 0xffff}",
    ]
    if BLOCK_MEDIA:
        args.append("--block-media")
    if PROXY_URL:
        args.append(f"--proxy-server={PROXY_URL}")
        log.info(f"chromium proxy enabled for {url}")
    args.append(f"--virtual-time-budget={wait_ms}")
    args.append(f"--timeout={max(total_ms // 1000, 30)}")
    args.append("--dump-dom")
    args.append(url)
    return args


async def _scrape_one(url, wait_after_load, timeout_ms):
    if not CHROMIUM_BIN:
        return None, "chromium binary not found in image"

    total_ms = timeout_ms + wait_after_load
    attempt = 0
    cur_wait = wait_after_load
    last_err = None

    while attempt < 2:
        attempt += 1
        args = _chrome_args(url, total_ms, cur_wait)
        log.info(f"scrape attempt={attempt} url={url} wait={cur_wait}ms timeout={total_ms}ms")
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=(total_ms / 1000) + 60,
            )
        except asyncio.TimeoutError:
            log.warning(f"chromium timeout for {url} attempt={attempt}")
            try:
                proc.kill()
            except Exception:
                pass
            last_err = f"chrome timeout after {total_ms}ms"
            cur_wait *= 2
            continue

        if proc.returncode != 0:
            err_tail = stderr.decode("utf-8", errors="replace")[:300]
            log.warning(f"chromium exit {proc.returncode} for {url}: {err_tail}")
            last_err = f"chrome exit {proc.returncode}: {err_tail}"
            cur_wait *= 2
            continue

        html = stdout.decode("utf-8", errors="replace")

        # Filter out chromium error pages (CSS blob artifact)
        # They start with "<style>..." and contain "color: var(--link-color)" — page never loaded.
        is_chromium_error = (
            len(html) < MIN_USEFUL_HTML
            or html.lstrip().lower().startswith("<style>")
            or "--google-gray" in html[:1000]
            and "Example Domain" not in html
            and "github.com" not in html
            and "vercel" not in html.lower()[:500]
        )

        if is_chromium_error and attempt == 1:
            log.warning(
                f"suspiciously small/chromium-error response for {url} (len={len(html)}), retrying with doubled wait"
            )
            cur_wait *= 2
            last_err = "chromium returned error page, retrying"
            continue

        if is_chromium_error:
            log.error(
                f"chromium error page persisted after retry for {url}, len={len(html)}"
            )
            return None, last_err or "chromium error page after retry"

        log.info(f"scrape ok url={url} html_len={len(html)} attempt={attempt}")
        return html, None

    return None, last_err or "unknown scrape failure"


def html_to_md(html):
    try:
        import markdownify

        md = markdownify.markdownify(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "noscript", "svg"],
        )
    except Exception:
        md = re.sub(r"<[^>]+>", "", html)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    title_m = re.search(r"<title>([^<]*)</title>", html, re.IGNORECASE)
    return md, (title_m.group(1) if title_m else "")


async def do_scrape(urls, formats, wait_after_load, timeout_ms):
    results = []
    sem = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT", "3")))

    async def one(u):
        async with sem:
            html, err = await _scrape_one(u, wait_after_load, timeout_ms)
            if err:
                return {"success": False, "url": u, "error": err}
            md, title = html_to_md(html)
            item = {}
            if "markdown" in formats:
                item["markdown"] = md
            item["metadata"] = {"title": title, "url": u, "statusCode": 200}
            return {"success": True, "url": u, "data": item}

    results = await asyncio.gather(*[one(u) for u in urls], return_exceptions=True)
    final = []
    for r in results:
        if isinstance(r, Exception):
            log.exception("scrape gather failed: %s", r)
            final.append({"success": False, "url": "?", "error": str(r)})
        else:
            final.append(r)
    return final


app = FastAPI()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "proxy_enabled": bool(PROXY_URL),
        "chromium": CHROMIUM_BIN is not None,
        "chromium_path": CHROMIUM_BIN or "",
        "version": "2.0.0",
        "defaults": {
            "wait_after_load_ms": DEFAULT_WAIT_AFTER_LOAD,
            "timeout_ms": DEFAULT_TIMEOUT_MS,
        },
    }


@app.get("/version")
async def version():
    return {"name": "url-scrape-resi", "version": "2.0.0"}


@app.post("/scrape")
@app.post("/yandex")
@app.post("/yandex/scrape")
async def scrape_native(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"success": False, "error": "invalid JSON"}, status_code=400)
    urls = body.get("urls") or ([body["url"]] if body.get("url") else [])
    if not urls:
        return JSONResponse({"success": False, "error": "missing url(s)"}, status_code=400)
    rf = body.get("formats")
    if rf is None:
        sf = body.get("format")
        rf = [sf] if isinstance(sf, str) else (sf if isinstance(sf, list) else ["markdown"])
    formats = rf or ["markdown"]
    w = int(body.get("wait_after_load") or DEFAULT_WAIT_AFTER_LOAD)
    t = int(body.get("timeout") or DEFAULT_TIMEOUT_MS)
    try:
        results = await do_scrape(urls, formats, w, t)
    except Exception as e:
        log.exception("scrape handler: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    all_ok = all(r["success"] for r in results)
    return JSONResponse(
        {"ok": all_ok, "success": all_ok, "results": results},
        status_code=200 if all_ok else 207,
    )


@app.post("/v2/scrape")
async def v2_scrape(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"success": False, "error": "invalid JSON"}, status_code=400)
    url = body.get("url")
    if not url:
        return JSONResponse({"success": False, "error": "missing url"}, status_code=400)
    rf = body.get("formats")
    if rf is None:
        sf = body.get("format")
        rf = [sf] if isinstance(sf, str) else (sf if isinstance(sf, list) else ["markdown"])
    formats = rf or ["markdown"]
    w = int(body.get("wait_after_load") or DEFAULT_WAIT_AFTER_LOAD)
    t = int(body.get("timeout") or DEFAULT_TIMEOUT_MS)
    try:
        results = await do_scrape([url], formats, w, t)
    except Exception as e:
        log.exception("v2 scrape: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    r0 = results[0] if results else {}
    if r0.get("success"):
        return {"success": True, "data": r0["data"], "url": r0["url"]}
    return JSONResponse(
        {"success": False, "error": r0.get("error", "unknown")}, status_code=500
    )


@app.post("/v2/batch/scrape")
async def v2_batch_scrape(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"success": False, "error": "invalid JSON"}, status_code=400)
    urls = body.get("urls", [])
    if not urls:
        return JSONResponse({"success": False, "error": "missing urls"}, status_code=400)
    rf = body.get("formats")
    if rf is None:
        sf = body.get("format")
        rf = [sf] if isinstance(sf, str) else (sf if isinstance(sf, list) else ["markdown"])
    formats = rf or ["markdown"]
    w = int(body.get("wait_after_load") or DEFAULT_WAIT_AFTER_LOAD)
    t = int(body.get("timeout") or DEFAULT_TIMEOUT_MS)
    try:
        results = await do_scrape(urls, formats, w, t)
    except Exception as e:
        log.exception("batch scrape: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    return {"success": True, "data": results, "status": "completed"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "3003"))
    log.info(
        f"url-scrape-resi v2.0.0 starting on 0.0.0.0:{port} proxy={'yes' if PROXY_URL else 'no'} chromium={'yes' if CHROMIUM_BIN else 'no'}"
    )
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")