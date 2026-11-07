// 人像覆盖：口播片里真人在不在画面、最长连续多久不见、脸旁大字会不会压到素材卡。只看分镜版式，不看像素，
// koubo_motion.mjs 每次同步都算一遍写进 timing.txt。版式含义见 templates/koubo/index.html 里的 L 表。
export const HIDDEN = new Set(['full', 'grid']);   // 这两种版式把真人整个藏掉，其余版式真人都在（全屏、小窗、侧条、竖条）
// shortcut: 只按版式算。style 'card' 的整屏字卡和 PART 章节字卡也会盖住真人（一条一两秒），这里不计；
//   full 加 card 时脸旁大字很长、脸又偏右，字也可能伸到右侧卡片上。要计进来得按 pop 的实际时长和卡片宽度算，真出过问题再加。

// scenes: [{ id, t0, t1, layout, card, pops }]，按时间顺序、首尾相接。maxAbsent：连续不见真人的上限（秒）。
export function presence(scenes, { maxAbsent = 20 } = {}) {
  let total = 0, shown = 0, run = null;
  const runs = [];
  for (const s of scenes) {
    const d = s.t1 - s.t0; total += d;
    if (!HIDDEN.has(s.layout)) { shown += d; if (run) runs.push(run); run = null; continue; }
    if (run) { run.to = s.id; run.t1 = s.t1; } else run = { from: s.id, to: s.id, t0: s.t0, t1: s.t1 };
  }
  if (run) runs.push(run);
  const len = r => r.t1 - r.t0;
  const longest = runs.reduce((a, r) => (!a || len(r) > len(a) ? r : a), null);
  const warnings = [];
  for (const r of runs) if (len(r) > maxAbsent + 1e-6)
    warnings.push(`${r.from === r.to ? r.from : r.from + '–' + r.to} 连续 ${len(r).toFixed(1)} 秒不见真人（上限 ${maxAbsent} 秒）：讲素材的长段改 pip，全屏只留脚本写明的地方`);
  // 脸旁大字从左边 96px 起铺：talk 左边是空的，full 加 card 时卡片靠右；其余版式左边是素材卡或宫格，字会压在素材上
  for (const s of scenes) for (const p of s.pops || []) {
    if (p.style === 'card' || s.layout === 'talk' || (s.layout === 'full' && s.card)) continue;
    warnings.push(`${s.id}：${s.layout} 版式里的脸旁大字「${String(p.text).split('\n')[0]}」会压在素材上，改成卡下 chips 或右栏文字`);
  }
  return { ratio: total ? shown / total : 0, shown, total, longest, warnings };
}

export function presenceText(p) {
  const L = p.longest;
  const head = `出镜 ${(p.ratio * 100).toFixed(0)}%（${p.shown.toFixed(1)} / ${p.total.toFixed(1)} 秒），最长连续不见 ` +
    (L ? `${(L.t1 - L.t0).toFixed(1)} 秒（${L.from === L.to ? L.from : L.from + '–' + L.to}）` : '0 秒');
  return head + '\n' + (p.warnings.join('\n') || '无警告');
}
