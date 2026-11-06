#!/usr/bin/env node
// 口播大剪辑工程同步：把 motion.config.js 里按口播锚定的分镜，换算成成片秒数，备好素材，写出 HyperFrames 能渲的 index.html。
//
//   node koubo_motion.mjs --dir <工程>/motion [--only s05,s06] [--from 60 --to 120]
//
// 工程目录里要有：motion.config.js（唯一参数源）、cut/（cut_takes.py 的输出，含 cut.mp4 和 cutmap.json）、faces.json（lib/faces.swift 的输出）。
// 锚点写法（scenes[].from、items[].at、chips[].at、pops[].at）：
//   {line: 9}                 稿子第 9 句开口处（取 cutmap 里第一个标成该句的句子起点）
//   {line: 9, p: '给谁看'}     第 9 句开口之后第一次说出这个词组的第一个字（向后最多找 40 秒，跨稿外句）
//   {t: 12.5}                 成片秒数；任何写法都可以再加 off（秒）微调
// 场景结束 = 下一场开始；最后一场结束 = 成片结束 + meta.tail。素材条目没写 at 时，在场内平分。
// 输出：timing.js（window.MOTION_T，场景和条目的成片秒数、每场人脸框）、assets/（统一成 30fps、bt709、无声，按需循环）、index.html。
// 找不到的锚点会打印出来并退回平分，不会静默猜。
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { execFileSync } from 'node:child_process';

const argv = process.argv.slice(2);
const arg = (k, d) => { const i = argv.indexOf('--' + k); return i >= 0 ? argv[i + 1] : d; };
const DIR = path.resolve(arg('dir', '.'));
const SKILL = path.dirname(path.dirname(new URL(import.meta.url).pathname));
const TEMPLATE = path.join(SKILL, 'templates', 'koubo', 'index.html');

const ctx = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(DIR, 'motion.config.js'), 'utf8'), ctx);
const M = ctx.window.MOTION;
const FPS = M.meta.fps || 30;
const cutDir = path.resolve(DIR, M.source.cut);
const cm = JSON.parse(fs.readFileSync(path.join(cutDir, 'cutmap.json'), 'utf8'));
const faces = JSON.parse(fs.readFileSync(path.resolve(DIR, M.source.faces), 'utf8')).frames.filter(f => f.box);
const ROOT = M.source.assetsRoot.replace(/^~/, process.env.HOME);
const r3 = x => Math.round(x * 1000) / 1000;
const snap = x => Math.round(x * FPS) / FPS;                 // 所有时刻落到帧上

