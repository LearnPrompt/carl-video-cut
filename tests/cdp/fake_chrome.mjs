#!/usr/bin/env node
// 假 Chrome，给 check_launch.mjs 用。FAKE_CHROME_MODE：
//   exit             马上退出，像沙箱初始化失败
//   silent           不退出也不给调试地址
//   exit-after-open  给出调试地址，连上后收到第一条调用就退出
//   hang-after-open  给出调试地址，连上后什么都不回
// FAKE_CHROME_PIDFILE 写入自己的 pid，测试据此确认它被回收。
import net from 'node:net';
import crypto from 'node:crypto';
import { writeFileSync } from 'node:fs';

const mode = process.env.FAKE_CHROME_MODE;
if (process.env.FAKE_CHROME_PIDFILE) writeFileSync(process.env.FAKE_CHROME_PIDFILE, String(process.pid));
if (mode === 'exit') { console.error('sandbox initialization failed: Operation not permitted'); process.exit(1); }
if (mode === 'silent') setInterval(() => {}, 1000);
else {
  const srv = net.createServer(sock => sock.once('data', buf => {
    const key = /sec-websocket-key: *(\S+)/i.exec(buf.toString())[1];
    const accept = crypto.createHash('sha1').update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
    sock.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
    sock.once('data', () => { if (mode === 'exit-after-open') process.exit(0); });
  }));
  srv.listen(0, '127.0.0.1', () => console.error(`DevTools listening on ws://127.0.0.1:${srv.address().port}/devtools/browser/fake`));
}
