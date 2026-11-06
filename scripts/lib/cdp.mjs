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
// - 调试用环境变量：CVC_SETTLE_FRAMES 截图前等几帧（默认 2）；CVC_CDP_TIMEOUT 单条调用、页面加载的时限秒数（默认 60）。
// - CVC_CHROME_FLAGS：显式追加 Chrome 参数的兼容入口，默认什么都不加。唯一已知用途：在别的沙箱（比如 Agent 框架的
//   seatbelt）里跑时 Chrome 自带沙箱起不来，确认外层已经隔离、页面是自己的工程后设 CVC_CHROME_FLAGS=--no-sandbox。
//   这会关掉 Chrome 自己的沙箱，别的场合不要用。
// - 有界等待：Chrome 10 秒内没给出调试地址、中途退出、调试连接断开、调用超时，都立刻报错，并且只回收这次启动的 Chrome。
// - 关掉 LCD 子像素文字：灰度抗锯齿跟合成层无关，hyperframes 模板由此做到逐像素一致。
import http from 'node:http';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { existsSync, rmSync } from 'node:fs';
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

const START_MS = 10000, CALL_MS = 1000 * (+process.env.CVC_CDP_TIMEOUT || 60);
// 调用方拿到页面后出错直接 process.exit 时，也把自己启动、还没关的 Chrome 带走
const live = new Set();
process.on('exit', () => { for (const r of live) { r.proc.kill('SIGKILL'); rmSync(r.prof, { recursive: true, force: true }); } });

function within(p, ms, what) {
  let t;
  const late = new Promise((_, bad) => { t = setTimeout(() => bad(new Error(`${what} ${ms / 1000} 秒没有结果`)), ms); });
  return Promise.race([p, late]).finally(() => clearTimeout(t));
}

// 立即返回；run.wsUrl 是有时限的 Promise，Chrome 先退出或 spawn 出错时带原因失败
async function launchChrome(chrome, width, height) {
  if (!existsSync(chrome)) throw new Error(`找不到 Chrome：${chrome}`);
  const prof = await mkdtemp(join(os.tmpdir(), 'cvc-chrome-'));
  const proc = spawn(chrome, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${prof}`, '--no-first-run',
    '--no-default-browser-check', '--hide-scrollbars', '--mute-audio', '--force-color-profile=srgb', '--font-render-hinting=none', '--disable-lcd-text',
    '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows',
    `--window-size=${width},${height}`, ...(process.env.CVC_CHROME_FLAGS || '').split(' ').filter(Boolean), 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const run = { proc, prof, tail: '' }; live.add(run);
  run.exited = new Promise(ok => {
    proc.on('exit', (code, sig) => ok(`Chrome 退出（${sig || `退出码 ${code}`}）`));
    proc.on('error', e => ok(`Chrome 起不来：${e.message}`));
  });
  const ready = new Promise(ok => proc.stderr.on('data', d => {
    run.tail = (run.tail + d).slice(-8000);
    const m = run.tail.match(/DevTools listening on (ws:\/\/\S+)/); if (m) ok(m[1]);
  }));
  run.wsUrl = within(Promise.race([ready, run.exited.then(why => { throw new Error(why); })]), START_MS, 'Chrome 启动');
  run.wsUrl.catch(() => {});
  return run;
}

// 只回收这次 spawn 的 Chrome：SIGTERM，2 秒不退再 SIGKILL；然后删临时 profile
async function stop(run) {
  const { proc, prof, exited } = run;
  if (proc.pid && proc.exitCode === null && proc.signalCode === null) {
    proc.kill();
    await within(exited, 2000, 'Chrome 退出').catch(() => proc.kill('SIGKILL'));
  }
  await rm(prof, { recursive: true, force: true }).catch(() => {});
  live.delete(run);
}

function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0, broken = null, die; const pending = new Map(); const listeners = [];
  const dead = new Promise((_, bad) => { die = bad; }); dead.catch(() => {});
  // Chrome 退出或连接断开：在等的调用和 dead 一起失败
  const fail = err => { if (broken) return; broken = err; die(err); for (const p of pending.values()) p.bad(err); pending.clear(); };
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const { ok, bad } = pending.get(m.id); pending.delete(m.id); m.error ? bad(new Error(`${m.error.message}`)) : ok(m.result); }
    else if (m.method) for (const f of listeners) f(m);
  };
  ws.onclose = () => fail(new Error('Chrome 调试连接断开'));
  const send = (method, params = {}, sessionId) => broken ? Promise.reject(broken) : within(new Promise((ok, bad) => {
    const mid = ++id; pending.set(mid, { ok, bad });
    ws.send(JSON.stringify({ id: mid, method, params, ...(sessionId ? { sessionId } : {}) }));
  }), CALL_MS, method);
  const opened = new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = () => bad(new Error('连不上 Chrome 调试端口')); });
  return { ws, send, on: f => listeners.push(f), opened, dead, fail };
}

// Chrome 这一侧的故障附上路径、最后几行输出和沙箱提示；页面本身的错误原样抛
function diag(e, chrome, tail = '') {
  if (!/Chrome (启动|退出|起不来|调试)|没有结果/.test(e.message)) return e;
  const last = tail.trim().split('\n').slice(-6).join('\n');
  e.message += `\n  Chrome：${chrome}${last ? `\n  Chrome 最后的输出：\n${last}` : ''}` +
    '\n  如果是在别的沙箱里运行，Chrome 自带沙箱可能起不来：确认外层已隔离后显式设 CVC_CHROME_FLAGS=--no-sandbox（见 references/qa.md）';
  return e;
}

export async function openPage({ dir, width = 1920, height = 1080, transparent = false, chrome = CHROME, query = 'render=1' }) {
  const st = serve(dir);
  let run, c;
  const close = async () => {
    if (c) { await within(c.send('Browser.close'), 2000, 'Browser.close').catch(() => {}); c.ws.close(); }
    if (run) await stop(run);
    st.server.close();
  };
  try {
    run = await launchChrome(chrome, width, height);
    c = connect(await run.wsUrl);
    run.exited.then(why => c.fail(new Error(why)));
    await within(Promise.race([c.opened, c.dead]), START_MS, '连接 Chrome 调试端口');
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
    await within(Promise.race([loaded, c.dead]), CALL_MS, '页面加载');
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
      mode, duration, errors, evaluate, close,
      seek: t => evaluate(mode === 'render' ? `(window.__render(${t}), true)` : `(() => { const tl = window.__timelines.main; tl.pause(); tl.time(${t}, false); return true; })()`),
      // 截图前等两帧，保证样式和合成已落到画面上
      shot: async () => {
        await evaluate(`new Promise(r => { let n = ${+(process.env.CVC_SETTLE_FRAMES || 2)}; const f = () => (--n > 0 ? requestAnimationFrame(f) : r(true)); requestAnimationFrame(f); })`);
        const { data } = await S('Page.captureScreenshot', { format: 'png', fromSurface: true });
        return Buffer.from(data, 'base64');
      },
    };
  } catch (e) {
    await close();
    throw diag(e, chrome, run?.tail);
  }
}
