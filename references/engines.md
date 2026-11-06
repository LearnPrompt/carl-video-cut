# 引擎手册

五种引擎共用一个原则：画面是 t 的纯函数，参数只在一个文件里。下面每节是该引擎的约定、命令和踩过的坑。命令以本机实际 `--help` 为准，这里是 2026-10 的记录。

## HyperFrames（项目动效默认）

HeyGen 开源的 HTML 转 MP4 渲染器，逐帧确定性渲染 HTML、CSS、媒体和可 seek 的 GSAP 时间线。需要 Node 22 以上和 FFmpeg。

```bash
npx hyperframes init my-video            # 可加 --non-interactive、--video x.mp4、--audio x.mp3
npx hyperframes lint                     # 静态检查
npx hyperframes check                    # 新版终审门；旧版叫 inspect，--snapshots 出带标注的帧
npx hyperframes preview                  # 默认 3002 端口，--port 改
npx hyperframes snapshot --at 1,3,5      # 指定秒数截帧；--frames 10 均匀取 10 帧
npx hyperframes render --quality draft --output draft.mp4
npx hyperframes render --quality delivery --fps 30 --output final.mp4   # 旧版质量档是 draft/standard/high
npx hyperframes doctor                   # 渲染失败先跑这个
```

渲染参数：`--fps` 24/30/60，默认 30；新版另有 `--resolution 1080p`、`--crf`、`--workers`、`--strict`。画布尺寸由根节点 `data-width` / `data-height` 决定。

版本：`npx hyperframes` 会解析最新版，新版本没缓存时 `--no-install` 直接报错。用 `grep -h '"version"' ~/.npm/_npx/*/node_modules/hyperframes/package.json` 查已缓存版本，工程里把用到的版本钉死，命令写成 `npx --no-install hyperframes@<版本> ...`；第一次用先去掉 `--no-install` 让 npx 下载（征得用户同意），要升级也先问用户。口播模板在 0.8.181 上验证过。

HTML 约定：

- 根节点 `<div id="root" data-composition-id="main" data-start="0" data-width="1920" data-height="1080" data-duration="总秒数">`。
- 每幕是带 `data-start` 和 `data-duration` 的元素（模板里是 `section.clip[data-scene]`），这两个值由 `sync_config.mjs` 从 config 计算写入，不手改。
- GSAP 时间线必须 `gsap.timeline({paused:true})` 并挂到 `window.__timelines["main"]`，键与 `data-composition-id` 一致。
- 视频元素 `muted playsinline`；音频元素必须有 `id`，否则渲染无声，音量用 `data-volume`。

坑：

- CSS 里写了初始 transform，GSAP 再动同一属性会冲突，用 `fromTo` 或 `xPercent`。
- 给 `.clip` 本身做 `autoAlpha` 或 `visibility` 动画会和调度打架，动它的子节点。
- 根节点不要硬写 `width:1920px` 样式，不要给 video/audio 加 `crossorigin`。
- lint 有错时 check 会跳过浏览器审计，显示 0 个问题不代表干净，先清 lint。
- 渲染摘要第二行出现 screenshot 加 software gpu，说明走了慢路径，跑 doctor。
- 渲染器 seek 时可能抑制回调，靠 `onUpdate` 写数字或文字会停在初值。数字滚动补间元素的 `innerText` 加 `modifiers` 格式化，注意时间线位于补间之前时显示的是未格式化的初值，初值要和格式化结果一致。
- 逐字打字效果：每个字一个 span，用 `fromTo(spans, {display:'none'}, {display:'inline', stagger})` 依次显现，光标元素放在最后跟着走。补间 innerText 做打字会在打字开始前露出一个 0（2026-10-11 GoodCase 片实测）。
- 每个 `.clip` 给一个 `id`，否则 lint 报 studio_missing_editable_id 警告。
- 0.8.145 实测：质量档 draft / looks（默认，CRF 16）/ delivery；18 秒 1080p 草稿约 10 秒渲完。

## selfrender（HyperFrames 不可用时）

页面暴露 `window.__render(t)` 纯函数和 `window.MOTION_TOTAL`，Playwright 逐帧调用后截图，管道送 ffmpeg。来自 AI做小程序 10 秒动画和 AI-MAP 3D 版，两次实战验证过。

```bash
NODE_PATH=<含 playwright-core 的 node_modules> node ~/.claude/skills/carl-video-cut/scripts/render_frames.mjs --dir <工程> --stills 0.5,2,5
NODE_PATH=... node ~/.claude/skills/carl-video-cut/scripts/render_frames.mjs --dir <工程> --fps 30 --out draft.mp4
```

工程里没有 playwright-core 时请用户同意后 `npm i -D playwright-core`，Chrome 用本机已装的。60fps 先出 30fps 小样，用户认可后再出 60fps。

