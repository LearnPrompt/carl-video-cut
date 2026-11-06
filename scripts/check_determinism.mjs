#!/usr/bin/env node
// check_determinism.mjs：每一帧只由 t 决定，逐像素验证。
//
//   node check_determinism.mjs --dir <工程> [--n 8] [--times 0.5,2,7.3] [--width 1920] [--height 1080] [--transparent] [--out <目录>]
//
// 两次全新加载页面：
//   第一次按时间顺序直接 seek 到每个时刻截图；
//   第二次打乱顺序，每个时刻之前先 seek 到另一个时刻（t + 总长/2），再回来截图。
// 两张逐像素比较，必须完全一致。两次加载能抓到没带种子的 Math.random、Date.now；打乱和绕路能抓到依赖上一帧状态、
// CSS 动画、计时器、onUpdate 回调。口播片型只对动效层跑（叠层工程本身），不对真人底片跑。
// 判定三档：逐像素一致；栅格噪声（最大通道差 <= 2，Chrome 按历史复用字形栅格造成的文字抗锯齿差一两个色阶，
// 换五组栅格参数、多等 30 帧都不变，和页面内容无关，算通过但单列出来）；真不同（失败）。--strict 时任何差异都算失败。
// 有差异时把两张图和放大 8 倍的差异图写进 --out（默认 <工程>/_determinism），失败退出码 1。
import { writeFile, mkdir } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { join, resolve } from 'node:path';
import { openPage } from './lib/cdp.mjs';

const argv = process.argv.slice(2);
const arg = (k, d) => { const i = argv.indexOf('--' + k); return i >= 0 && argv[i + 1] !== undefined ? argv[i + 1] : d; };
if (argv.includes('-h') || argv.includes('--help') || !arg('dir')) {
  console.error('usage: node check_determinism.mjs --dir <工程> [--n 8] [--times 0.5,2] [--width 1920] [--height 1080] [--transparent] [--out dir]');
  process.exit(arg('dir') ? 0 : 2);
}
const DIR = resolve(arg('dir')), W = +arg('width', 1920), H = +arg('height', 1080), N = +arg('n', 8);
const TRANSPARENT = argv.includes('--transparent'), STRICT = argv.includes('--strict');
const OUT = resolve(arg('out', join(DIR, '_determinism')));

const p1 = await openPage({ dir: DIR, width: W, height: H, transparent: TRANSPARENT });
const dur = +(arg('duration') ?? p1.duration);
if (!(dur > 0)) { console.error('无法确定时长：传 --duration 或让页面设置 window.MOTION_TOTAL / window.__duration'); await p1.close(); process.exit(2); }
// 默认取 N 个不落在整秒上的时刻（整秒常是幕切点，避开边界巧合），再加首尾附近各一个
const times = arg('times') ? arg('times').split(',').map(Number)
  : [0.04, ...Array.from({ length: N }, (_, k) => +(((k + 0.37) / N) * dur).toFixed(3)), +(dur - 0.04).toFixed(3)];
const A = new Map();
for (const t of times) { await p1.seek(t); A.set(t, await p1.shot()); }
const mode = p1.mode, errs1 = [...p1.errors];
await p1.close();

const p2 = await openPage({ dir: DIR, width: W, height: H, transparent: TRANSPARENT });
const order = times.map((t, k) => [t, (k * 7919) % times.length]).sort((a, b) => a[1] - b[1]).map(x => x[0]);
const Bm = new Map();
for (const t of order) { await p2.seek(+((t + dur / 2) % dur).toFixed(3)); await p2.shot(); await p2.seek(t); Bm.set(t, await p2.shot()); }
const errs2 = [...p2.errors];
await p2.close();

const raw = png => {
  const r = spawnSync('ffmpeg', ['-v', 'error', '-i', 'pipe:0', '-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1'], { input: png, maxBuffer: 1 << 28 });
  return r.stdout;
};
let bad = 0, noise = 0;
console.log(`工程 ${DIR}\n驱动 ${mode}，时长 ${dur}s，${W}x${H}${TRANSPARENT ? '，透明背景' : ''}，${times.length} 个时刻`);
console.log('t(s)     | 结果 | 不同像素 | 最大通道差');
for (const t of times) {
  const a = A.get(t), b = Bm.get(t);
  if (a.equals(b)) { console.log(`${t.toFixed(3).padEnd(8)} | 一致 | 0        | 0`); continue; }
  const ra = raw(a), rb = raw(b); let px = 0, mx = 0;
  for (let i = 0; i < ra.length; i += 4) {
    const d = Math.max(Math.abs(ra[i] - rb[i]), Math.abs(ra[i + 1] - rb[i + 1]), Math.abs(ra[i + 2] - rb[i + 2]), Math.abs(ra[i + 3] - rb[i + 3]));
    if (d) { px++; if (d > mx) mx = d; }
  }
  if (!px) { console.log(`${t.toFixed(3).padEnd(8)} | 一致 | 0        | 0（PNG 编码不同，像素相同）`); continue; }
  const isNoise = mx <= 2 && !STRICT;
  if (isNoise) noise++; else bad++;
  await mkdir(OUT, { recursive: true });
  const fa = join(OUT, `t${t.toFixed(3)}_a.png`), fb = join(OUT, `t${t.toFixed(3)}_b.png`), fd = join(OUT, `t${t.toFixed(3)}_diff.png`);
  await writeFile(fa, a); await writeFile(fb, b);
  spawnSync('ffmpeg', ['-v', 'error', '-y', '-i', fa, '-i', fb, '-filter_complex', '[0:v]format=rgba[x];[1:v]format=rgba[y];[x][y]blend=all_mode=difference,lutrgb=r=val*8:g=val*8:b=val*8', fd]);
  console.log(`${t.toFixed(3).padEnd(8)} | ${isNoise ? '噪声' : '不同'} | ${String(px).padEnd(8)} | ${mx}   → ${fd}`);
}
for (const e of [...new Set([...errs1, ...errs2])]) console.log(`[页面报错] ${e}`);
const same = times.length - bad - noise;
console.log(bad ? `DETERMINISM FAIL：${bad}/${times.length} 个时刻不一致${STRICT ? '（--strict）' : ''}`
  : noise ? `DETERMINISM PASS：${same}/${times.length} 个时刻逐像素一致，${noise} 个只有栅格噪声（最大差 <= 2 色阶）`
  : `DETERMINISM PASS：${times.length}/${times.length} 个时刻逐像素一致`);
process.exit(bad ? 1 : 0);
