// url-scrape-resi v3.0.0 — lightweight HTTP scraper via Playwright
// Endpoints: GET /health, GET /version, POST /scrape, POST /v2/scrape, POST /v2/batch/scrape
// All deps baked at build time. Chromium included in mcr.microsoft.com/playwright:v1.55.0-jammy.

import http from "node:http";
import { chromium } from "playwright";
import { parseProxyConfig } from "./proxy.js";

// Tiny built-in HTML -> Markdown converter. Avoids the 3-year-stale
// `markdownify` npm package and keeps the image fully self-contained.
// Supports: headings, paragraphs, lists (ul/ol), code, pre, blockquote,
// links, images, hr, br, bold/italic/code, tables (basic).
function htmlToMarkdown(html) {
  if (!html || typeof html !== "string") return "";
  let s = html;
  // Drop <script> and <style> blocks entirely
  s = s.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
  s = s.replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, "");
  // Drop comments
  s = s.replace(/<!--[\s\S]*?-->/g, "");
  // Extract <title>
  const titleMatch = s.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  const title = titleMatch ? titleMatch[1].trim() : "";
  // Drop head
  s = s.replace(/<head\b[^>]*>[\s\S]*?<\/head>/gi, "");
  s = s.replace(/<title\b[^>]*>[\s\S]*?<\/title>/gi, "");

  // Block-level replacements (process in order: pre, headings, lists, table, blockquote, hr, p, br)
  s = s.replace(/<pre\b[^>]*>([\s\S]*?)<\/pre>/gi, (_, code) => {
    const text = code.replace(/<[^>]+>/g, "");
    return "\n\n```\n" + text.trim() + "\n```\n\n";
  });
  s = s.replace(/<h1\b[^>]*>([\s\S]*?)<\/h1>/gi, "\n\n# $1\n\n");
  s = s.replace(/<h2\b[^>]*>([\s\S]*?)<\/h2>/gi, "\n\n## $1\n\n");
  s = s.replace(/<h3\b[^>]*>([\s\S]*?)<\/h3>/gi, "\n\n### $1\n\n");
  s = s.replace(/<h4\b[^>]*>([\s\S]*?)<\/h4>/gi, "\n\n#### $1\n\n");
  s = s.replace(/<h5\b[^>]*>([\s\S]*?)<\/h5>/gi, "\n\n##### $1\n\n");
  s = s.replace(/<h6\b[^>]*>([\s\S]*?)<\/h6>/gi, "\n\n###### $1\n\n");
  s = s.replace(/<hr\s*\/?>/gi, "\n\n---\n\n");
  s = s.replace(/<br\s*\/?>/gi, "  \n");
  s = s.replace(/<blockquote\b[^>]*>([\s\S]*?)<\/blockquote>/gi, (_, t) => "\n\n" + t.trim().split(/\n+/).map((l) => "> " + l).join("\n") + "\n\n");
  s = s.replace(/<ul\b[^>]*>([\s\S]*?)<\/ul>/gi, (_, items) => {
    const inner = items.replace(/<li\b[^>]*>([\s\S]*?)<\/li>/gi, "\n- $1");
    return "\n\n" + inner.trim() + "\n\n";
  });
  s = s.replace(/<ol\b[^>]*>([\s\S]*?)<\/ol>/gi, (_, items) => {
    let n = 1;
    const inner = items.replace(/<li\b[^>]*>([\s\S]*?)<\/li>/gi, () => `\n${n++}. $1`);
    return "\n\n" + inner.trim() + "\n\n";
  });
  s = s.replace(/<table\b[^>]*>([\s\S]*?)<\/table>/gi, (_, t) => {
    const rows = [...t.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)].map(([, r]) => r);
    if (!rows.length) return "";
    const cells = rows.map((r) =>
      [...r.matchAll(/<t[hd]\b[^>]*>([\s\S]*?)<\/t[hd]>/gi)].map(([, c]) => c.replace(/<[^>]+>/g, "").trim())
    );
    const md = cells.map((row) => "| " + row.join(" | ") + " |").join("\n");
    if (cells.length > 1) {
      const sep = "| " + cells[0].map(() => "---").join(" | ") + " |";
      md.replace("\n", "\n" + sep + "\n", 1);
      return "\n\n" + md.slice(0, md.indexOf("\n", 2)) + sep + md.slice(md.indexOf("\n", 2)) + "\n\n";
    }
    return "\n\n" + md + "\n\n";
  });
  s = s.replace(/<p\b[^>]*>([\s\S]*?)<\/p>/gi, "\n\n$1\n\n");
  s = s.replace(/<div\b[^>]*>([\s\S]*?)<\/div>/gi, "\n$1\n");

  // Inline: links, images, bold/italic, code
  s = s.replace(/<a\b[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi, "[$2]($1)");
  s = s.replace(/<img\b[^>]*src="([^"]+)"[^>]*alt="([^"]*)"[^>]*\/?>/gi, "![$2]($1)");
  s = s.replace(/<img\b[^>]*src="([^"]+)"[^>]*\/?>/gi, "![]($1)");
  s = s.replace(/<(strong|b)\b[^>]*>([\s\S]*?)<\/\1>/gi, "**$2**");
  s = s.replace(/<(em|i)\b[^>]*>([\s\S]*?)<\/\1>/gi, "*$2*");
  s = s.replace(/<code\b[^>]*>([\s\S]*?)<\/code>/gi, "`$1`");

  // Strip remaining tags
  s = s.replace(/<[^>]+>/g, "");

  // Decode common HTML entities
  s = s.replace(/&nbsp;/g, " ")
   .replace(/&amp;/g, "&")
   .replace(/&lt;/g, "<")
   .replace(/&gt;/g, ">")
   .replace(/&quot;/g, '"')
   .replace(/&#39;/g, "'")
   .replace(/&apos;/g, "'");

  // Collapse whitespace
  s = s.replace(/[ \t]+\n/g, "\n")
   .replace(/\n{3,}/g, "\n\n")
   .replace(/[ \t]{2,}/g, " ")
   .trim();

  if (title) s = `# ${title}\n\n` + s;
  return s;
}