// ---------- 锚点 ----------
const S = cm.sentences;
const stream = [];                                            // 成片逐字流 [字, 起, 句下标]
S.forEach((s, i) => (s.chars || []).forEach(c => stream.push([norm(c[0]), c[1], i])));
function norm(x) { return String(x).toLowerCase().replace(/[\s，。、：；！？“”‘’（）()《》,.:;!?"'·\-—_/|]/g, ''); }
const lineStart = {};
let cursor = 0;
const lines = [...new Set(S.map(s => s.line).filter(l => l != null))].sort((a, b) => a - b);
for (const l of lines) {                                       // 单调：每句取不早于上一句的第一个句子
  const hit = S.find(s => s.line === l && s.t0 >= cursor - 0.01);
  if (hit) { lineStart[l] = hit.t0; cursor = hit.t0; }
}
const misses = [];
function resolve(a, where) {
  if (!a) return null;
  let t = null;
  if (a.t != null) t = a.t;
  else if (a.line != null) {
    let base = lineStart[a.line];
    if (base == null) {                                        // 没说到的句：退到前一句
      for (let l = a.line - 1; l > 0 && base == null; l--) base = lineStart[l];
      if (!a.p) misses.push(`${where}：第 ${a.line} 句没说到，用前一句起点`);
    }
    t = base;
    if (a.p != null && base != null) {
      const key = [...norm(a.p)];
      const i0 = stream.findIndex(c => c[1] >= base - 0.05);
      let found = null;
      for (let i = Math.max(0, i0); i < stream.length && stream[i][1] < base + 40; i++) {
        if (key.every((k, j) => stream[i + j] && stream[i + j][0] === k)) { found = stream[i][1]; break; }
      }
      if (found == null) misses.push(`${where}：第 ${a.line} 句后 40 秒内没听到“${a.p}”，用句子起点`);
      else t = found;
    }
  }
  if (t == null) { misses.push(`${where}：锚点无法解析 ${JSON.stringify(a)}`); return null; }
  return t + (a.off || 0);
}

// ---------- 场景时间 ----------
const total = r3(cm.duration + (M.meta.tail || 0));
const scenes = M.scenes.map((sc, i) => ({ sc, t0: resolve(sc.from, sc.id) }));
scenes.forEach((x, i) => { if (x.t0 == null) x.t0 = i ? scenes[i - 1].t0 + 1 : 0; });
scenes[0].t0 = 0;
for (let i = 1; i < scenes.length; i++) if (scenes[i].t0 <= scenes[i - 1].t0 + 0.4) {
  misses.push(`${scenes[i].sc.id}：起点 ${scenes[i].t0.toFixed(2)} 不晚于上一场，顺延`); scenes[i].t0 = scenes[i - 1].t0 + 0.5;
}
scenes.forEach((x, i) => { x.t0 = snap(x.t0); x.t1 = i + 1 < scenes.length ? snap(scenes[i + 1].t0) : total; });

// ---------- 人脸 ----------
function faceIn(t0, t1) {
  const fs_ = faces.filter(f => f.t >= t0 - 0.25 && f.t <= t1 + 0.25).map(f => f.box);
  const use = fs_.length ? fs_ : faces.map(f => f.box);
  const med = a => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };
  return { x0: r3(Math.min(...use.map(b => b[0]))), y0: r3(Math.min(...use.map(b => b[1]))),
           x1: r3(Math.max(...use.map(b => b[2]))), y1: r3(Math.max(...use.map(b => b[3]))),
           cx: r3(med(use.map(b => (b[0] + b[2]) / 2))), cy: r3(med(use.map(b => (b[1] + b[3]) / 2))) };
}

// ---------- 素材 ----------
const probe = f => JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-show_entries',
  'stream=width,height:format=duration', '-of', 'json', f]).toString());
const need = {};                                              // key → 需要的最大出点
const out = { duration: total, scenes: [], parts: [] };
const contAt = {};                                            // in:'cont' 接着上次用到的位置
for (const x of scenes) {
  const sc = x.sc, items = sc.items || [];
  const ts = items.map((it, j) => it.at ? resolve(it.at, `${sc.id}.items[${j}]`) : null);
  const n = items.length;
  const its = items.map((it, j) => {
    let a = ts[j] != null ? Math.max(x.t0, Math.min(x.t1 - 0.3, ts[j])) : x.t0 + (x.t1 - x.t0) * j / n;
    if (j === 0) a = x.t0;
    return { ...it, t0: snap(a) };
  });
  its.forEach((it, j) => { it.t1 = j + 1 < its.length ? its[j + 1].t0 : x.t1; });
  if (sc.layout === 'grid') its.forEach(it => { it.t0 = x.t0; it.t1 = x.t1; });   // 宫格：所有条目整场同时在
  for (const it of its) {
    const md = M.media[it.m];
    if (!md) { misses.push(`${sc.id}：素材键 ${it.m} 不在 media 里`); continue; }
    const inn = it.in === 'cont' ? (contAt[it.m] || md.in || 0) : (it.in != null ? it.in : (md.in || 0));
    it.in = r3(inn); contAt[it.m] = inn + (it.t1 - it.t0);
    if (!md.frame) need[it.m] = Math.max(need[it.m] || 0, inn + (it.t1 - it.t0) + 0.5);
  }
  const chips = (sc.chips || []).map((c, j) => ({ ...c, t: c.at ? snap(resolve(c.at, `${sc.id}.chips[${j}]`) ?? x.t0) : null }));
  const extra = {};
  for (const k of ['marks', 'events', 'pops']) if (sc[k]) extra[k] = sc[k].map((c, j) => ({ ...c, t: c.at ? snap(resolve(c.at, `${sc.id}.${k}[${j}]`) ?? x.t0) : null,
    st: c.stkAt ? snap(resolve(c.stkAt, `${sc.id}.${k}[${j}].stkAt`) ?? x.t0) : null }));   // pops：概念文字动画；stkAt：划线时刻
  if (sc.qat) extra.qat = snap(resolve(sc.qat, `${sc.id}.qat`) ?? x.t0 + 0.5);   // 引用句开始打字的时刻
  out.scenes.push({ id: sc.id, t0: x.t0, t1: x.t1, face: faceIn(x.t0, x.t1), items: its, chips, ...extra });
}
for (const p of M.parts || []) out.parts.push({ t0: snap(resolve(p.from, 'parts') ?? 0), tag: p.tag });
out.parts[0] && (out.parts[0].t0 = 0);

