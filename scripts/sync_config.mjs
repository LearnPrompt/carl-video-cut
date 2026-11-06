#!/usr/bin/env node
// sync_config.mjs <project_dir>
// 读 <project_dir>/motion.config.js（window.MOTION），累加各 scene 的 dur 得到 start 和总时长，
// 再把 index.html 里 data-scene="<id>" 元素的 data-start / data-duration、
// 以及 id="root" 元素的 data-duration 改写为计算值（保留其他属性；缺则补上）。
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const dir = process.argv[2];
if (!dir || dir === '-h' || dir === '--help') { console.error('usage: node sync_config.mjs <project_dir>'); process.exit(2); }
const cfgPath = path.join(dir, 'motion.config.js');
const htmlPath = path.join(dir, 'index.html');
for (const p of [cfgPath, htmlPath]) if (!fs.existsSync(p)) { console.error(`缺少文件：${p}`); process.exit(2); }

const win = {};
vm.runInNewContext(fs.readFileSync(cfgPath, 'utf8'), { window: win, console }, { filename: cfgPath });
const M = win.MOTION;
if (!M || !Array.isArray(M.scenes)) { console.error('motion.config.js 里没有 window.MOTION.scenes'); process.exit(2); }

const r3 = x => Math.round(x * 1000) / 1000;
let t = 0;
const rows = M.scenes.map(s => { const row = { id: s.id, start: r3(t), dur: r3(s.dur) }; t += s.dur; return row; });
const total = r3(t);

let html = fs.readFileSync(htmlPath, 'utf8');
const setAttr = (tag, name, val) => {
  const re = new RegExp(`(\\s${name}=)(["'])[^"']*\\2`);
  return re.test(tag) ? tag.replace(re, `$1"${val}"`) : tag.replace(/\s*(\/?)>$/, ` ${name}="${val}"$1>`);
};
const missing = [];
for (const r of rows) {
  const re = new RegExp(`<[a-zA-Z][^<>]*\\sdata-scene=["']${r.id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}["'][^<>]*>`);
  if (!re.test(html)) { missing.push(r.id); continue; }
  html = html.replace(re, tag => setAttr(setAttr(tag, 'data-start', r.start), 'data-duration', r.dur));
}
const rootRe = /<[a-zA-Z][^<>]*\sid=["']root["'][^<>]*>/;
if (rootRe.test(html)) html = html.replace(rootRe, tag => setAttr(tag, 'data-duration', total));
else console.error('[warn] index.html 里没找到 id="root"');
fs.writeFileSync(htmlPath, html);

const htmlIds = [...html.matchAll(/\sdata-scene=["']([^"']+)["']/g)].map(m => m[1]);
const extra = htmlIds.filter(id => !rows.some(r => r.id === id));
const pad = (s, n) => String(s).padEnd(n);
console.log(`${pad('scene id', 12)} | ${pad('start', 8)} | dur`);
console.log('-'.repeat(32));
for (const r of rows) console.log(`${pad(r.id, 12)} | ${pad(r.start, 8)} | ${r.dur}`);
console.log('-'.repeat(32));
console.log(`${pad('root total', 12)} | ${pad(0, 8)} | ${total}   (fps ${M.meta?.fps ?? '?'}, ${Math.round(total * (M.meta?.fps ?? 30))} frames)`);
if (missing.length) { console.error(`[warn] index.html 缺少 data-scene: ${missing.join(', ')}`); process.exitCode = 1; }
if (extra.length) console.error(`[warn] index.html 有 config 里没有的 data-scene: ${extra.join(', ')}`);
