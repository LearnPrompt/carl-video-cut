#!/usr/bin/env node
// 通用 self-render 渲染器：本地静态服务 + 无头 Chrome 逐帧调用 window.__render(t) + ffmpeg 管道。
//
// 用法：
//   node render_frames.mjs --dir <含 index.html 的目录> [--fps 30] [--width 1920] [--height 1080]
//        [--duration <秒>] [--out out.mp4] [--stills 0.5,2,5] [--chrome <可执行路径>]
//
// - 时长缺省读页面 window.MOTION_TOTAL，其次 window.__duration，其次 window.__DUR。
// - 页面需暴露 window.__render(t)（纯函数）；没有时回退到 window.__timelines.main（GSAP，t 秒处 seek）。
// - 页面若有 window.__ready（Promise）则先等待（字体/图片就绪）。
// - --stills：只截这些时刻到 <dir>/stills/t<秒>.png，不出视频。
// - playwright-core 从调用者项目（cwd）或 NODE_PATH 解析，不自动安装。
import http from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { join, extname, resolve, sep } from 'node:path';
import { createRequire } from 'node:module';

const argv = process.argv.slice(2);
const arg = (k, d) => { const i = argv.indexOf('--' + k); return i >= 0 && argv[i + 1] !== undefined ? argv[i + 1] : d; };
if (argv.includes('-h') || argv.includes('--help') || !arg('dir')) {
  console.error('usage: node render_frames.mjs --dir <dir> [--fps 30] [--width 1920] [--height 1080] [--duration s] [--out out.mp4] [--stills 0.5,2,5] [--chrome path]');
  process.exit(argv.includes('--dir') ? 0 : 2);
}

// --- 解析 playwright-core：cwd 的 node_modules，其次 NODE_PATH，其次 --dir 的 node_modules ---
const ROOT = resolve(arg('dir'));
if (!existsSync(join(ROOT, 'index.html'))) { console.error(`找不到 ${join(ROOT, 'index.html')}`); process.exit(2); }
let chromium;
for (const base of [process.cwd(), ROOT]) {
  try { ({ chromium } = createRequire(join(base, '_resolve.cjs'))('playwright-core')); break; } catch {}
}
if (!chromium) {
  console.error('找不到 playwright-core。本脚本不会自动安装。\n' +
    '  - 在项目目录里执行：npm i -D playwright-core，再从该目录运行本脚本；\n' +
    '  - 或用 NODE_PATH 指向已有的 node_modules：NODE_PATH=/path/to/node_modules node render_frames.mjs ...');
  process.exit(3);
}

const FPS = +arg('fps', 30);
const WIDTH = +arg('width', 1920);
const HEIGHT = +arg('height', 1080);
const STILLS = arg('stills', '');
const OUT = resolve(arg('out', join(ROOT, 'out.mp4')));
const CHROME = arg('chrome', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');
if (!existsSync(CHROME)) { console.error(`找不到 Chrome：${CHROME}\n用 --chrome 指定可执行路径。`); process.exit(2); }

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
  '.gif': 'image/gif', '.svg': 'image/svg+xml', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf',
  '.otf': 'font/otf', '.mp4': 'video/mp4', '.wav': 'audio/wav', '.mp3': 'audio/mpeg',
};
const server = http.createServer(async (req, res) => {
  try {
    const p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const f = resolve(ROOT, '.' + (p === '/' ? '/index.html' : p));
    if (f !== ROOT && !f.startsWith(ROOT + sep)) throw 0;
    const body = await readFile(f);
    res.writeHead(200, { 'content-type': TYPES[extname(f).toLowerCase()] || 'application/octet-stream' });
    res.end(body);
  } catch { res.writeHead(404); res.end(); }
}).listen(0);
const port = server.address().port;

const browser = await chromium.launch({ executablePath: CHROME, args: ['--force-color-profile=srgb', '--font-render-hinting=none'] });
const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT }, deviceScaleFactor: 1 });
page.on('pageerror', e => console.error('[page error]', e.message));
page.on('console', m => { if (m.type() === 'error') console.error('[console]', m.text()); });
await page.goto(`http://localhost:${port}/?render=1`, { waitUntil: 'load' });
await page.evaluate(() => window.__ready);
await page.evaluate(() => document.fonts && document.fonts.ready);

const hasRender = await page.evaluate(() => typeof window.__render === 'function');
const hasTl = await page.evaluate(() => !!(window.__timelines && window.__timelines.main));
if (!hasRender && !hasTl) { console.error('页面既没有 window.__render(t)，也没有 window.__timelines.main。'); process.exit(4); }
const seek = t => page.evaluate(([t, r]) => {
  if (r) return window.__render(t);
  const tl = window.__timelines.main; tl.pause(); tl.time(t, false);
}, [t, hasRender]);

let DUR = arg('duration') !== undefined ? +arg('duration')
  : await page.evaluate(() => window.MOTION_TOTAL ?? window.__duration ?? window.__DUR ?? null);
if (!(DUR > 0) && !STILLS) { console.error('无法确定时长：请传 --duration，或让页面设置 window.MOTION_TOTAL / window.__duration。'); process.exit(4); }

if (STILLS) {
  await mkdir(join(ROOT, 'stills'), { recursive: true });
  for (const t of STILLS.split(',').map(Number)) {
    await seek(t);
    const f = join(ROOT, 'stills', `t${t.toFixed(2)}.png`);
    await page.screenshot({ path: f });
    console.log(f);
  }
} else {
  const N = Math.round(DUR * FPS);
  await mkdir(resolve(OUT, '..'), { recursive: true });
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'png', '-i', '-',
    '-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '14', '-pix_fmt', 'yuv420p',
    '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', '-color_range', 'tv',
    '-movflags', '+faststart', OUT], { stdio: ['pipe', 'inherit', 'inherit'] });
  ff.stdin.on('error', () => {});
  const t0 = Date.now();
  for (let i = 0; i < N; i++) {
    await seek(i / FPS);
    const buf = await page.screenshot({ type: 'png' });
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
    if (i % FPS === 0) console.log(`frame ${i}/${N}  ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  }
  ff.stdin.end();
  const code = await new Promise(r => ff.on('close', r));
  console.log(code === 0 ? `done ${OUT}  (${N} frames @ ${FPS}fps, ${DUR}s)` : `ffmpeg exit ${code}`);
  if (code !== 0) process.exitCode = 1;
}
await browser.close();
server.close();
