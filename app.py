"""url-scrape-resi — minimal HTTP server (stdlib) + chromium --dump-dom + stdlib HTML→MD."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import logging, os, re, subprocess, asyncio, glob, json as _json

log = logging.getLogger("url-scrape-resi")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(message)s")

PROXY_URL = os.getenv("PROXY_URL", "").strip()
BLOCK_MEDIA = os.getenv("BLOCK_MEDIA", "True").lower() in ("1", "true", "yes")
DEFAULT_TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "30000"))
DEFAULT_WAIT_AFTER_LOAD = int(os.getenv("WAIT_AFTER_LOAD", "1500"))

def _find_chromium():
    import shutil
    # headless_shell is the proper headless binary (chromium returns empty DOM)
    headless_shell_candidates = sorted(glob.glob("/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell"), reverse=True)
    for c in headless_shell_candidates:
        if os.access(c, os.X_OK):
            return c
    candidates = [
        shutil.which("chromium"), shutil.which("chromium-browser"),
        "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/ms-playwright/chromium-1234/chrome-linux/chrome",
        "/ms-playwright/chromium-1187/chrome-linux/chrome",
        "/ms-playwright/chromium-1180/chrome-linux/chrome",
        "/ms-playwright/chromium-1179/chrome-linux/chrome",
        "/ms-playwright/chromium-1175/chrome-linux/chrome",
    ]
    for c in candidates:
        if c and os.path.exists(c) and os.access(c, os.X_OK):
            return c
    for c in sorted(glob.glob("/ms-playwright/chromium-*/chrome-linux/chrome"), reverse=True):
        if os.access(c, os.X_OK):
            return c
    return None

CHROMIUM_BIN = _find_chromium()
log.info(f"chromium bin: {CHROMIUM_BIN}")

def _chrome_args(wait_ms, total_ms):
    args = [
        CHROMIUM_BIN,
        "--no-sandbox",
        "--headless",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--no-sandbox",
        f"--user-data-dir=/tmp/chrome-profile-{os.getpid()}",
        "--no-zygote",
    ]
    if BLOCK_MEDIA:
        args.append("--block-media")
    if PROXY_URL:
        args.append(f"--proxy-server={PROXY_URL}")
        log.info("chromium proxy enabled")
    args.append(f"--timeout={max(total_ms // 1000, 30)}")
    args.append("--dump-dom")
    return args

async def _scrape_one(url, wait_after_load, timeout_ms):
    if not CHROMIUM_BIN:
        return None, "chromium binary not found in image"
    total_ms = timeout_ms + wait_after_load
    args = _chrome_args(wait_after_load, total_ms) + [url]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=total_ms / 1000 + 30
        )
    except asyncio.TimeoutError:
        proc.kill()
        return None, "chrome timeout"
    if proc.returncode != 0:
        return None, f"chrome exit {proc.returncode}: {stderr.decode()[:200]}"
    return stdout.decode("utf-8", errors="replace"), None

def html_to_md(html):
    try:
        import markdownify
        md = markdownify.markdownify(html, heading_style="ATX", bullets="-", strip=["script", "style", "noscript"])
    except Exception:
        md = re.sub(r"<[^>]+>", "", html)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    title_m = re.search(r"<title>([^<]*)</title>", html)
    return md, (title_m.group(1) if title_m else "")

async def do_scrape(urls, formats, wait_after_load, timeout_ms):
    results = []
    for url in urls:
        try:
            html, err = await _scrape_one(url, wait_after_load, timeout_ms)
            if err:
                results.append({"success": False, "url": url, "error": err})
                continue
            md, title = html_to_md(html)
            item = {}
            if "markdown" in formats:
                item["markdown"] = md
            item["metadata"] = {"title": title, "url": url, "statusCode": 200}
            results.append({"success": True, "url": url, "data": item})
        except Exception as e:
            log.exception("scrape %s failed: %s", url, e)
            results.append({"success": False, "url": url, "error": str(e)})
    return results

from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, c, b, t="application/json"):
        d = b.encode() if isinstance(b, str) else b
        self.send_response(c); self.send_header("Content-Type", t); self.send_header("Content-Length", str(len(d))); self.end_headers(); self.wfile.write(d)
    def _json(self, c, o): self._send(c, _json.dumps(o, ensure_ascii=False))
    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok", "proxy_enabled": bool(PROXY_URL), "chromium": CHROMIUM_BIN is not None, "chromium_path": CHROMIUM_BIN or ""})
        elif self.path == "/version":
            self._json(200, {"name": "url-scrape-resi", "version": "1.5.0"})
        elif self.path in ("/", "/yandex"):
            self._json(200, {"status": "ok", "service": "url-scrape-resi"})
        else:
            self._json(404, {"error": "not found"})
    def do_POST(self):
        if self.path not in ("/scrape", "/yandex/scrape", "/v2/scrape", "/v2/batch/scrape"):
            self._json(404, {"error": "not found"}); return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = _json.loads(self.rfile.read(n).decode())
        except Exception:
            self._json(400, {"success": False, "error": "invalid JSON"}); return
        is_single = self.path == "/v2/scrape"
        is_batch = self.path == "/v2/batch/scrape"
        if is_single:
            urls = [body["url"]] if body.get("url") else []
        elif is_batch:
            urls = body.get("urls", [])
        else:
            urls = body.get("urls") or ([body["url"]] if body.get("url") else [])
        if not urls:
            self._json(400, {"success": False, "error": "missing url(s)"}); return
        rf = body.get("formats")
        if rf is None:
            sf = body.get("format")
            rf = [sf] if isinstance(sf, str) else (sf if isinstance(sf, list) else ["markdown"])
        formats = rf or ["markdown"]
        w = int(body.get("wait_after_load") or DEFAULT_WAIT_AFTER_LOAD)
        t = int(body.get("timeout") or DEFAULT_TIMEOUT_MS)
        try:
            loop = asyncio.new_event_loop()
            try:
                results = loop.run_until_complete(do_scrape(urls, formats, w, t))
            finally:
                loop.close()
        except Exception as e:
            log.exception("scrape handler: %s", e)
            self._json(500, {"success": False, "error": str(e)}); return
        all_ok = all(r["success"] for r in results)
        if is_single:
            r0 = results[0] if results else {}
            if r0.get("success"):
                self._json(200, {"success": True, "data": r0["data"], "url": r0["url"]})
            else:
                self._json(500, {"success": False, "error": r0.get("error", "unknown")})
        elif is_batch:
            self._json(200, {"success": True, "data": results, "status": "completed"})
        else:
            self._json(200 if all_ok else 207, {"ok": all_ok, "success": all_ok, "results": results})

def main():
    port = int(os.getenv("PORT", "3003"))
    srv = HTTPServer(("0.0.0.0", port), H)
    log.info(f"url-scrape-resi listening on 0.0.0.0:{port} (proxy={'yes' if PROXY_URL else 'no'})")
    srv.serve_forever()

if __name__ == "__main__":
    main()