## manim（讲解片）

manim 0.21，Python 3.12 venv，1920x1080@30。首次使用先建 venv（经用户同意再装）：

```bash
brew install cairo pkgconf
python3.12 -m venv ~/mvenv && ~/mvenv/bin/pip install manim==0.21.* edge-tts
```
框架来自三支 3B1B 风格讲解片，场景基类按下面的约定写：

- `setup` 读 `timings.json`，`seg(key)` 挂一段配音，`at(t)` 等到段内时刻，`sent(i)` 等到第 i 句开始，`finish()` 补齐到段尾。动画对齐用句子起点，不用估算时长；涉及数字的动作等念到那个数字再动。
- 字幕全部预建，只切透明度，按 `，、：；` 断行，每行最多 21 字，底部 0.6 透明黑条。
- 常量区放文件顶部：配色、字体、每段 key。

坑：

- 用 updater 的对象没 `self.add` 就不会执行，加完再把字幕 `bring_to_front`。
- 每帧新建 Text 会卡，还会因为渲染器在每次 play 开始固定对象列表而不显示，所以要预建。
- 0.21 的 CLI 没有 `--max_files_cached`，清缓存用 `--flush_cache`。
- pycairo 装不上时先 `brew install cairo pkgconf` 再 pip。

## 口播大剪辑（真人口播 + 代码版式）

真人口播剪完（cut_takes.py 出 cut/）以后，画面整片用 HyperFrames 渲：你的人像、素材卡、品牌底、信息层都在同一个页面里，按时间算出来。2026-10-21 Opus 5.5 教程片首次实战。

```bash
swift ~/.claude/skills/carl-video-cut/scripts/lib/faces.swift <工程>/cut/cut.mp4 0.5 > <工程>/motion/faces.json
node ~/.claude/skills/carl-video-cut/scripts/koubo_motion.mjs --dir <工程>/motion      # 锚点换算、素材统一、写 index.html 和 timing.txt
npx --no-install hyperframes@0.8.181 lint <工程>/motion
npx --no-install hyperframes@0.8.181 snapshot <工程>/motion --at 12,60,98 --no-end --output snap   # 先看静帧再整片渲
HF_CAPTURE_PARALLEL_STREAM=true npx --no-install hyperframes@0.8.181 render <工程>/motion --quality draft --output renders/draft.mp4
```

