#!/usr/bin/env node
// transcribe.mjs：口播转写对位。能量切块 → whisper-cli 逐块转写（DTW 字级时间）→ 按稿子对齐、纠错、分句。
//
// 用法：
//   node transcribe.mjs --src A=<norm>/A.voice.wav [--src B=...] --script 稿子.txt --out <工程>/timings.json
//        [--model ~/.local/share/whisper.cpp/models/ggml-small.bin] [--trim] [--min-pause 0.08] [--keep-chunks]
//
// 做法：
// 1. 能量检测：10ms 一帧算 dB，底噪取 10% 分位、语音取 90% 分位，门限在两者之间，带回差；起止点再往外找到
//    比底噪高 3dB 的位置，软起音（s、sh）不会被切晚。停顿 >= --min-pause 秒处切块，块长不超过 25 秒。
//    缺省 0.08 秒，切得很碎：whisper 在一块里遇到连说两遍的话常只写一遍（自带去重），块切碎了重复才会分在两块里露出来。
//    实拍口播实测：0.35 秒时漏掉 3 处重说，0.08 秒全部转写出来，总字数多 2.8%。
// 2. 每块单独转写（躲开 whisper 30 秒窗口边界处的塌缩和重复），一次 whisper-cli 调用喂全部块，模型只载一次。
//    DTW 要关 flash attention（-nfa）才生效。DTW 时间整体偏晚约 0.2 秒，每块按首字和能量起点之差扣掉。
//    提示词只放稿子里的英文词（专名），不放整句稿子，免得 whisper 把没说的话照着提示词补出来。
// 3. 对齐：识别出的字和稿子都转成读音形式（12 → 十二，18% → 百分之十八，两=二，英文小写），
//    动态规划允许：一致、替换（听错字）、多说（稿外插话或口头禅）、漏说、跳回或跳到任意句首（重录、跳句）、
//    跳回本句的分句起点（句内磕巴重说，记成 dup 区间，build 时默认剪掉）。
// 4. 分句：跳到句首处断开，句号推进处断开，句首句尾的口头禅、成段的稿外话单独成句。
//    句界优先落在能量检测到的停顿上，没有停顿时落在两字之间能量最低的那一帧。
//
// 输出 timings.json 沿用 tts_yunxi.py 的结构 {源名: {duration, sentences: [{text, start, end}]}}，manim 的 sent(i) 照样能用；
// 每句另有 id（源内第几句，takes 的 sent 就是它）、line（稿子第几句，稿外为 null）、heard（实际听到的）、
// coverage、partial、offscript、dups、chars（读音形式的逐字 [字, 起, 止]，marks 的 phrase 在这里查时刻）。
// 稿子按 。！？ 和换行分句，存在 _script；运行参数存在 _meta。同目录另写 转写报告.md（逐句表 + 识别改正表）。
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const argv = process.argv.slice(2);
const arg = (k, d) => { const i = argv.indexOf('--' + k); return i >= 0 && argv[i + 1] !== undefined ? argv[i + 1] : d; };
const args = (k) => argv.flatMap((v, i) => (v === '--' + k && argv[i + 1] ? [argv[i + 1]] : []));
const USAGE = 'usage: node transcribe.mjs --src A=<voice.wav> [--src B=...] --script 稿子.txt --out timings.json [--model ggml-small.bin] [--trim] [--min-pause 0.08] [--keep-chunks]';
if (argv.includes('-h') || argv.includes('--help')) { console.error(USAGE); process.exit(0); }
const SRCS = args('src').map(s => { const k = s.indexOf('='); return k > 0 ? [s.slice(0, k), s.slice(k + 1)] : [path.basename(s).split('.')[0], s]; });
const SCRIPT = arg('script'); const OUT = arg('out');
if (!SRCS.length || !SCRIPT || !OUT) { console.error(USAGE); process.exit(2); }
const MODEL = arg('model', path.join(os.homedir(), '.local/share/whisper.cpp/models/ggml-small.bin'));
const MIN_PAUSE = +arg('min-pause', 0.08);
const TRIM = argv.includes('--trim');
for (const f of [SCRIPT, MODEL, ...SRCS.map(s => s[1])]) if (!fs.existsSync(f)) { console.error(`找不到文件：${f}`); process.exit(2); }

