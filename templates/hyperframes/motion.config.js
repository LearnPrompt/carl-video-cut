// 唯一参数源：改文案、时长、数字、素材路径只改这里，其余文件不写死任何内容。
// 改完 dur 之后跑：node scripts/sync_config.mjs <本目录>   （把 data-start / data-duration 同步进 index.html）
//
// beats 是场内 0-1 比例（改 dur 时场内动画按比例拉伸）：
//   in   入场动画开始
//   hold 入场结束、画面完整停留开始
//   out  出场动画开始（到 1.0 结束）
window.MOTION = {
  meta: { width: 1920, height: 1080, fps: 30, title: '示例工程 · 版本更新' },
  theme: {
    bg: '#F3EDE2',
    ink: '#141414',
    accent: '#C4482A',
    font: '"PingFang SC", sans-serif',
    mono: 'Menlo, monospace',
  },
  scenes: [
    // 标题卡：text.title 里用 \n 换行，每行单独遮罩上滑
    { id: 's1', type: 'title', dur: 4, beats: { in: 0.0, hold: 0.15, out: 0.9 },
      text: { kicker: 'VERSION 2.0', title: '更快\n更稳', sub: '启动耗时降低一半，常驻内存更低' } },

    // 数字对比：number 指向顶层 numbers 的键；数字从 old 滚到 new
    { id: 's2', type: 'compare', dur: 5, beats: { in: 0.0, hold: 0.2, out: 0.9 },
      text: { kicker: '冷启动', title: '启动耗时快了一倍多', oldLabel: '旧版本', newLabel: '新版本', deltaLabel: '耗时下降' },
      number: 'latency' },

    // 截图推镜：asset 指向顶层 assets 的键；zoom 是推镜目标区域（图片 0-1 坐标）
    { id: 's3', type: 'focus', dur: 5, beats: { in: 0.0, hold: 0.2, out: 0.9 },
      text: { kicker: '新面板', title: '指标一屏看完', sub: '延迟、内存、错误率集中在右上角，一眼定位问题。' },
      asset: 'shot1' },
  ],
  numbers: {
    latency: { old: 2.4, new: 1.1, unit: 's', decimals: 1 },
  },
  assets: {
    shot1: { src: 'assets/screenshot.svg', w: 1600, h: 900, zoom: { x: 0.58, y: 0.18, w: 0.34, h: 0.32 } },
  },
};
