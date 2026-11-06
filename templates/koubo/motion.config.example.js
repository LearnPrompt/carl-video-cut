// 口播大剪辑的参数示例：复制到 <工程>/motion/motion.config.js 再改。改完跑：
//   node ~/.claude/skills/carl-video-cut/scripts/koubo_motion.mjs --dir <工程>/motion
// 生成器把锚点换算成秒、统一素材、写出 index.html 和 timing.txt；找不到的锚点会打印出来，不会静默猜。
//
// 时刻全部按口播锚定，不写死秒数：
//   { line: 12 }              稿子.txt 第 12 句开口处
//   { line: 12, p: '关键词' }  第 12 句里说到这个词的那一刻
//   { t: 0 }                  成片秒数（只给片头这类没有口播的地方用）
//   以上都可以加 off: 0.5 微调，单位秒
//
// 版式（layout）：
//   talk  你全屏，可挂信息层（kicker/title/sub/chips）或文字动画（pops）
//   side  你全屏，素材卡避开你的脸放在旁边
//   pip   素材卡在左，你在右下小窗；讲素材的长段默认用它
//   split 素材卡在左，你在右侧竖条
//   full  素材全屏；card: true 时改成右对齐的大卡片（快切蒙太奇用，不在铺满和卡片之间来回跳）
//   grid  宫格，cols 列数；可带 quote 打字引用
//   text  提示语打字卡 + 小窗
//   seek  时间轴示意 + 小窗（marks 标跳到第几秒）
//   beats 拍点示意 + 小窗
//
// 素材条目（items 里每一项）：m 素材键，in 入点秒（'cont' 接着上一场同一素材往下放），label 名字，kicker 小标题，
//   at 锚点（同一场多条素材按锚点依次切），zoom { x, y, s, to } 推近到素材里某块区域，pos 'top' | 'tr' 让开素材自带字幕。
// 文字动画（pops，伪 AE 层）：style 'card' 整屏字卡，不写是脸旁大字；hl 橙底重点词，stk 划掉（stkAt 说到再划），
//   num 大号序号，kicker 小标题，at 出现锚点（默认场景开头），dur 停留秒（默认到下一条或场景结尾）。
//   落在场景开头或结尾的整屏字卡会自动对齐切点，色块盖住换场。整片十来条为宜，太密就成了 PPT。
window.MOTION = {
  meta: { width: 1920, height: 1080, fps: 30, title: '片名', tail: 3 },   // tail：口播说完后多留几秒（结尾标题卡停住）
  theme: {
    bg: '#0e0f12', ink: '#EDEAE4', accent: '#C4482A',
    font: '"PingFang SC", "Hiragino Sans GB", sans-serif', mono: '"SF Mono", Menlo, monospace',
  },
  brand: {   // 品牌底：两种字重复排、慢慢漂移；换成你自己的名字
    pattern: { bg: '#1c1c1e', word: '#3a3a3c', solid: 'Your', outline: 'NAME', size: 150, rowH: 172, gap: 0.62, stroke: 2 },
    drift: { loop: 60 },
    // noPartWipe: true,   // 关掉章节开头的整屏字卡
  },
  // 路径都相对本文件所在的 motion 目录。cut：cut_takes.py 的输出目录（含 cut.mp4、cut.voice.wav、cutmap.json），
  // 按 SKILL.md 的步骤它在工程根目录，所以写 ../cut；faces：faces.swift 的输出；assetsRoot：素材根目录
  source: { cut: '../cut', faces: 'faces.json', assetsRoot: '~/path/to/素材目录' },

  media: {
    demo1: { src: 'demo1.mp4', by: '@作者' },
    demo2: { src: 'demo2.mp4', by: '@作者', crop: [0, 0, 1600, 900] },   // crop [x, y, w, h] 裁掉录屏黑边
    shot: { src: 'screenshot.png', by: '出处', img: true },
    ending: { src: 'ending.mp4', by: '@作者', in: 2, hold: 6 },          // hold：只用到第 6 秒，之后定格
  },

  // 章节角标；PART n · 名字 这种写法会在章节开头自动出整屏字卡
  parts: [
    { from: { t: 0 }, tag: '片名 · 副标题' },
    { from: { line: 4 }, tag: 'PART 1 · 第一章' },
    { from: { line: 12 }, tag: 'PART 2 · 第二章' },
  ],

  scenes: [
    // 开场先亮真实成品
    { id: 's01', from: { line: 1 }, layout: 'side', kicker: '一句话说清这支片讲什么',
      items: [{ m: 'demo1', in: 3, label: '案例一' }, { m: 'demo2', in: 0, label: '案例二', at: { line: 1, p: '第二个' } }] },
    // 关键判断用整屏字卡
    { id: 's02', from: { line: 2 }, layout: 'talk',
      pops: [{ style: 'card', text: '这里写\n全片最重要的一句话', hl: ['最重要'], dur: 2.6 }] },
    // 第几个技巧：脸旁大字加序号，序号要成套
    { id: 's03', from: { line: 4 }, layout: 'talk',
      pops: [{ num: '01', kicker: '第一个技巧', text: '技巧的名字', hl: ['名字'] }] },
    // 讲素材的长段
    { id: 's04', from: { line: 5 }, layout: 'pip', kicker: '小标题', title: '这一段讲什么',
      items: [{ m: 'demo1', in: 10, label: '素材名' }],
      chips: [{ text: '要点一', at: { line: 5, p: '要点' } }, { text: '反例', kind: 'ng', at: { line: 6 } }] },
    { id: 's05', from: { line: 7 }, layout: 'full', items: [{ m: 'demo2', in: 4, kicker: '全屏素材', label: '名字', pos: 'top' }] },
    { id: 's06', from: { line: 9 }, layout: 'grid', cols: 2, qkicker: '提示语', quote: '打字出现的一段原话', type: 1.6,
      items: [{ m: 'demo1', in: 0 }, { m: 'shot' }] },
    // 说到某个词再划掉
    { id: 's07', from: { line: 11 }, layout: 'talk',
      pops: [{ text: '不用先学会\n写代码', stk: ['写代码'], stkAt: { line: 11, p: '代码' } }] },
    { id: 's08', from: { line: 14 }, layout: 'full', noTag: true, items: [{ m: 'ending' }] },
  ],
};
