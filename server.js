// Sagrada Família online — static page + WebSocket room server.
// Channels hold up to CHANNEL_CAP people each; positions, bows and candles are relayed per channel.
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { WebSocketServer } = require('ws');

const PORT = Number(process.env.PORT) || 3000;
const CHANNEL_CAP = Number(process.env.CHANNEL_CAP) || 50;
const MAX_CHANNELS = Number(process.env.MAX_CHANNELS) || 100;
const CANDLE_TTL_MS = 10 * 60 * 1000;
const CANDLES_PER_CHANNEL = 200;
const DEFAULT_VIDEO = 'Za3A_9Plm5A'; // big screen in the nave plays this until someone changes it
const INDEX = path.join(__dirname, 'public', 'index.html');
const STATIC_RE = /^\/(textures|photos|panos|avatars|models|status)\/[a-z0-9_-]+\.(jpg|png|webp|glb)$/;
const SPOTS = new Set(['nativity', 'passion', 'glory', 'nave', 'crossing', 'altar']); // 360° tour spots
const MIME = { '.jpg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp', '.glb': 'model/gltf-binary' };

// ---------- http ----------
const server = http.createServer((req, res) => {
  const { pathname } = new URL(req.url, 'http://local');
  if (pathname === '/' || pathname === '/index.html') {
    fs.readFile(INDEX, (err, body) => {
      if (err) { res.writeHead(500); res.end('index.html를 읽지 못했습니다'); return; }
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-cache' });
      res.end(body);
    });
  } else if (STATIC_RE.test(pathname)) {
    // textures and reference photos: fixed folders, plain file names only
    const file = path.join(__dirname, 'public', pathname);
    fs.stat(file, (e1, st) => {
      if (e1 || !st.isFile()) { res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' }); res.end('not found'); return; }
      // revalidate every time: a replaced model or texture shows up at once, an unchanged one costs a 304
      const etag = `"${st.size.toString(36)}-${Math.floor(st.mtimeMs).toString(36)}"`;
      const head = { 'content-type': MIME[path.extname(file)], 'cache-control': 'no-cache', etag };
      if (req.headers['if-none-match'] === etag) { res.writeHead(304, head); res.end(); return; }
      fs.readFile(file, (err, body) => {
        if (err) { res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' }); res.end('not found'); return; }
        res.writeHead(200, head); res.end(body);
      });
    });
  } else if (pathname === '/healthz') {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ ok: true, online: wss.clients.size, channels: channels.size }));
  } else {
    res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' }); res.end('not found');
  }
});

// ---------- validation ----------
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const num = (v, lo, hi) => (typeof v === 'number' && Number.isFinite(v) ? clamp(v, lo, hi) : undefined);
const cleanName = (s) => String(s)
  .replace(/[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁠-⁯<>]/g, '')
  .trim().slice(0, 16);