const PORT = parseInt(process.env.PORT || "3003", 10);
const PROXY_URL = (process.env.PROXY_URL || "").trim();
const VERSION = "3.0.0";
const MAX_CONCURRENT = parseInt(process.env.MAX_CONCURRENT || "2", 10);
const DEFAULT_WAIT_MS = parseInt(process.env.WAIT_AFTER_LOAD_MS || "2000", 10);
const DEFAULT_TIMEOUT_MS = parseInt(process.env.TIMEOUT_MS || "30000", 10);

let browser = null;

async function getBrowser() {
  if (browser && browser.isConnected()) return browser;
  const launchOpts = {
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
    ],
  };
  // Allow pinning to a pre-installed Chromium (e.g. inside mcr.microsoft.com/playwright
  // image where the binary lives at a known path). Saves the "playwright install"
  // step at runtime.
  if (process.env.CHROMIUM_PATH) launchOpts.executablePath = process.env.CHROMIUM_PATH;
  // parseProxyConfig (proxy.js) splits PROXY_URL into server + username/password
  // so chromium receives Proxy-Authorization headers instead of 407.
  const proxyCfg = parseProxyConfig(PROXY_URL);
  if (proxyCfg) launchOpts.proxy = proxyCfg;
  browser = await chromium.launch(launchOpts);
  return browser;
}

function chromiumAvailable() {
  return !!process.env.PLAYWRIGHT_BROWSERS_PATH || process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
}

