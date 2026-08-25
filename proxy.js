// proxy.js — pure parser for PROXY_URL env into Playwright's proxy config shape.
//
// Playwright accepts only `server` (and optional username/password). Feeding
// the raw URL strips credentials, so without this split the proxy returns
// 407 Proxy Authentication Required and every request times out.
//
// Returns null when the URL is empty or uses an unsupported scheme — caller
// then runs Chromium without proxy.
//
// This is a separate module so tests can import it directly without booting
// chromium or binding the HTTP port.

export function parseProxyConfig(rawUrl) {
  if (!rawUrl) return null;
  let u;
  try { u = new URL(rawUrl); }
  catch { return null; }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  const server = `${u.protocol}//${u.host}`;
  const cfg = { server };
  if (u.username) cfg.username = decodeURIComponent(u.username);
  if (u.password) cfg.password = decodeURIComponent(u.password);
  return cfg;
}