- 模板 `templates/koubo/index.html`，参数只在工程的 `motion.config.js`。场景时刻按口播锚定（`{line}`、`{line, p: '说出的词'}`），找不到的锚点同步时打印，不静默猜。
- 版式九种：talk、side、pip、split、full、grid、text、seek、beats，含义写在 config 文件头。讲素材的长段默认 pip（素材卡 + 右下人像小窗），真人不能连续几十秒不见；全屏素材只给脚本写了全屏的地方。
- 人像只有一个 video 元素，按场景换位置和裁切：小窗按该场人脸中位数居中裁、头顶留空；side 的素材卡宽度由该场人脸最左边算，卡片不进脸。
- 素材卡：同一场卡框比例取素材比例中位数，比例不同的条目 contain，卡框不跳。条目名和署名放卡框外（上沿外左右两端；side、split 放下沿外，右侧空得出 320 就放右侧），不压素材。字号下限：正文 36，标签和署名 24 以上。
- 条目可选：`zoom: {x, y, s, to}` 推近到素材里的某块区域（录屏只看预览区时用）；全屏条目 `pos: 'top' | 'tr'` 让开素材自带的底部字幕。场景可选 `qat` 锚点，引用行到那句话才开始打字；`card: true` 让全屏场的条目都用右对齐卡片（快切蒙太奇不在铺满和卡片之间来回跳，素材四角自带角标、字幕时也用它）；`noTag: true` 这一场藏章节角标。素材可写 `crop: [x, y, w, h]`（图片和视频都行）、`hold: 秒`（只用到这一秒，之后定格，片尾标题卡要停住时用）。素材不够长一律停在最后一帧，不循环回片头。
- 概念文字动画（伪 AE 层，2026-10-22 v2 加）：场景写 `pops: [...]`，每条一句关键话。`style: 'card'` 是整屏字卡，橙色色块从左扫进再扫开露出字，退场再扫一遍，用在章节开头和关键判断；默认是脸旁大字，逐行先铺深色底条再逐字弹进来，字号按脸左边的空位自动收。可选 `kicker` 小标题、`num` 大号橙色序号（第几个技巧，整屏字卡和脸旁大字都能用，序号要成套，有 02 就得有 01）、`hl: ['词']` 字齐后橙底块擦过、`stk: ['词']` 划掉加 `stkAt` 锚点（说到那个词再划）、`at` 锚点（默认场景开头）、`dur` 秒（默认到下一条前 0.35 秒）、`size` 字号。带 `PART n · 名字` 的章节角标自动在章节开头出整屏字卡，`brand.noPartWipe: true` 关掉。用了 pops 的 talk 场把原来的 title 和 chips 删掉，免得两层字打架。整片控制在十来条，太密就成 PPT。
- 素材自带烧录字幕的全屏段别用 zoom 去裁：字幕位置每段不同，裁一半比不裁难看（Opus 5.5 片 M42 实测）。
- 定入点前先把素材每秒一帧拼成一张图看一遍（`ffmpeg -vf "fps=1,scale=320:-2,tile=5x4"`）：录屏开头常有几秒黑画面，混剪素材中段常夹着带侧栏的录屏，按名字猜入点会撞上（M45、M25 实测）。
- transformOrigin 不能写进补间的 from/to：往回 seek 时 GSAP 把它还原成默认值，再往前不会重设，入场途中那几帧随 seek 历史错位，确定性检查只在入场时刻报不一致（2026-10-22 逐元素比对查实）。一律在搭建时 `gsap.set(el, {transformOrigin})` 设一次。clearProps 落平也别用，零时长 set 往回 seek 时会再触发清除。
- 字卡和切点的关系：整屏字卡落在换场处，色块必须在切点那一帧正好盖满，入场提前一个扫入时长（模板里 WIPE 0.22 秒，场景开头的字卡和 PART 字卡自动提前），退场离场景结尾不到 1.3 秒就挪到切点上，揭开的是下一场。否则新场景先闪 6 到 8 帧再被盖住，或者揭开上一场的半秒尾巴（v2 三路审片每一路都把这条列为最严重）。
- 视频的 data-start 只能往下取整到毫秒：场景起点落在帧的三分之二处（如 507.36667）四舍五入成 507.367，比那一帧晚，渲染器那一帧不出素材画面，换场第一帧变成空卡或黑帧，只有部分切点中招，很难靠肉眼抽查发现。
- 同一个元素别既用 show() 的 autoAlpha 零时长 set 管隐藏、又用 opacity 补间做退场：先跳到片尾再 seek 回来时，set 按 opacity 0 把 visibility 还原成 hidden，退场补间只还原 opacity，字是不透明的却看不见（v2 脸旁大字实测整块消失）。退场补间直接用 autoAlpha，隐藏只交给它。
- 模板只用 2D transform 并 `gsap.config({force3D:false})`：3D 停住后文字栅格会随合成层历史差一两个色阶。仿 3D 入场用 2D 版（横向收窄加左移再放开）。
- 同一个源文件在同一时段出现多次（宫格），每格要单独一份文件（media 里多写几个键），否则 lint 报 duplicate_media_discovery_risk，抽帧可能被去重。
- 实测：多 worker 截图渲染默认把每帧落盘，8.5 分钟 1080p 要约 16 GB 临时空间；加 `HF_CAPTURE_PARALLEL_STREAM=true` 改成流式编码。素材抽帧缓存在 `$TMPDIR/hyperframes-extract-cache-<uid>`，同一批素材重渲会复用。
- 画中画帧准确：小窗里的画面和原片同一时刻逐像素比，0 帧偏移误差最小。
- 声音不在页面里：渲完用 `scripts/koubo_mux.sh` 配口播人声（结尾要垫素材原声时加 `--bed`），再走 export_1080p.sh 做响度和 probe。

## 录屏剪辑

真机录屏按口播顺序重排。参数集中在 `build.py` 顶部常量和 `SEGS` 列表，每段一行：源文件、入出点、变速、zoompan 推镜目标、PIL 画的透明 PNG 叠层（字幕条、聚光框、结尾宫格）。

导出链路：每段 `libx264 -crf 16 -preset medium` 单独编码，`concat -c copy` 拼接，最后混入配乐 `aac 192k -shortest -movflags +faststart`。所有切点落在配乐的拍上。

坑：改时长后要同步改脚本头部注释和配乐的 DUR，AI-MAP 3D 版就出现过注释写 11s、实际 12s。

## 合集与倍速

```bash
# 拼接（编码参数一致时）
ffmpeg -f concat -safe 0 -i list.txt -c copy merged.mp4
# 1.1 倍速，音画同步
ffmpeg -i merged.mp4 -filter_complex "[0:v]setpts=PTS/1.1[v];[0:a]atempo=1.1[a]" -map "[v]" -map "[a]" -c:v libx264 -crf 16 -preset slow -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart out-1.1x.mp4
```

倍速后，画面和文案里出现的总时长、分段时长都要按新时长改，开头卡写的 7 分钟在 1.1 倍速版里应是 6 分半。