async function scrapeOne(rawUrl, { waitMs = DEFAULT_WAIT_MS, timeoutMs = DEFAULT_TIMEOUT_MS, formats = ["markdown"] } = {}) {
  const url = String(rawUrl || "").trim();
  if (!url) return { success: false, error: "missing url" };
  let parsed;
  try { parsed = new URL(url); }
  catch { return { success: false, error: "invalid url" }; }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return { success: false, error: `url must be http(s); got '${parsed.protocol}'` };
  }

  const br = await getBrowser();
  const ctx = await br.newContext({
    userAgent: "Mozilla/5.0 (url-scrape-resi/3.0) Chrome/140",
    ignoreHTTPSErrors: true,
  });
  const page = await ctx.newPage();
  try {
    const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: timeoutMs });
    const httpStatus = resp ? resp.status() : 0;
    // Give JS-rendered pages a moment to settle (Vercel challenge, SPAs)
    await page.waitForLoadState("networkidle", { timeout: Math.min(timeoutMs, 8000) }).catch(() => {});
    if (waitMs > 0) await page.waitForTimeout(waitMs);
    const html = await page.content();
    const title = await page.title();
    const item = { metadata: { title, url, statusCode: httpStatus } };
    if (formats.includes("markdown")) {
      item.markdown = htmlToMarkdown(html);
    } else {
      item.html = html;
    }
    return { success: true, url, data: item };
  } catch (err) {
    return { success: false, url, error: String(err && err.message || err).slice(0, 500) };
  } finally {
    await page.close().catch(() => {});
    await ctx.close().catch(() => {});
  }
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try {
        const raw = Buffer.concat(chunks).toString("utf8");
        resolve(raw ? JSON.parse(raw) : {});
      } catch (e) { reject(e); }
    });
    req.on("error", reject);
  });
}

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

function sendError(res, code, msg) {
  sendJson(res, code, { success: false, error: msg });
}

async function handleScrape(req, res) {
  let body;
  try { body = await readJson(req); }
  catch (e) { return sendError(res, 400, "invalid JSON: " + e.message); }

  const waitMs = parseInt(body.wait_after_load || body.waitAfterLoad || DEFAULT_WAIT_MS, 10);
  const timeoutMs = parseInt(body.timeout || DEFAULT_TIMEOUT_MS, 10);
  let formats = body.formats;
  if (formats == null) {
    const f = body.format;
    formats = Array.isArray(f) ? f : (typeof f === "string" ? [f] : ["markdown"]);
  }
  if (!Array.isArray(formats) || formats.length === 0) formats = ["markdown"];

  let urls;
  if (Array.isArray(body.urls) && body.urls.length) {
    urls = body.urls;
  } else if (typeof body.url === "string" && body.url) {
    urls = [body.url];
  } else {
    return sendError(res, 400, "missing url(s)");
  }

  // Concurrency limit
  const results = [];
  for (let i = 0; i < urls.length; i += MAX_CONCURRENT) {
    const batch = urls.slice(i, i + MAX_CONCURRENT);
    const batchResults = await Promise.all(batch.map((u) => scrapeOne(u, { waitMs, timeoutMs, formats })));
    results.push(...batchResults);
  }

  // v2 contract: { success, data, url }
  if (req.url === "/v2/scrape" && urls.length === 1) {
    const r = results[0];
    if (r.success) return sendJson(res, 200, { success: true, data: r.data, url: r.url });
    return sendJson(res, 500, { success: false, error: r.error });
  }
  // v2 batch
  if (req.url === "/v2/batch/scrape") {
    return sendJson(res, 200, { success: true, data: results, status: "completed" });
  }
  // native
  const allOk = results.every((r) => r.success);
  return sendJson(res, allOk ? 200 : 207, { ok: allOk, success: allOk, results });
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === "GET" && req.url === "/health") {
      return sendJson(res, 200, {
        status: "ok",
        version: VERSION,
        proxy_enabled: !!PROXY_URL,
        chromium: true,
      });
    }
    if (req.method === "GET" && req.url === "/version") {
      return sendJson(res, 200, { name: "url-scrape-resi", version: VERSION });
    }
    if (req.method === "POST" && ["/scrape", "/v2/scrape", "/v2/batch/scrape"].includes(req.url)) {
      return await handleScrape(req, res);
    }
    sendError(res, 404, "not found");
  } catch (err) {
    console.error("unhandled:", err);
    sendError(res, 500, "internal: " + String(err && err.message || err).slice(0, 300));
  }
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`url-scrape-resi v${VERSION} listening on 0.0.0.0:${PORT} proxy=${PROXY_URL ? "yes" : "no"}`);
  // warm up browser in background
  getBrowser().then(() => console.log("chromium ready")).catch((e) => console.error("chromium warmup failed:", e.message));
});

process.on("SIGTERM", () => { browser?.close(); server.close(() => process.exit(0)); });
process.on("SIGINT", () => { browser?.close(); server.close(() => process.exit(0)); });