const SR = 16000, HOP = 160; // 10ms 一帧
const COST = { sub: 1, ins: 0.7, del: 0.9, jump: 2.5, clause: 1.5 };
const FILLER = new Set('嗯啊呃额哦唉诶嘛呢吧'.split(''));
const r3 = x => Math.round(x * 1000) / 1000;

// ---------- 读音形式 ----------
const D = '零一二三四五六七八九';
function sec4(n) { // 0..9999
  const u = ['', '十', '百', '千']; const ds = String(n).split('').reverse(); let out = '', zero = false;
  for (let k = 0; k < ds.length; k++) { const d = +ds[k]; if (!d) { if (out) zero = true; continue; } out = D[d] + u[k] + (zero ? '零' : '') + out; zero = false; }
  return out;
}
function int2cn(s) {
  s = s.replace(/^0+(?=\d)/, '');
  if (s === '0') return '零';
  if (s.length > 8) return [...s].map(d => D[+d]).join('');
  const n = +s, hi = Math.floor(n / 10000), lo = n % 10000;
  let out = hi ? sec4(hi) + '万' + (lo ? (lo < 1000 ? '零' : '') + sec4(lo) : '') : sec4(lo);
  return out.replace(/^一十/, '十');
}
const num2cn = s => { const [i, f] = s.split('.'); return int2cn(i) + (f ? '点' + [...f].map(d => D[+d]).join('') : ''); };
const EQV = { '两': '二', '〇': '零', '幺': '一' };
// 返回 {c: 读音字数组, src: 每个读音字对应原文下标, orig: 显示用原文（一段原文展开成几个读音字时，原文只记在第一个上）}
function canon(str) {
  const s = [...str].map(ch => { const c = ch.codePointAt(0); return c >= 0xFF01 && c <= 0xFF5E ? String.fromCodePoint(c - 0xFEE0) : ch; });
  const flat = s.join(''); const cpAt = new Map(); { let k = 0; s.forEach((ch, i) => { cpAt.set(k, i); k += ch.length; }); }
  const at = off => cpAt.get(off) ?? 0;
  const c = [], src = [], orig = [];
  const re = /(\d{4})(?=年)|(\d+(?:\.\d+)?)%|(\d+(?:\.\d+)?)|([A-Za-z])|(\p{Script=Han})|([\s\S])/gu;
  let m;
  while ((m = re.exec(flat))) {
    const o = at(m.index);
    let first = true;
    const push = t => { for (const ch of t) { c.push(EQV[ch] || ch); src.push(o); orig.push(first ? m[0] : ''); first = false; } };
    if (m[1]) push([...m[1]].map(d => D[+d]).join(''));
    else if (m[2]) push('百分之' + num2cn(m[2]));
    else if (m[3]) push(num2cn(m[3]));
    else if (m[4]) push(m[4].toLowerCase());
    else if (m[5]) push(m[5]);
  }
  return { c, src, orig };
}

// ---------- 稿子 ----------
const scriptText = fs.readFileSync(SCRIPT, 'utf8');
const LINES = (scriptText.match(/[^。！？!?\n]+[。！？!?]?/g) || []).map(s => s.trim()).filter(s => canon(s).c.length);
const S = [], Sline = [], Spos = [], isStart = [], isClause = [];
LINES.forEach((text, li) => {
  const { c, src } = canon(text);
  const clauseAt = new Set(); [...text].forEach((ch, k) => { if ('，,、；;：:'.includes(ch)) clauseAt.add(k + 1); });
  c.forEach((ch, k) => {
    isStart.push(k === 0); isClause.push(k > 0 && src[k] !== src[k - 1] && clauseAt.has(src[k]));
    S.push(ch); Sline.push(li); Spos.push(k);
  });
});
isStart.push(false); isClause.push(false);
const LINE_C = LINES.map(t => canon(t));
const LINE_START = []; Sline.forEach((l, j) => { if (Spos[j] === 0) LINE_START[l] = j; });
const latin = [...new Set(scriptText.match(/[A-Za-z][A-Za-z0-9.+\-]*(?: [A-Z][A-Za-z0-9.+\-]*)*/g) || [])];
const PROMPT = '以下是普通话口播，简体中文。' + (latin.length ? '可能出现：' + latin.join('、') + '。' : '');

