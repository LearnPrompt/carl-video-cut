#!/usr/bin/env node
// 人像覆盖回归：先拿合成分镜直接测 scripts/lib/presence.mjs，再造一个最小口播工程跑 koubo_motion.mjs，看 timing.txt 里的人像覆盖。
//   node tests/presence/check_presence.mjs
// 全部通过打印 PRESENCE TESTS PASS、退出码 0；任何一条不对打印 FAIL 那条、退出码 1。不联网，只要 node 和 ffmpeg。
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { presence } from '../../scripts/lib/presence.mjs';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
let bad = 0;
const ok = (cond, name, extra = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${extra ? '  ' + extra : ''}`); if (!cond) bad++; };
const seq = list => { let t = 0; return list.map(([id, layout, d, more]) => { const s = { id, t0: t, t1: t + d, layout, ...more }; t += d; return s; }); };

// 1. 一长串全屏讲素材（参考 2026-11 一次口播片：8 场全屏首尾相接 156 秒）必须报，且只报一条、指明起止场
const longFull = seq([['a', 'talk', 19.2], ['b', 'full', 30.1, { card: true }], ['c', 'full', 15.1], ['d', 'full', 18.5], ['e', 'full', 8.5],
  ['f', 'full', 27.1], ['g', 'full', 8.5], ['h', 'full', 14.4], ['i', 'full', 19.8], ['j', 'full', 13.5], ['k', 'seek', 24.4]]);
let p = presence(longFull);
ok(p.warnings.length === 1 && /^b–j 连续 155\.5 秒不见真人/.test(p.warnings[0]), '长段全屏报一条', p.warnings[0]);
ok(p.longest && p.longest.from === 'b' && p.longest.to === 'j', '最长不见的起止场');

// 2. 同样的时长换成 pip：不报，出镜 100%
p = presence(longFull.map(s => ({ ...s, layout: s.layout === 'full' ? 'pip' : s.layout })));
ok(p.warnings.length === 0 && Math.abs(p.ratio - 1) < 1e-9 && p.longest === null, '换成 pip 不报');

// 3. 上限边界：正好 20 秒不报，20.1 秒报；meta.maxAbsent 可调
ok(presence(seq([['a', 'talk', 5], ['b', 'full', 20], ['c', 'pip', 5]])).warnings.length === 0, '正好 20 秒不报');
ok(presence(seq([['a', 'talk', 5], ['b', 'full', 20.1], ['c', 'pip', 5]])).warnings.length === 1, '20.1 秒报');
ok(presence(seq([['a', 'full', 16], ['b', 'talk', 5]]), { maxAbsent: 15 }).warnings.length === 1, 'maxAbsent 15 时 16 秒报');

// 4. 宫格也算不见真人，和相邻全屏连成一段；比例按时长算
p = presence(seq([['a', 'talk', 30], ['b', 'full', 6], ['c', 'grid', 4]]));
ok(Math.abs(p.ratio - 0.75) < 1e-9 && p.longest.from === 'b' && p.longest.to === 'c' && p.warnings.length === 0, '宫格并入不见段，出镜 75%');

// 5. 脸旁大字：talk、full 加 card、style card 不报；pip、side、split、full 不加 card 报
const pop = [{ text: '不出现\n文字和字幕' }];
const pops = layout => presence([{ id: 'x', t0: 0, t1: 10, layout, pops: pop }]).warnings.length;
ok(pops('talk') === 0, 'talk 的脸旁大字不报');
ok(presence([{ id: 'x', t0: 0, t1: 10, layout: 'full', card: true, pops: pop }]).warnings.length === 0, 'full 加 card 的脸旁大字不报');
ok(presence([{ id: 'x', t0: 0, t1: 10, layout: 'pip', pops: [{ text: '章节', style: 'card' }] }]).warnings.length === 0, 'style card 不报');
for (const l of ['pip', 'side', 'split', 'seek', 'beats', 'text']) ok(pops(l) === 1, `${l} 的脸旁大字报`);
ok(/「不出现」/.test(presence([{ id: 'x', t0: 0, t1: 10, layout: 'pip', pops: pop }]).warnings[0]), '警告里引用第一行字');

// 6. 端到端：最小口播工程跑 koubo_motion.mjs，timing.txt 写出人像覆盖；把全屏改成 pip 后警告消失
const F = fs.mkdtempSync(path.join(os.tmpdir(), 'cvc-presence-'));
try {
  fs.mkdirSync(path.join(F, 'motion', 'cut'), { recursive: true });
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=64x36:rate=30', '-t', '1', path.join(F, 'motion', 'cut', 'cut.mp4')]);
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=320x180', '-frames:v', '1', path.join(F, 'card.png')]);
  const sent = (i, line, t0, t1) => ({ src: 'A', sent: i, line, text: '句' + line, t0, t1, chars: [['句', t0, t0 + 0.2]] });
  fs.writeFileSync(path.join(F, 'motion', 'cut', 'cutmap.json'), JSON.stringify({ fps: 30, duration: 60, segments: [],
    sentences: [sent(0, 1, 0, 9), sent(1, 2, 10, 34), sent(2, 3, 35, 60)] }));
  fs.writeFileSync(path.join(F, 'motion', 'faces.json'), JSON.stringify({ frames: [0, 20, 40].map(t => ({ t, box: [0.42, 0.2, 0.58, 0.55] })) }));
  const config = layout => `window.MOTION = { meta: { fps: 30, title: 'presence', tail: 1 },
  source: { cut: 'cut', faces: 'faces.json', assetsRoot: ${JSON.stringify(F)} },
  theme: { bg: '#0e0f12', ink: '#EDEAE4', accent: '#FF3D00', font: 'sans-serif', mono: 'monospace' },
  brand: { pattern: { bg: '#0e0f12', word: 'rgba(237,234,228,0.05)', stroke: 1, size: 116, gap: 0.55, rowH: 200, solid: 'A', outline: 'B' }, drift: { loop: 60 } },
  media: { M: { src: 'card.png', img: true } },
  scenes: [
    { id: 's1', from: { line: 1 }, layout: 'talk' },
    { id: 's2', from: { line: 2 }, layout: '${layout}', card: true, items: [{ m: 'M', label: '素材' }] },
    { id: 's3', from: { line: 3 }, layout: 'pip', items: [{ m: 'M' }] },
  ] };`;
  const run = layout => {
    fs.writeFileSync(path.join(F, 'motion', 'motion.config.js'), config(layout));
    const out = execFileSync('node', [path.join(ROOT, 'scripts', 'koubo_motion.mjs'), '--dir', path.join(F, 'motion')]).toString();
    return { json: JSON.parse(out.split('\n')[0]), txt: fs.readFileSync(path.join(F, 'motion', 'timing.txt'), 'utf8') };
  };
  let r = run('full');
  ok(/人像覆盖：\n出镜 59%/.test(r.txt) && /s2 连续 25\.0 秒不见真人/.test(r.txt) && r.json.presenceWarnings === 1, '工程：全屏 25 秒写进 timing.txt',
    r.txt.split('人像覆盖：\n')[1]?.split('\n')[0]);
  r = run('pip');
  ok(/人像覆盖：\n出镜 100%.*最长连续不见 0 秒\n无警告/.test(r.txt) && r.json.presenceWarnings === 0 && r.json.presence === 100, '工程：改 pip 后无警告');
} finally { fs.rmSync(F, { recursive: true, force: true }); }

console.log(bad ? `PRESENCE TESTS FAIL ${bad}` : 'PRESENCE TESTS PASS');
process.exit(bad ? 1 : 0);
