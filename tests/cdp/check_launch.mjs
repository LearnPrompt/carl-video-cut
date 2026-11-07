#!/usr/bin/env node
// cdp.mjs 遇到 Chrome 起不来或中途出事要及时失败，并只回收自己启动的 Chrome。
//   node tests/cdp/check_launch.mjs [--no-real]
// 先用假 Chrome 跑四种启动故障和六种 profile 清理故障（子进程晚写、子进程不退、旁边有独立 profile 的进程、
// 删除暂时失败、删除一直失败、列不出进程）；再用真 Chrome 跑正常路径、页面死循环（拿到页面之后超时）、两者各加一个
// 晚写子进程、正常路径但 profile 一直删不掉、外层 seatbelt 沙箱、沙箱里显式 CVC_CHROME_FLAGS=--no-sandbox。
// 每项一行 PASS / FAIL，最后一行 CDP ALL PASS 或 CDP FAIL。--no-real 跳过真 Chrome。
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import os from 'node:os';

const HERE = dirname(fileURLToPath(import.meta.url)), S = resolve(HERE, '../..');
const CHECK = join(S, 'scripts/check_determinism.mjs'), FAKE = join(HERE, 'fake_chrome.mjs');
const REAL = process.env.CVC_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const tmp = mkdtempSync(join(os.tmpdir(), 'cvc-cdp-test-'));
cpSync(join(S, 'templates/hyperframes'), join(tmp, 'tpl'), { recursive: true });
cpSync(join(HERE, 'loop'), join(tmp, 'loop'), { recursive: true });
// 被测进程的 TMPDIR 指到这里，profile 和 Chrome 进程都只在这个目录下找，不受机器上别的 Chrome 影响
const TMPD = join(tmp, 'tmpdir'); mkdirSync(TMPD);
const profiles = () => new Set(readdirSync(TMPD).filter(n => n.startsWith('cvc-chrome-')));
const chromes = () => new Set(spawnSync('pgrep', ['-f', `user-data-dir=${TMPD}/cvc-chrome-`], { encoding: 'utf8' }).stdout.split('\n').filter(Boolean));
const alive = pid => { try { process.kill(pid, 0); return true; } catch { return false; } };
let fails = 0;

