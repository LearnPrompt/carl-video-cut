#!/usr/bin/env node
// 假 Chrome，给 check_launch.mjs 用。FAKE_CHROME_MODE：
//   exit             马上退出，像沙箱初始化失败
//   silent           不退出也不给调试地址
//   exit-after-open  给出调试地址，连上后收到第一条调用就退出
//   hang-after-open  给出调试地址，连上后什么都不回
// 下面几种和 exit-after-open 一样在第一条调用时退出，另外给 profile 清理制造麻烦：
//   late-write       带同一个 --user-data-dir 的子进程（像 Chrome Helper）一直往 profile 里写，主进程退出后再写 0.8 秒
//   late-write-stuck 同上，但子进程一直写、不退
//   decoy            同 late-write-stuck，另起一个独立进程占着旁边的 profile（路径是这次 profile 加后缀 -decoy），清理时不能碰它
//   rm-transient     退出前把 profile 里一个目录设成只读，0.5 秒后由一个不带 profile 参数的进程改回来
//   rm-stuck         同上，但一直不改回来
// 包一层真 Chrome（FAKE_REAL_CHROME，参数原样转交），Chrome 退出它就退出：
//   real-late-write  外加 late-write 那样的子进程
//   real-rm-stuck    启动前把 profile 里一个目录设成只读，一直不改回来
// FAKE_CHROME_PIDFILE 写入自己的 pid，测试据此确认它被回收；decoy 写 <PIDFILE>.decoy（pid 和它的目录）。
import net from 'node:net';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { chmodSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mode = process.env.FAKE_CHROME_MODE || '', role = process.env.FAKE_CHROME_ROLE;
const prof = (process.argv.find(a => a.startsWith('--user-data-dir=')) || '').slice('--user-data-dir='.length);
const child = (r, dir, opts = {}) => spawn(process.execPath, [fileURLToPath(import.meta.url), `--user-data-dir=${dir}`, `--type=fake-${r}`],
  { env: { ...process.env, FAKE_CHROME_ROLE: r, FAKE_HELPER_STUCK: /stuck|decoy/.test(mode) ? '1' : '' }, stdio: 'ignore', ...opts });
const lock = transient => {
  const d = join(prof, 'Locked');
  mkdirSync(d, { recursive: true }); writeFileSync(join(d, 'f'), 'x'); chmodSync(d, 0o555);
  if (transient) spawn(process.execPath, ['-e', `setTimeout(() => require('fs').chmodSync(${JSON.stringify(d)}, 0o755), 500)`],
    { detached: true, stdio: 'ignore' }).unref();
};

if (role === 'helper') {
  // 每 10 毫秒建目录写文件（目录被删了就连 profile 一起建回来）；父进程没了之后再写 0.8 秒，stuck 时一直写
  const ppid = process.ppid, dir = join(prof, 'LateWrite'); let n = 0, gone = 0;
  setInterval(() => {
    try { mkdirSync(dir, { recursive: true }); writeFileSync(join(dir, String(n++ % 50)), 'x'); } catch {}
    if (process.ppid !== ppid) gone ||= Date.now();
    if (gone && !process.env.FAKE_HELPER_STUCK && Date.now() - gone > 800) process.exit(0);
  }, 10);
} else if (role === 'decoy') setInterval(() => {}, 1000);
else {
  if (process.env.FAKE_CHROME_PIDFILE) writeFileSync(process.env.FAKE_CHROME_PIDFILE, String(process.pid));
  if (/late-write|decoy/.test(mode)) child('helper', prof);
  if (mode === 'decoy') {   // 目录和 pid 由主进程先同步写好，测试不会因为 decoy 还没起来而误判
    const dir = `${prof}-decoy`; mkdirSync(dir, { recursive: true }); writeFileSync(join(dir, 'keep'), 'x');
    const d = child('decoy', dir, { detached: true }); d.unref();
    writeFileSync(`${process.env.FAKE_CHROME_PIDFILE}.decoy`, `${d.pid}\n${dir}`);
  }
  if (mode === 'real-rm-stuck') lock(false);
  if (mode === 'exit') { console.error('sandbox initialization failed: Operation not permitted'); process.exit(1); }
  if (mode === 'silent') setInterval(() => {}, 1000);
  else if (mode.startsWith('real-')) {
    const chrome = spawn(process.env.FAKE_REAL_CHROME, process.argv.slice(2), { stdio: ['ignore', 'inherit', 'inherit'] });
    for (const s of ['SIGTERM', 'SIGINT']) process.on(s, () => chrome.kill(s));
    chrome.on('exit', code => process.exit(code ?? 1));
  } else {
    const onCall = () => {
      if (mode === 'hang-after-open') return;
      if (mode.startsWith('rm-')) lock(mode === 'rm-transient');
      process.exit(0);
    };
    const srv = net.createServer(sock => sock.once('data', buf => {
      const key = /sec-websocket-key: *(\S+)/i.exec(buf.toString())[1];
      const accept = crypto.createHash('sha1').update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
      sock.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
      sock.once('data', onCall);
    }));
    srv.listen(0, '127.0.0.1', () => console.error(`DevTools listening on ws://127.0.0.1:${srv.address().port}/devtools/browser/fake`));
  }
}