// ---------- 音频与能量 ----------
function readPcm(file) {
  const r = spawnSync('ffmpeg', ['-v', 'error', '-i', file, '-ac', '1', '-ar', String(SR), '-f', 's16le', '-'], { maxBuffer: 1 << 30 });
  if (r.status !== 0) { console.error(`读音频失败：${file}\n${r.stderr}`); process.exit(1); }
  const b = r.stdout; const x = new Float32Array(b.length >> 1);
  for (let i = 0; i < x.length; i++) x[i] = b.readInt16LE(i * 2) / 32768;
  return x;
}
function frameDb(x) {
  const n = Math.floor(x.length / HOP), db = new Float32Array(n);
  for (let f = 0; f < n; f++) { let s = 0; for (let i = f * HOP; i < (f + 1) * HOP; i++) s += x[i] * x[i]; db[f] = 10 * Math.log10(s / HOP + 1e-12); }
  return db;
}
const pct = (a, p) => { const s = Float32Array.from(a).sort(); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };
function vad(db) {
  const noise = pct(db, 0.10), speech = pct(db, 0.90);
  const on = noise + Math.max(8, 0.35 * (speech - noise)), off = on - 3, edge = noise + 3;
  const regs = []; let st = -1;
  for (let f = 0; f < db.length; f++) {
    if (st < 0 && db[f] > on) st = f;
    else if (st >= 0 && db[f] < off) { regs.push([st, f]); st = -1; }
  }
  if (st >= 0) regs.push([st, db.length]);
  const merged = [];
  for (const r of regs) { const last = merged[merged.length - 1]; if (last && r[0] - last[1] < 15) last[1] = r[1]; else merged.push([...r]); }
  const out = merged.filter(r => r[1] - r[0] >= 8);
  for (let k = 0; k < out.length; k++) { // 起止点外扩到比底噪高 3dB 处，最多 15 帧，不越过邻段
    const lo = k ? out[k - 1][1] : 0, hi = k + 1 < out.length ? out[k + 1][0] : db.length;
    let a = out[k][0]; while (a > lo && out[k][0] - a < 15 && db[a - 1] > edge) a--;
    let b = out[k][1]; while (b < hi && b - out[k][1] < 15 && db[b] > edge) b++;
    out[k] = [a, b];
  }
  return { regions: out.map(([a, b]) => [a * HOP / SR, b * HOP / SR]), noise, speech, on };
}
function chunkify(regions) {
  const ch = [];
  for (const r of regions) {
    const last = ch[ch.length - 1];
    if (last && r[0] - last.b < MIN_PAUSE && r[1] - last.a <= 25) { last.b = r[1]; last.regs.push(r); }
    else ch.push({ a: r[0], b: r[1], regs: [r] });
  }
  return ch;
}
function writeWav(file, x) {
  const n = x.length, buf = Buffer.alloc(44 + n * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8); buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22); buf.writeUInt32LE(SR, 24); buf.writeUInt32LE(SR * 2, 28);
  buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) buf.writeInt16LE(Math.max(-32768, Math.min(32767, Math.round(x[i] * 32767))), 44 + i * 2);
  fs.writeFileSync(file, buf);
}
const dtwPreset = m => { const b = path.basename(m).replace(/^ggml-|\.bin$/g, ''); return b.replace(/-q\d.*$/, '').replace(/-/g, '.'); };