fs.mkdirSync(path.join(DIR, 'assets'), { recursive: true });
const carl = path.join(DIR, 'assets', 'carl.mp4');
if (!fs.existsSync(carl)) {                                   // 硬链接省空间；工程和 cut 不在同一个卷上时链接会失败，退回复制
  try { fs.linkSync(path.join(cutDir, 'cut.mp4'), carl); } catch { fs.copyFileSync(path.join(cutDir, 'cut.mp4'), carl); }
}
const info = {};
for (const [k, md] of Object.entries(M.media)) {
  if (!(k in need) && !md.frame) continue;
  const src = path.join(ROOT, md.src);
  if (!fs.existsSync(src)) { misses.push(`素材 ${k} 找不到文件 ${src}`); continue; }
  const p = probe(src), st = p.streams[0], dur = parseFloat(p.format.duration || '0');
  const img = md.img || /\.(png|jpe?g|webp)$/i.test(src);
  if (md.crop && (md.crop[0] + md.crop[2] > st.width || md.crop[1] + md.crop[3] > st.height)) {   // 越界的 crop 让 ffmpeg 直接崩，先说清楚
    misses.push(`素材 ${k} 的 crop [${md.crop}] 超出原片 ${st.width}x${st.height}，这条先跳过`); continue;
  }
  if (img || md.frame != null) {                               // 静帧：图片原样拷，视频按 frame 秒抽一帧；crop: [x, y, w, h] 先裁掉原图的边
    const dst = path.join(DIR, 'assets', `${k}.png`), stamp = dst + '.json', tag = JSON.stringify({ src, frame: md.frame, crop: md.crop });
    if (!fs.existsSync(dst) || !fs.existsSync(stamp) || fs.readFileSync(stamp, 'utf8') !== tag) {
      const vf = md.crop ? ['-vf', `crop=${md.crop[2]}:${md.crop[3]}:${md.crop[0]}:${md.crop[1]}`] : [];
      if (img && !md.crop) fs.copyFileSync(src, dst);
      else execFileSync('ffmpeg', ['-v', 'error', '-y', ...(md.frame != null ? ['-ss', String(md.frame)] : []), '-i', src, ...vf, '-frames:v', '1', dst]);
      fs.writeFileSync(stamp, tag);
    }
    info[k] = { src: `assets/${k}.png`, w: md.crop ? md.crop[2] : st.width, h: md.crop ? md.crop[3] : st.height, img: true };
    continue;
  }
  // 视频：统一 30fps、bt709、无声。素材不够长时停在最后一帧（tpad clone），不循环回片头；
  // hold: 秒 表示素材只用到这一秒，之后定格（结尾标题卡这类要停住的画面）
  const dst = path.join(DIR, 'assets', `${k}.mp4`);
  const want = need[k], end = md.hold != null ? Math.min(md.hold, dur) : dur, pad = Math.max(0, Math.ceil(want) + 1 - end);
  const stamp = dst + '.json', tag = JSON.stringify({ src, want: Math.ceil(want), end, pad, crop: md.crop });
  if (!fs.existsSync(dst) || !fs.existsSync(stamp) || fs.readFileSync(stamp, 'utf8') !== tag) {
    const crop = md.crop ? `crop=${md.crop[2]}:${md.crop[3]}:${md.crop[0]}:${md.crop[1]},` : '';
    execFileSync('ffmpeg', ['-v', 'error', '-y', '-t', String(end), '-i', src, '-an',
      '-vf', `${crop}scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=${FPS},format=yuv420p` +
        (pad > 0 ? `,tpad=stop_mode=clone:stop_duration=${pad}` : ''),
      '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '17', '-g', String(FPS),
      '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', dst]);
    fs.writeFileSync(stamp, tag);
  }
  if (md.hold == null && want > end + 0.05) misses.push(`素材 ${k} 只有 ${end.toFixed(1)} 秒，用到 ${want.toFixed(1)} 秒，后面停在最后一帧`);
  const q = probe(dst).streams[0];
  info[k] = { src: `assets/${k}.mp4`, w: q.width, h: q.height, dur: r3(end + pad), img: false };
}
out.media = info;

