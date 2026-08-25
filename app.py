"""url-scrape-resi — minimal HTTP server (stdlib only) + chromium subprocess.

Endpoints:
  - POST /scrape     - native {url, urls, format, wait_after_load, timeout}
  - POST /v2/scrape  - firecrawl v2 compat
  - POST /v2/batch/scrape
  - GET  /health, /version

Uses system chromium (debian package chromium). Image = python:3.11-slim-bookworm.
"""
import asyncio
import base64
import json as json_lib
import logging
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

log = logging.getLogger("url-scrape-resi")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(message)s")

PROXY_URL = os.getenv("PROXY_URL", "").strip()
BLOCK_MEDIA = os.getenv("BLOCK_MEDIA", "True").lower() in ("1", "true", "yes")
DEFAULT_TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "30000"))
DEFAULT_WAIT_AFTER_LOAD = int(os.getenv("WAIT_AFTER_LOAD", "1500"))
CHROMIUM_BIN = "/usr/bin/chromium"


def chrome_args():
    args = [
        "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--headless=new", "--disable-gpu",
        "--no-first-run", "--no-default-browser-check",
    ]
    if BLOCK_MEDIA:
        args.append("--block-media")
    if PROXY_URL:
        args.append(f"--proxy-server={PROXY_URL}")
        log.info("chromium proxy enabled")
    return args


async def _scrape_one(url, wait_after_load, timeout_ms):
    args = chrome_args() + [
        f"--virtual-time-budget={max(wait_after_load, 100)}",
        f"--timeout={timeout_ms // 1000}",
        "--run-all-compositor-stages-before-draw",
        "--dump-dom", url,
    ]
    proc = await asyncio.create_subprocess_exec(
        CHROMIUM_BIN, *args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    stdout, _ = await asyncio.wait_for(
        proc.communicate(), timeout=timeout_ms / 1000 + 30
    )
    return stdout.decode("utf-8", errors="replace")


def html_to_md(html):
    try:
        import markdownify
        md = markdownify.markdownify(html, heading_style="ATX", bullets="-",
                                      strip=["script", "style", "noscript"])
    except ImportError:
        # Fallback: very rough plain-text
        md = re.sub(r"<[^>]+>", "", html)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    title_m = re.search(r"<title>([^<]*)</title>", html)
    return md, (title_m.group(1) if title_m else "")


async def do_scrape(urls, formats, wait_after_load, timeout_ms):
    results = []
    for url in urls:
        try:
            html = await _scrape_one(url, wait_after_load, timeout_ms)
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # silent

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json_lib.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok", "proxy_enabled": bool(PROXY_URL)})
        elif self.path == "/version":
            self._json(200, {"name": "url-scrape-resi", "version": "1.0.0"})
        elif self.path == "/" or self.path == "/yandex":
            self._json(200, {"status": "ok", "service": "url-scrape-resi"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/scrape", "/yandex/scrape", "/v2/scrape", "/v2/batch/scrape"):
            self._json(404, {"error": "not found"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            body = json_lib.loads(raw.decode("utf-8"))
        except Exception as e:
            self._json(400, {"success": False, "error": "invalid JSON"}); return

        is_v2_single = self.path == "/v2/scrape"
        is_v2_batch = self.path == "/v2/batch/scrape"

        if is_v2_single:
            urls = [body["url"]] if body.get("url") else []
            formats = body.get("formats", ["markdown"])
        elif is_v2_batch:
            urls = body.get("urls", [])
            formats = body.get("formats", ["markdown"])
        else:
            # native /scrape and /yandex/scrape — accept both contracts
            urls = body.get("urls") or ([body["url"]] if body.get("url") else [])
            raw_fmts = body.get("formats")
            if raw_fmts is None:
                sf = body.get("format")
                raw_fmts = [sf] if isinstance(sf, str) else (sf if isinstance(sf, list) else ["markdown"])
            formats = raw_fmts or ["markdown"]

        if not urls:
            self._json(400, {"success": False, "error": "missing url(s)"}); return

        w = int(body.get("wait_after_load") or DEFAULT_WAIT_AFTER_LOAD)
        t = int(body.get("timeout") or DEFAULT_TIMEOUT_MS)

        try:
            loop = asyncio.new_event_loop()
            try:
                results = loop.run_until_complete(do_scrape(urls, formats, w, t))
            finally:
                loop.close()
        except Exception as e:
            log.exception("scrape handler failed: %s", e)
            self._json(500, {"success": False, "error": str(e)}); return

        all_ok = all(r["success"] for r in results)
        if is_v2_single:
            r0 = results[0] if results else {}
            if r0.get("success"):
                self._json(200, {"success": True, "data": r0["data"], "url": r0["url"]})
            else:
                self._json(500, {"success": False, "error": r0.get("error", "unknown")})
        elif is_v2_batch:
            self._json(200, {"success": True, "data": results, "status": "completed"})
        else:
            self._json(200 if all_ok else 207,
                       {"ok": all_ok, "success": all_ok, "results": results})


def main():
    port = int(os.getenv("PORT", "3003"))
    srv = HTTPServer(("0.0.0.0", port), Handler)
    log.info(f"url-scrape-resi listening on 0.0.0.0:{port} (proxy={'yes' if PROXY_URL else 'no'})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