// Only these presence fields are relayed; everything else is dropped.
function cleanPresence(p) {
  const out = {};
  if (!p || typeof p !== 'object') return out;
  for (const k of Object.keys(p)) {
    const v = p[k];
    if (v === null) { out[k] = null; continue; }
    switch (k) {
      case 'n': if (typeof v === 'string') { const n = cleanName(v); if (n) out.n = n; } break;
      case 'c': if (typeof v === 'string' && /^#[0-9a-f]{6}$/i.test(v)) out.c = v; break;
      case 'w': out.w = v === 1 ? 1 : 0; break;
      case 'k': if (Number.isInteger(v) && v >= 0 && v <= 20) out.k = v; break; // character sheet 1..7, 0 = cloak
      case 'x': case 'z': { const n = num(v, -1000, 1000); if (n !== undefined) out[k] = Math.round(n * 100) / 100; break; }
      case 'r': { const n = num(v, -10, 10); if (n !== undefined) out.r = Math.round(n * 100) / 100; break; }
      case 'a': if (v === 'bow' || v === 'dance' || v === 'atk' || v === 'sp' || v === 'tj') out.a = v; break; // gestures, attack, special move
      case 'v': if (Number.isInteger(v) && v >= 0 && v <= 6) out.v = v; break; // ride: 0 on foot, 1 horse, 2 Porsche, 3 AMG, 4 gull-wing, 5 cruiser
      case 's': if (SPOTS.has(v)) out.s = v; break;
      case 't': { const n = num(v, 0, 1e14); if (n !== undefined) out.t = n; break; }
    }
  }
  return out;
}

// ---------- channels ----------
const channels = new Map(); // ch -> { clients:Set<ws>, candles:[{x,z,at}] }
function channelFor(want) {
  const size = (c) => (channels.get(c)?.clients.size || 0);
  if (Number.isInteger(want) && want >= 1 && want <= MAX_CHANNELS && size(want) < CHANNEL_CAP) return want;
  for (let c = 1; c <= MAX_CHANNELS; c++) if (size(c) < CHANNEL_CAP) return c;
  return null;
}
function broadcast(ch, msg, except) {
  const c = channels.get(ch); if (!c) return;
  const data = JSON.stringify(msg);
  for (const ws of c.clients) if (ws !== except && ws.readyState === 1) ws.send(data);
}
// token bucket: ~30 messages/s sustained, burst 60
function allow(ws) {
  const now = Date.now(), b = ws.bucket;
  b.tokens = Math.min(60, b.tokens + (now - b.last) * 0.03); b.last = now;
  if (b.tokens < 1) return false; b.tokens -= 1; return true;
}

const wss = new WebSocketServer({ server, path: '/ws', maxPayload: 4096 });
wss.on('connection', (ws) => {
  ws.id = crypto.randomBytes(6).toString('hex');
  ws.ch = null; ws.p = {}; ws.alive = true; ws.lastCandle = 0; ws.lastScreen = 0; ws.lastChat = 0;
  ws.bucket = { tokens: 60, last: Date.now() };
  ws.on('pong', () => { ws.alive = true; });

  ws.on('message', (raw) => {
    if (!allow(ws)) return;
    let m; try { m = JSON.parse(raw); } catch { return; }
    if (!m || typeof m !== 'object') return;

    if (m.type === 'hello') {
      if (ws.ch !== null) return;
      const ch = channelFor(Number(m.ch));
      if (ch === null) { ws.send(JSON.stringify({ type: 'full' })); ws.close(1013, 'full'); return; }
      if (!channels.has(ch)) channels.set(ch, { clients: new Set(), candles: [], screen: { v: DEFAULT_VIDEO } });
      const c = channels.get(ch);
      ws.ch = ch;
      for (const [k, v] of Object.entries(cleanPresence(m.p))) if (v !== null) ws.p[k] = v;
      const now = Date.now();
      c.candles = c.candles.filter((k) => now - k.at < CANDLE_TTL_MS);
      ws.send(JSON.stringify({
        type: 'welcome', id: ws.id, ch,
        peers: [...c.clients].map((o) => ({ id: o.id, p: o.p })),
        candles: c.candles.map(({ x, z }) => ({ x, z })),
        screen: c.screen,
      }));
      c.clients.add(ws);
      broadcast(ch, { type: 'presence', id: ws.id, p: ws.p }, ws);
      return;
    }
    if (ws.ch === null) return;

    if (m.type === 'presence') {
      for (const [k, v] of Object.entries(cleanPresence(m.p))) { if (v === null) delete ws.p[k]; else ws.p[k] = v; }
      broadcast(ws.ch, { type: 'presence', id: ws.id, p: ws.p }, ws);
    } else if (m.type === 'event' && m.topic === 'candle') {
      const now = Date.now();
      if (now - ws.lastCandle < 1500) return; // one candle per 1.5 s per person
      const x = num(m.data?.x, -1000, 1000), z = num(m.data?.z, -1000, 1000);
      if (x === undefined || z === undefined) return;
      ws.lastCandle = now;
      const c = channels.get(ws.ch);
      c.candles.push({ x, z, at: now });
      if (c.candles.length > CANDLES_PER_CHANNEL) c.candles.shift();
      broadcast(ws.ch, { type: 'event', topic: 'candle', id: ws.id, data: { x, z } }); // includes sender (renders on echo)
    } else if (m.type === 'event' && m.topic === 'chat') {
      const now = Date.now();
      if (now - ws.lastChat < 1000) return; // one line per second per person
      const t = typeof m.data?.t === 'string' ? m.data.t.replace(/[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁠-⁯]/g, '').trim().slice(0, 80) : '';
      if (!t) return;
      ws.lastChat = now;
      // name and colour come from the sender's own presence, never from the chat message
      broadcast(ws.ch, { type: 'event', topic: 'chat', id: ws.id, data: { t, n: ws.p.n || '순례자', c: ws.p.c || '#c9a46a' } });
    } else if (m.type === 'event' && m.topic === 'screen') {
      const now = Date.now();
      if (now - ws.lastScreen < 5000) return; // one change per 5 s per person
      const v = m.data?.v;
      if (typeof v !== 'string' || !/^[A-Za-z0-9_-]{11}$/.test(v)) return; // YouTube video ids only
      ws.lastScreen = now;
      channels.get(ws.ch).screen = { v };
      broadcast(ws.ch, { type: 'event', topic: 'screen', id: ws.id, data: { v } });
    }
  });

  ws.on('close', () => {
    if (ws.ch === null) return;
    const c = channels.get(ws.ch); if (!c) return;
    c.clients.delete(ws);
    broadcast(ws.ch, { type: 'leave', id: ws.id });
    if (c.clients.size === 0 && c.candles.length === 0) channels.delete(ws.ch);
  });
});

// drop dead sockets, expire candles
setInterval(() => {
  for (const ws of wss.clients) { if (!ws.alive) { ws.terminate(); continue; } ws.alive = false; ws.ping(); }
  const now = Date.now();
  for (const [ch, c] of channels) {
    c.candles = c.candles.filter((k) => now - k.at < CANDLE_TTL_MS);
    if (c.clients.size === 0 && c.candles.length === 0) channels.delete(ch);
  }
}, 30000).unref();

server.listen(PORT, () => console.log(`사그라다 파밀리아 온라인: http://localhost:${PORT}`));