// ---------- 写文件 ----------
fs.writeFileSync(path.join(DIR, 'timing.js'),
  '// 由 koubo_motion.mjs 生成，不手改：改分镜改 motion.config.js 再跑同步。\nwindow.MOTION_T = ' + JSON.stringify(out, null, 1) + ';\n');
const tags = [`<video id="v-carl" class="media" src="assets/carl.mp4" data-start="0" data-duration="${r3(cm.duration)}" data-media-start="0" muted playsinline></video>`];
// data-start 只能往下取整：t0 落在帧的三分之二处（507.36667）时四舍五入成 507.367，比那一帧晚，渲染器那一帧不出画面，
// 换场第一帧就是一帧空卡或黑帧（v2 审片 28.67、507.37 实测）
const f3 = x => Math.floor(x * 1000 + 1e-6) / 1000;
for (const s of out.scenes) s.items.forEach((it, j) => {
  const md = info[it.m]; if (!md || md.img) return;
  const st = f3(it.t0);
  tags.push(`<video id="v-${s.id}-${j}" class="media" src="${md.src}" data-start="${st}" data-duration="${r3(it.t1 - st)}" data-media-start="${it.in}" muted playsinline></video>`);
});
let html = fs.readFileSync(TEMPLATE, 'utf8')
  .replace('<!--MEDIA-->', tags.join('\n  '))
  .replace(/data-duration="TOTAL"/, `data-duration="${total}"`)
  .replace('<title>口播大剪辑</title>', `<title>${M.meta.title}</title>`);
fs.writeFileSync(path.join(DIR, 'index.html'), html);

const rows = out.scenes.map(s => `${s.id.padEnd(5)} ${fmt(s.t0)}–${fmt(s.t1)} ${(s.t1 - s.t0).toFixed(1).padStart(5)}s  ${(M.scenes.find(x => x.id === s.id).layout).padEnd(6)} ${s.items.map(i => i.m).join(',')}`);
function fmt(t) { return `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}`; }
fs.writeFileSync(path.join(DIR, 'timing.txt'), rows.join('\n') + '\n\n锚点问题：\n' + (misses.join('\n') || '无') + '\n');
console.log(JSON.stringify({ scenes: out.scenes.length, videos: tags.length, duration: total, misses: misses.length }));
if (misses.length) console.log(misses.join('\n'));