// ---------- whisper ----------
function whisperRun(files, prompt) {
  const a = ['-m', MODEL, '-l', 'zh', '-dtw', dtwPreset(MODEL), '-nfa', '-ojf', '-np', '-sns', '-t', String(Math.min(8, os.cpus().length))];
  if (prompt) a.push('--prompt', prompt);
  for (const f of files) a.push('-f', f);
  const r = spawnSync('whisper-cli', a, { maxBuffer: 1 << 28, encoding: 'utf8' });
  if (r.status !== 0) { console.error(`whisper-cli 失败：\n${(r.stderr || '').slice(-1500)}`); process.exit(1); }
  return files.map(f => JSON.parse(fs.readFileSync(f + '.json', 'utf8')));
}
// 短块上 whisper 偶尔把提示词原样吐出来当识别结果（夹具实测：1.7 秒的半句被听成以下是普通话口播简体中文），
// 这样的块去掉提示词单独重转一遍；专名提示少一点，总比把真话换成提示词强
const ECHO = /以下是普通话|简体中文|可能出现/;
function whisper(files) {
  const js = whisperRun(files, PROMPT);
  const echo = files.map((f, k) => k).filter(k => ECHO.test(js[k].transcription.map(s => s.text).join('')));
  if (echo.length) {
    const again = whisperRun(echo.map(k => files[k]), null);
    echo.forEach((k, q) => { js[k] = again[q]; });
    console.error(`[info] ${echo.length} 块识别结果是提示词回声，已去掉提示词重转`);
  }
  // whisper 偶尔整段吐繁体（提示词写了简体也会），统一转简体再对齐；转换失败就原样用并提示
  const toks = js.flatMap(j => j.transcription.flatMap(s => s.tokens));
  const hans = spawnSync('swift', [path.join(path.dirname(new URL(import.meta.url).pathname), 'lib', 'hant2hans.swift')],
    { input: toks.map(t => t.text.replace(/\n/g, ' ')).join('\n'), encoding: 'utf8', maxBuffer: 1 << 26 });
  const out = hans.status === 0 ? hans.stdout.split('\n') : [];
  if (out.length === toks.length) toks.forEach((t, k) => { t.text = out[k]; });
  else console.error('[warn] 繁转简失败（需要 macOS 自带的 swift），转写里可能有繁体字');
  return js;
}
// 一块的识别结果 → 原文字串和每个字的时间（秒，相对整段源）
function chunkChars(j, t0, tEnd) {
  const out = [];
  const toks = j.transcription.flatMap(s => s.tokens).filter(t => !/^\[_|^\[/.test(t.text) && t.t_dtw >= 0);
  toks.forEach((t, k) => {
    const chars = [...t.text]; const ta = t.t_dtw / 100;
    const tb = k + 1 < toks.length ? toks[k + 1].t_dtw / 100 : ta + 0.15 * chars.length;
    chars.forEach((ch, i) => out.push({ ch, t: t0 + ta + (tb - ta) * i / chars.length }));
  });
  // 块尾塌缩：末尾连续 3 个以上同一时刻的字，在塌缩点和块的能量终点之间均匀摊开
  let k = out.length - 1; while (k > 0 && Math.abs(out[k].t - out[k - 1].t) < 1e-6) k--;
  const run = out.length - k;
  if (run >= 3) { const ta = out[k].t, tb = Math.max(ta + 0.12 * run, tEnd); for (let q = k; q < out.length; q++) out[q].t = ta + (tb - ta) * (q - k) / run; }
  return out;
}

// ---------- 对齐 ----------
function align(R) {
  const n = R.length, m = S.length, W = m + 1;
  const Dm = new Float32Array((n + 1) * W).fill(Infinity), B = new Uint8Array((n + 1) * W), JF = new Int32Array(n + 1), CL = new Map();
  const eq = (a, b) => a === b;
  for (let j = 0; j <= m; j++) if (isStart[j]) { Dm[j] = 0; B[j] = 4; }
  for (let j = 1; j <= m; j++) { const c = Dm[j - 1] + COST.del; if (c < Dm[j]) { Dm[j] = c; B[j] = 2; } }
  for (let i = 1; i <= n; i++) {
    const r = i * W, p = (i - 1) * W;
    for (let j = 0; j <= m; j++) {
      let best = Dm[p + j] + COST.ins, op = 1;
      if (j > 0) { const c = Dm[p + j - 1] + (eq(R[i - 1].c, S[j - 1]) ? 0 : COST.sub); if (c < best) { best = c; op = 0; } }
      Dm[r + j] = best; B[r + j] = op;
    }
    let bj = 0; for (let j = 1; j <= m; j++) if (Dm[r + j] < Dm[r + bj]) bj = j;
    JF[i] = bj;
    for (let j = 0; j <= m; j++) {
      if (isStart[j]) { const c = Dm[r + bj] + COST.jump; if (c < Dm[r + j]) { Dm[r + j] = c; B[r + j] = 3; } }
    }
    // 句内回到分句起点（磕巴重说）：只能从同一句里更靠后的位置跳回来。回到句首不算这种，算重录（上面的 jump）
    for (let L = 0; L < LINES.length; L++) {
      const a = LINE_START[L], e = L + 1 < LINES.length ? LINE_START[L + 1] : m;
      let bk = e;
      for (let jj = e - 1; jj > a; jj--) {
        if (isClause[jj]) { const c = Dm[r + bk] + COST.clause; if (c < Dm[r + jj]) { Dm[r + jj] = c; B[r + jj] = 5; CL.set(r + jj, bk); } }
        if (Dm[r + jj] < Dm[r + bk]) bk = jj;
      }
    }
    for (let j = 1; j <= m; j++) { const c = Dm[r + j - 1] + COST.del; if (c < Dm[r + j]) { Dm[r + j] = c; B[r + j] = 2; } }
  }
  let j = 0; for (let k = 1; k <= m; k++) if (Dm[n * W + k] < Dm[n * W + j]) j = k;
  // 回溯：每个识别字得到 {op: 'eq'|'sub'|'ins', j}，字间事件 {jump|clause|del}
  const charOp = new Array(n), events = []; let i = n;
  while (i > 0 || B[j] !== 4) {
    const op = B[i * W + j];
    if (op === 4) break;
    if (op === 0) { charOp[i - 1] = { op: R[i - 1].c === S[j - 1] ? 'eq' : 'sub', j: j - 1 }; i--; j--; }
    else if (op === 1) { charOp[i - 1] = { op: 'ins', j }; i--; }
    else if (op === 2) { j--; }
    else if (op === 3) { events.push({ i, type: 'jump', to: j, from: JF[i] }); j = JF[i]; }
    else if (op === 5) { const from = CL.get(i * W + j); events.push({ i, type: 'clause', to: j, from }); j = from; }
    if (i === 0 && B[j] === 4) break;
  }
  events.push({ i: 0, type: 'jump', to: j, from: -1 });
  return { charOp, events: events.reverse() };
}

// ---------- 主流程 ----------
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'koubo-asr-'));
const result = {
  _meta: { model: path.basename(MODEL), dtw: dtwPreset(MODEL), prompt: PROMPT, min_pause: MIN_PAUSE, script: path.resolve(SCRIPT), trim: TRIM },
  _script: LINES.map((text, k) => ({ line: k + 1, text })),
};
const report = ['# 转写报告', '', `模型 ${path.basename(MODEL)}，提示词：${PROMPT}`, ''];

