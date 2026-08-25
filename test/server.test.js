// test/server.test.js — uses node:test built-in runner.
// Tests health, version, and a basic local-server scrape (without network proxy).

import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { chromium } from "playwright";

const PORT = 4310;
const BASE = `http://127.0.0.1:${PORT}`;

// Tiny local HTML server to scrape
const localServer = http.createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(`<!doctype html>
<html><head><title>Test Page Title</title></head>
<body><h1>Hello, World!</h1><p>This is a test paragraph with <a href="https://example.com">a link</a>.</p>
<style>body { color: red; }</style>
</body></html>`);
});

// We test the running server by spawning it as a subprocess on a free test port.
// This avoids the global state of importing server.js directly (which binds to PORT 3003).

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER_PATH = path.join(__dirname, "..", "server.js");

function startServer(env = {}) {
  return new Promise((resolve, reject) => {
    const proc = spawn("node", [SERVER_PATH], {
      env: { ...process.env, PORT: String(PORT), ...env },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stderr = "";
    proc.stderr.on("data", (b) => { stderr += b.toString(); });
    const onReady = setTimeout(() => {
      proc.kill("SIGTERM");
      reject(new Error("server didn't start in 8s\n" + stderr));
    }, 8000);
    const tryConnect = (n = 0) => {
      const req = http.get({ hostname: "127.0.0.1", port: PORT, path: "/health", timeout: 1000 }, (res) => {
        clearTimeout(onReady);
        res.resume();
        if (res.statusCode === 200) resolve(proc);
        else reject(new Error("status " + res.statusCode));
      });
      req.on("error", () => {
        if (n > 30) {
          clearTimeout(onReady);
          proc.kill("SIGTERM");
          reject(new Error("server didn't respond after 3s\n" + stderr));
        } else {
          setTimeout(() => tryConnect(n + 1), 100);
        }
      });
    };
    setTimeout(() => tryConnect(), 200);
  });
}

function stopServer(proc) {
  return new Promise((resolve) => {
    proc.once("exit", () => resolve());
    proc.kill("SIGTERM");
    setTimeout(() => proc.kill("SIGKILL"), 2000);
  });
}

function fetchJson(method, path, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request(
      {
        hostname: "127.0.0.1",
        port: PORT,
        path,
        method,
        headers: data ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) } : {},
      },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          try { resolve({ status: res.statusCode, body: JSON.parse(text) }); }
          catch (e) { resolve({ status: res.statusCode, body: text }); }
        });
      }
    );
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

test("GET /health returns ok", async () => {
  const proc = await startServer();
  try {
    const r = await fetchJson("GET", "/health");
    assert.equal(r.status, 200);
    assert.equal(r.body.status, "ok");
    assert.equal(r.body.proxy_enabled, false);
    assert.equal(r.body.chromium, true);
    assert.equal(r.body.version, "3.0.0");
  } finally { await stopServer(proc); }
});

test("GET /version returns name+version", async () => {
  const proc = await startServer();
  try {
    const r = await fetchJson("GET", "/version");
    assert.equal(r.status, 200);
    assert.equal(r.body.name, "url-scrape-resi");
    assert.equal(r.body.version, "3.0.0");
  } finally { await stopServer(proc); }
});

test("GET /unknown returns 404", async () => {
  const proc = await startServer();
  try {
    const r = await fetchJson("GET", "/nope");
    assert.equal(r.status, 404);
    assert.equal(r.body.success, false);
  } finally { await stopServer(proc); }
});

test("POST /scrape rejects missing url", async () => {
  const proc = await startServer();
  try {
    const r = await fetchJson("POST", "/scrape", {});
    assert.equal(r.status, 400);
    assert.match(r.body.error, /missing url/);
  } finally { await stopServer(proc); }
});

test("POST /scrape rejects non-http url", async () => {
  const proc = await startServer();
  try {
    const r = await fetchJson("POST", "/scrape", { url: "ftp://nope" });
    // Multi-status: server returns 207 with results[0].error set.
    assert.equal(r.body.ok, false);
    assert.equal(r.body.results[0].success, false);
    assert.match(r.body.results[0].error, /http/);
  } finally { await stopServer(proc); }
});

test("POST /scrape returns markdown for local server", async () => {
  // Start a local HTTP server to be the scrape target
  await new Promise((r) => localServer.listen(4320, "127.0.0.1", r));
  const proc = await startServer();
  try {
    const r = await fetchJson("POST", "/scrape", { url: "http://127.0.0.1:4320/", wait_after_load: 100 });
    assert.equal(r.status, 200);
    assert.equal(r.body.ok, true);
    assert.equal(r.body.results[0].success, true);
    const md = r.body.results[0].data.markdown;
    assert.match(md, /Hello, World!/);
    assert.match(md, /a link/);
    // Style tag should be stripped
    assert.doesNotMatch(md, /color: red/);
  } finally {
    await stopServer(proc);
    await new Promise((r) => localServer.close(r));
  }
});

test("POST /v2/scrape returns single-result v2 contract", async () => {
  await new Promise((r) => localServer.listen(4321, "127.0.0.1", r));
  const proc = await startServer();
  try {
    const r = await fetchJson("POST", "/v2/scrape", { url: "http://127.0.0.1:4321/", wait_after_load: 100 });
    assert.equal(r.status, 200);
    assert.equal(r.body.success, true);
    assert.equal(r.body.url, "http://127.0.0.1:4321/");
    assert.match(r.body.data.markdown, /Test Page Title/);
  } finally {
    await stopServer(proc);
    await new Promise((r) => localServer.close(r));
  }
});

test("POST /v2/batch/scrape returns array", async () => {
  await new Promise((r) => localServer.listen(4322, "127.0.0.1", r));
  const proc = await startServer();
  try {
    const r = await fetchJson("POST", "/v2/batch/scrape", { urls: ["http://127.0.0.1:4322/", "http://127.0.0.1:4322/"] });
    assert.equal(r.status, 200);
    assert.equal(r.body.status, "completed");
    assert.equal(r.body.data.length, 2);
    assert.equal(r.body.data[0].success, true);
  } finally {
    await stopServer(proc);
    await new Promise((r) => localServer.close(r));
  }
});
