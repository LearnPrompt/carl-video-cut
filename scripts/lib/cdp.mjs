// 零依赖 Chrome 驱动：Node 22+ 自带 WebSocket，直接说 Chrome DevTools 协议，不再需要 playwright-core。
// 给 check_determinism.mjs 和透明叠层渲染用。
//
//   const page = await openPage({ dir, width, height, transparent });
//   await page.seek(t); const png = await page.shot(); await page.close();
//
// - dir：含 index.html 的工程目录，用本地静态服务打开，地址带 ?render=1。
// - transparent：页面默认背景设成全透明，截图带 alpha（页面自己的 body/root 背景也要是透明才有用）。
// - seek(t)：有 window.__render(t) 用它，否则 seek window.__timelines.main（GSAP）。
// - 页面若有 window.__ready（Promise）先等它，再等字体。
// - 调试用环境变量：CVC_CHROME_FLAGS 追加 Chrome 参数；CVC_SETTLE_FRAMES 截图前等几帧（默认 2）。
// - 关掉 LCD 子像素文字：灰度抗锯齿跟合成层无关，hyperframes 模板由此做到逐像素一致。
import http from 'node:http';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { join, extname, resolve, sep } from 'node:path';
import os from 'node:os';

// 非 macOS 或 Chrome 装在别处时用 CVC_CHROME 指过去
export const CHROME = process.env.CVC_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
  '.gif': 'image/gif', '.svg': 'image/svg+xml', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf',
  '.otf': 'font/otf', '.mp4': 'video/mp4', '.wav': 'audio/wav', '.mp3': 'audio/mpeg',
};

export function serve(root) {
  root = resolve(root);
  const server = http.createServer(async (req, res) => {
    try {
      const p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
      const f = resolve(root, '.' + (p === '/' ? '/index.html' : p));
      if (f !== root && !f.startsWith(root + sep)) throw 0;
      const body = await readFile(f);
      res.writeHead(200, { 'content-type': TYPES[extname(f).toLowerCase()] || 'application/octet-stream' });
      res.end(body);
    } catch { res.writeHead(404); res.end(); }
  }).listen(0);
  return { server, port: () => server.address().port };
}

async function launchChrome(chrome, width, height) {
  if (!existsSync(chrome)) throw new Error(`找不到 Chrome：${chrome}`);
  const prof = await mkdtemp(join(os.tmpdir(), 'cvc-chrome-'));
  const proc = spawn(chrome, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${prof}`, '--no-first-run',
    '--no-default-browser-check', '--hide-scrollbars', '--mute-audio', '--force-color-profile=srgb', '--font-render-hinting=none', '--disable-lcd-text',
    '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows',
    `--window-size=${width},${height}`, ...(process.env.CVC_CHROME_FLAGS || '').split(' ').filter(Boolean), 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsUrl = await new Promise((ok, bad) => {
    let buf = '';
    const t = setTimeout(() => bad(new Error('Chrome 10 秒内没给出调试地址')), 10000);
    proc.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\/\/\S+)/); if (m) { clearTimeout(t); ok(m[1]); } });
    proc.on('exit', c => { clearTimeout(t); bad(new Error(`Chrome 退出 ${c}\n${buf.slice(-500)}`)); });
  });
  return { proc, prof, wsUrl };
}

function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0; const pending = new Map(); const listeners = [];
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const { ok, bad } = pending.get(m.id); pending.delete(m.id); m.error ? bad(new Error(`${m.error.message}`)) : ok(m.result); }
    else if (m.method) for (const f of listeners) f(m);
  };
  const send = (method, params = {}, sessionId) => new Promise((ok, bad) => {
    const mid = ++id; pending.set(mid, { ok, bad });
    ws.send(JSON.stringify({ id: mid, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const opened = new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = () => bad(new Error('连不上 Chrome 调试端口')); });
  return { ws, send, on: f => listeners.push(f), opened };
}

export async function openPage({ dir, width = 1920, height = 1080, transparent = false, chrome = CHROME, query = 'render=1' }) {
  const st = serve(dir);
  const { proc, prof, wsUrl } = await launchChrome(chrome, width, height);
  const c = connect(wsUrl); await c.opened;
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  const S = (m, p) => c.send(m, p, sessionId);
  const errors = [];
  c.on(m => {
    if (m.sessionId !== sessionId) return;
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.exception?.description || m.params.exceptionDetails?.text);
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push(m.params.args.map(a => a.value ?? a.description).join(' '));
  });
  await S('Page.enable'); await S('Runtime.enable');
  await S('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
  if (transparent) await S('Emulation.setDefaultBackgroundColorOverride', { color: { r: 0, g: 0, b: 0, a: 0 } });
  const loaded = new Promise(ok => c.on(m => { if (m.sessionId === sessionId && m.method === 'Page.loadEventFired') ok(); }));
  await S('Page.navigate', { url: `http://localhost:${st.port()}/?${query}` });
  await loaded;
  const evaluate = async (expression) => {
    const r = await S('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  await evaluate('Promise.resolve(window.__ready).then(() => document.fonts && document.fonts.ready).then(() => true)');
  const mode = await evaluate(`typeof window.__render === 'function' ? 'render' : (window.__timelines && window.__timelines.main ? 'gsap' : 'none')`);
  if (mode === 'none') throw new Error('页面既没有 window.__render(t)，也没有 window.__timelines.main');
  const duration = await evaluate('window.MOTION_TOTAL ?? window.__duration ?? window.__DUR ?? null');
  return {
    mode, duration, errors, evaluate,
    seek: t => evaluate(mode === 'render' ? `(window.__render(${t}), true)` : `(() => { const tl = window.__timelines.main; tl.pause(); tl.time(${t}, false); return true; })()`),
    // 截图前等两帧，保证样式和合成已落到画面上
    shot: async () => {
      await evaluate(`new Promise(r => { let n = ${+(process.env.CVC_SETTLE_FRAMES || 2)}; const f = () => (--n > 0 ? requestAnimationFrame(f) : r(true)); requestAnimationFrame(f); })`);
      const { data } = await S('Page.captureScreenshot', { format: 'png', fromSurface: true });
      return Buffer.from(data, 'base64');
    },
    close: async () => { try { await c.send('Browser.close'); } catch {} proc.kill(); st.server.close(); await rm(prof, { recursive: true, force: true }).catch(() => {}); },
  };
}