for (const [name, file] of SRCS) {
  const x = readPcm(file), dur = x.length / SR, db = frameDb(x);
  const V = vad(db), chunks = chunkify(V.regions);
  const files = chunks.map((c, k) => {
    const lo = k ? (chunks[k - 1].b + c.a) / 2 : 0, hi = k + 1 < chunks.length ? (c.b + chunks[k + 1].a) / 2 : dur;
    c.wa = Math.max(lo, c.a - 0.2); c.wb = Math.min(hi, c.b + 0.2);
    const f = path.join(work, `${name}_${String(k).padStart(4, '0')}.wav`);
    writeWav(f, x.subarray(Math.floor(c.wa * SR), Math.ceil(c.wb * SR)));
    return f;
  });
  const asr = files.length ? whisper(files) : [];
  // 识别字串（读音形式），带时间、所属块、原文
  const R = [];
  chunks.forEach((c, k) => {
    const cc = chunkChars(asr[k], c.wa, c.b);
    const heardStr = cc.map(o => o.ch).join('');
    c.heard = heardStr.trim();
    const { c: chars, src, orig } = canon(heardStr);
    if (!chars.length) return;
    const flatIdx = []; { let off = 0; cc.forEach((o, i) => { for (let u = 0; u < o.ch.length; u++) flatIdx[off + u] = i; off += o.ch.length; }); }
    const cpIdx = []; { let off = 0; for (const ch of heardStr) { cpIdx.push(off); off += ch.length; } }
    const tOf = cp => cc[flatIdx[cpIdx[cp]]]?.t ?? c.a;
    const bias = Math.min(0.35, Math.max(0, tOf(src[0]) - c.a));
    chars.forEach((ch, q) => R.push({ c: ch, t: Math.max(c.a, tOf(src[q]) - bias), chunk: k, raw: orig[q] }));
  });
  const { charOp, events } = align(R);

  // 平滑：夹在成段多说中间、只对上一两个字的小岛（稿外话里碰巧有稿子里的字）并回多说
  for (let k = 0; k < R.length;) {
    if (charOp[k].op === 'ins') { k++; continue; }
    let e = k; while (e < R.length && charOp[e].op !== 'ins') e++;
    const insL = (() => { let q = k - 1, n = 0; while (q >= 0 && charOp[q].op === 'ins' && R[q].chunk === R[k].chunk) { q--; n++; } return n; })();
    const insR = (() => { let q = e, n = 0; while (q < R.length && charOp[q].op === 'ins' && R[q].chunk === R[e - 1].chunk) { q++; n++; } return n; })();
    if (e - k <= 2 && insL + insR >= 3 && (insL > 0 || k === 0 || R[k - 1].chunk !== R[k].chunk) && (insR > 0 || e === R.length || R[e].chunk !== R[e - 1].chunk))
      for (let q = k; q < e; q++) charOp[q] = { op: 'ins', j: charOp[q].j };
    k = e;
  }
  // 字间断点：跳句 → 新句；句号推进 → 新句；成段稿外话、句首尾口头禅 → 单独成句；分句回跳 → 句内 dup
  const breakBefore = new Set([0]), dupEvents = [];
  for (const e of events) {
    if (e.type === 'clause') dupEvents.push(e);
    if (e.type !== 'jump' || e.i <= 0) continue;
    // 跳转点落在多说的字中间时，顺延到这段多说结束（多说本身的断点由下面的规则给）
    let k = e.i; while (k < R.length && charOp[k].op === 'ins') k++;
    if (k < R.length) breakBefore.add(k);
  }
  const lineOf = k => charOp[k].op === 'ins' ? null : Sline[charOp[k].j];
  // 换到另一句稿子就断；中间夹着多说的字（临场加的"那""然后"）也算，断在上一句最后一个对上的字后面，多说的字归新句
  for (let k = 0, last = null, lastK = -1; k < R.length; k++) {
    const b = lineOf(k);
    if (b === null) continue;
    if (last !== null && b !== last) breakBefore.add(lastK + 1);
    last = b; lastK = k;
  }
  // 成段多说：连续 >=3 个 ins 且贴着块边界，或 >=6 个
  for (let k = 0; k < R.length;) {
    if (charOp[k].op !== 'ins') { k++; continue; }
    let e = k; while (e < R.length && charOp[e].op === 'ins') e++;
    const len = e - k, atEdge = (k === 0 || R[k - 1].chunk !== R[k].chunk) || (e === R.length || R[e].chunk !== R[e - 1].chunk);
    const allFiller = R.slice(k, e).every(o => FILLER.has(o.c));
    if ((len >= 3 && atEdge) || len >= 6 || (allFiller && atEdge)) { breakBefore.add(k); if (e < R.length) breakBefore.add(e); }
    k = e;
  }
  const cuts = [...breakBefore].sort((p, q) => p - q);
  // 断点时刻：两字之间找能量停顿，找不到就取能量最低帧
  const regs = V.regions;
  const boundary = k => {
    const ta = R[k - 1].t, tb = R[k].t;
    const lo = ta + 0.05, hi = tb + 0.15;
    let best = null;
    for (let q = 0; q + 1 < regs.length; q++) { const g0 = regs[q][1], g1 = regs[q + 1][0]; if (g1 > lo && g0 < hi && (!best || g1 - g0 > best[1] - best[0])) best = [g0, g1]; }
    if (best) return best;
    let fb = Math.floor(lo * SR / HOP), fe = Math.max(fb + 1, Math.floor(hi * SR / HOP)), mf = fb;
    for (let f = fb; f < fe && f < db.length; f++) if (db[f] < db[mf]) mf = f;
    const t = mf * HOP / SR; return [t, t];
  };
  const regionOf = t => !regs.length ? [t, t] : regs.find(r => t >= r[0] - 0.2 && t <= r[1] + 0.05) || regs.reduce((b, r) => Math.abs(r[0] - t) < Math.abs(b[0] - t) ? r : b, regs[0]);
  const sents = [];
  cuts.forEach((k0, ci) => {
    const k1 = (ci + 1 < cuts.length ? cuts[ci + 1] : R.length) - 1;
    if (k1 < k0) return;
    const start = k0 === 0 ? regionOf(R[0].t)[0] : boundary(k0)[1];
    const end = k1 === R.length - 1 ? regionOf(R[k1].t)[1] : boundary(k1 + 1)[0];
    const ops = charOp.slice(k0, k1 + 1);
    const lines = ops.filter(o => o.op !== 'ins').map(o => Sline[o.j]);
    const cnt = new Map(); for (const l of lines) cnt.set(l, (cnt.get(l) || 0) + 1);
    const line = lines.length && lines.length >= (k1 - k0 + 1) * 0.4 ? [...cnt].sort((p, q) => q[1] - p[1])[0][0] : null;
    const heard = R.slice(k0, k1 + 1).map(o => o.raw).join('');
    const s = { id: 0, line: line === null ? null : line + 1, text: line === null ? heard : LINES[line], heard, start: r3(start), end: r3(Math.max(end, start + 0.05)) };
    if (line !== null) {
      const L = LINE_C[line].c.length, js = ops.filter(o => o.op !== 'ins' && Sline[o.j] === line).map(o => Spos[o.j]);
      const covered = new Set(js);
      s.coverage = r3(covered.size / L); s.partial = s.coverage < 0.8;
      // 逐字时间按实际说出的字排：对上或听错的字用稿子的写法（纠错），多说的字用听到的，没说的稿子字不进来。
      // 这样逐字流盖满整段录音：剪辑切在两字之间、字幕显示临场加的话都靠它。句内重说的两遍都在，被剪的那遍剪辑时连字一起去掉
      s.chars = ops.map((o, q) => [o.op === 'ins' ? R[k0 + q].c : LINE_C[Sline[o.j]].c[Spos[o.j]],
        r3(R[k0 + q].t), r3(q + 1 < ops.length ? R[k0 + q + 1].t : s.end)]);
      if (s.chars.length) s.chars[0][1] = r3(Math.max(s.chars[0][1], s.start));
      // 句内重说：被放弃的那截从回跳目标第一次被说到的字开始，到重说的第一个字为止（连同中间的停顿一起剪）
      s.dups = dupEvents.filter(e => e.i > k0 && e.i <= k1).map(e => {
        let q = e.i - 1; while (q > k0 && !(charOp[q].op !== 'ins' && charOp[q].j === e.to)) q--;
        return [r3(q > k0 ? boundary(q)[1] : s.start), r3(boundary(e.i)[1])];
      });
    } else {
      s.offscript = true;
      s.chars = R.slice(k0, k1 + 1).map((o, q, arr) => [o.c, r3(o.t), r3(q + 1 < arr.length ? arr[q + 1].t : end)]);
    }
    s._k = [k0, k1];
    sents.push(s);
  });
  // --trim：源头源尾带进来的半截邻句
  let trimmed = [];
  if (TRIM) {
    while (sents.length && sents[0].partial && sents[0].coverage < 0.5) trimmed.push(sents.shift());
    while (sents.length && sents[sents.length - 1].partial && sents[sents.length - 1].coverage < 0.5) trimmed.push(sents.pop());
  }
  sents.forEach((s, k) => { s.id = k + 1; });

  // 识别改正表
  const fixes = [];
  for (const s of sents) {
    if (s.line === null) continue;
    const [k0, k1] = s._k; let k = k0;
    while (k <= k1) {
      if (charOp[k].op === 'eq') { k++; continue; }
      let e = k; while (e <= k1 && charOp[e].op !== 'eq') e++;
      const heardSeg = R.slice(k, e).map(o => o.raw).join('');
      const js = charOp.slice(k, e).filter(o => o.op !== 'ins').map(o => o.j);
      const scriptSeg = js.length ? js.map(j => LINE_C[Sline[j]].orig[Spos[j]]).join('') : '（稿子无此字）';
      if (heardSeg !== scriptSeg) fixes.push({ id: s.id, t: r3(R[k].t), heard: heardSeg, script: scriptSeg });
      k = e;
    }
  }
  for (const s of sents) delete s._k;
  result[name] = { src: name, file: path.resolve(file), duration: r3(dur), vad: { noise_db: r3(V.noise), speech_db: r3(V.speech), on_db: r3(V.on), regions: V.regions.length, chunks: chunks.length }, sentences: sents };
  if (trimmed.length) result[name].trimmed = trimmed.map(s => ({ heard: s.heard, start: s.start, end: s.end }));

  report.push(`## ${name}`, '', `\`${path.resolve(file)}\`，${dur.toFixed(2)}s；底噪 ${V.noise.toFixed(1)} dB，语音 ${V.speech.toFixed(1)} dB，${chunks.length} 块`, '',
    '| 句 | 稿子 | 起 | 止 | 情况 | 听到的 |', '|---|---|---|---|---|---|');
  for (const s of sents) {
    const tag = s.offscript ? '稿外' : s.partial ? `半句 ${Math.round(s.coverage * 100)}%` : (s.dups?.length ? `句内重说 ${s.dups.length} 处` : '完整');
    report.push(`| ${s.id} | ${s.line ?? '—'} | ${s.start.toFixed(2)} | ${s.end.toFixed(2)} | ${tag} | ${s.heard} |`);
  }
  report.push('', '识别改正表（按稿子改）：', '');
  if (fixes.length) { report.push('| 句 | 秒 | 听成 | 改为 |', '|---|---|---|---|'); for (const f of fixes) report.push(`| ${f.id} | ${f.t.toFixed(2)} | ${f.heard} | ${f.script} |`); }
  else report.push('无。');
  report.push('');
  console.error(`[${name}] ${sents.length} 句（稿外 ${sents.filter(s => s.offscript).length}，半句 ${sents.filter(s => s.partial).length}），改正 ${fixes.length} 处`);
}
fs.mkdirSync(path.dirname(path.resolve(OUT)), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(result, null, 1));
const rp = path.join(path.dirname(path.resolve(OUT)), '转写报告.md');
fs.writeFileSync(rp, report.join('\n'));
if (!argv.includes('--keep-chunks')) fs.rmSync(work, { recursive: true, force: true }); else console.error(`chunks: ${work}`);
console.log(path.resolve(OUT));
console.log(rp);