// 清理失败时必须同时保留原故障和清理诊断（路径）
const cleanupFail = first => new RegExp(`DETERMINISM ERROR：${first}[\\s\\S]*临时 profile 清理失败：[^\\n]*cvc-chrome-`);
// keep：预期留下几个 profile（只有清理失败的几项是 1，且报错里必须写明）。decoy：旁边独立 profile 的进程必须活着、目录完整
function run(name, { env = {}, wrap = [], dir = 'tpl', times = '0.5', code, match, maxSec, keep = 0, decoy = false }) {
  const pidFile = join(tmp, `${name}.pid`), before = profiles(), procs = chromes(), t0 = Date.now();
  const r = spawnSync(wrap[0] || process.execPath, [...wrap.slice(1), ...(wrap.length ? [process.execPath] : []), CHECK, '--dir', join(tmp, dir), '--times', times],
    { env: { ...process.env, TMPDIR: TMPD, FAKE_CHROME_PIDFILE: pidFile, ...env }, encoding: 'utf8', timeout: 120000 });
  const sec = (Date.now() - t0) / 1000, out = `${r.stdout}${r.stderr}`;
  const pid = existsSync(pidFile) ? +readFileSync(pidFile, 'utf8') : 0;
  const [dpid, ddir] = existsSync(`${pidFile}.decoy`) ? readFileSync(`${pidFile}.decoy`, 'utf8').split('\n') : [];
  const newProcs = [...chromes()].filter(p => !procs.has(p) && p !== dpid), newProfs = [...profiles()].filter(n => !before.has(n) && !n.endsWith('-decoy'));
  const leaks = [...(pid && alive(pid) ? ['Chrome 没回收'] : []), ...newProcs.map(p => `Chrome 进程 ${p} 没回收`),
    ...(!decoy ? [] : !dpid ? ['旁边独立 profile 的进程没起来'] : [...(alive(+dpid) ? [] : ['误杀了旁边独立 profile 的进程']),
      ...(existsSync(join(ddir, 'keep')) ? [] : ['误删了旁边独立 profile 的目录'])]),
    ...(newProfs.length === keep ? [] : newProfs.length ? newProfs.map(n => `profile 没删 ${n}`) : [`应留下 ${keep} 个 profile，实际 0 个`])];
  const ok = (Array.isArray(code) ? code : [code]).includes(r.status) && match.test(out) && sec <= maxSec && !leaks.length;
  if (!ok) fails++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(22)} 退出码 ${r.status}  ${sec.toFixed(1)}s  ${out.split('\n').find(l => /DETERMINISM/.test(l)) || out.split('\n')[0]}${leaks.length ? `  ${leaks.join('；')}` : ''}`);
  // 测试自己的进程（假 Chrome、本测试 TMPDIR 下的 Chrome 和晚写子进程），留着会挂住或干扰后面的项
  for (const p of [pid, +dpid, ...newProcs.map(Number)]) if (p && alive(p)) process.kill(p, 'SIGKILL');
}

const fake = mode => ({ CVC_CHROME: FAKE, FAKE_CHROME_MODE: mode });
run('假：马上退出', { env: fake('exit'), code: 2, match: /DETERMINISM ERROR：Chrome 退出/, maxSec: 5 });
run('假：不给调试地址', { env: fake('silent'), code: 2, match: /DETERMINISM ERROR：Chrome 启动 10 秒没有结果/, maxSec: 16 });
run('假：连上后退出', { env: fake('exit-after-open'), code: 2, match: /DETERMINISM ERROR：(Chrome 调试连接断开|Chrome 退出)/, maxSec: 5 });
run('假：连上后不回', { env: { ...fake('hang-after-open'), CVC_CDP_TIMEOUT: '3' }, code: 2, match: /DETERMINISM ERROR：.*3 秒没有结果/, maxSec: 10 });
const gone = /DETERMINISM ERROR：(Chrome 调试连接断开|Chrome 退出)/;
run('假：子进程晚写', { env: fake('late-write'), code: 2, match: gone, maxSec: 6 });
run('假：子进程不退', { env: fake('late-write-stuck'), code: 2, match: gone, maxSec: 8 });
run('假：旁边有独立 profile', { env: fake('decoy'), code: 2, match: gone, maxSec: 8, decoy: true });
run('假：profile 暂时删不掉', { env: fake('rm-transient'), code: 2, match: gone, maxSec: 6 });
run('假：profile 一直删不掉', { env: fake('rm-stuck'), code: 2, match: cleanupFail('Chrome (调试连接断开|退出)'), maxSec: 8, keep: 1 });
// PATH 里放一个总是出错的 pgrep（退出码 3）：列不出进程不能当作没有进程
const nops = join(tmp, 'nops'); mkdirSync(nops); writeFileSync(join(nops, 'pgrep'), '#!/bin/sh\nexit 3\n', { mode: 0o755 });
run('假：列不出进程', { env: { ...fake('exit-after-open'), PATH: `${nops}:${process.env.PATH}` }, code: 2,
  match: /临时 profile 清理失败：[^\n]*cvc-chrome-[^\n]*pgrep 列不出进程：退出码 3/, maxSec: 6, keep: 1 });

if (!process.argv.includes('--no-real') && existsSync(REAL)) {
  run('真：正常路径', { code: 0, match: /DETERMINISM PASS/, maxSec: 60 });
  run('真：页面死循环', { dir: 'loop', times: '0.5,1.5', env: { CVC_CDP_TIMEOUT: '3' }, code: 2, match: /DETERMINISM ERROR：Runtime.evaluate 3 秒没有结果/, maxSec: 20 });
  const late = { ...fake('real-late-write'), FAKE_REAL_CHROME: REAL };
  run('真：正常路径+晚写', { env: late, code: 0, match: /DETERMINISM PASS/, maxSec: 60 });
  run('真：页面死循环+晚写', { dir: 'loop', times: '0.5,1.5', env: { ...late, CVC_CDP_TIMEOUT: '3' }, code: 2, match: /DETERMINISM ERROR：Runtime.evaluate 3 秒没有结果/, maxSec: 25 });
  // 检查本身通过，但 profile 删不掉：不能报 PASS，要退出码 2 写明路径
  run('真：正常路径+删不掉', { env: { ...fake('real-rm-stuck'), FAKE_REAL_CHROME: REAL }, code: 2, match: cleanupFail(''), maxSec: 60, keep: 1 });
  if (existsSync('/usr/bin/sandbox-exec')) {
    const seatbelt = ['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)'];
    // 外层沙箱里 Chrome 自带沙箱多半起不来；要求的是有界：要么正常通过，要么 30 秒内报错退出
    run('真：外层沙箱', { wrap: seatbelt, code: [0, 2], match: /DETERMINISM (PASS|ERROR)/, maxSec: 30 });
    run('真：外层沙箱+显式开关', { wrap: seatbelt, env: { CVC_CHROME_FLAGS: '--no-sandbox' }, code: 0, match: /DETERMINISM PASS/, maxSec: 60 });
  }
} else console.log('跳过真 Chrome');

spawnSync('chmod', ['-R', 'u+rwx', tmp]);   // 删不掉那几项留下的只读目录
rmSync(tmp, { recursive: true, force: true });
console.log(fails ? `CDP FAIL：${fails} 项` : 'CDP ALL PASS');
process.exit(fails ? 1 : 0);
