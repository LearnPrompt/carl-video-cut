---
name: carl-video-cut
description: 大剪辑。把真人口播、项目文档、录屏或讲解稿做成代码渲染的 1080p MP4：素材盘点、分镜先行、参数集中、草稿逐秒抽帧自检、终版导出。用于真人口播大剪辑（实拍口播转写对位、按遍剪、素材卡与人像小窗、伪 AE 概念文字动画）、产品动效与宣传片、版本数据对比动画、新功能展示、3B1B 式讲解片、录屏重剪、合集拼接与变速；已有工程只改一个参数（第二幕放慢、数字改成 18%）时直接走改参。
license: MIT
metadata:
  version: "0.2.0"
  author: "Carl + Claude"
---

# 大剪辑 0.2

每一帧都是代码按时间算出来的，所以视频是一份可以单点修改的工程，改一处只动一个参数。社区实测下来提示词只占一成，流程占九成：素材可追溯、分镜先确认、参数只有一个出处、草稿逐秒看过。成功交付是三样东西：用户选定的分镜、每一秒都检查过的 1080p MP4、以及一份以后改一个字只动一处的 `motion.config.js`。

## 先看进度

工程目录里已有 `motion.config.js` 和草稿时，直接跳到 **改参协议**，不重做盘点和分镜。用户给了现成分镜或口播稿时，从第 3 步开始，并把他给的稿当素材原文。

## 片型与引擎

| 片型 | 默认引擎 | 细节 |
|---|---|---|
| 真人口播：实拍口播 + 素材 + 代码版式 | 转写剪口播，再用 HyperFrames 口播模板合成 | 本文 **口播片型** 一节，engines.md 口播大剪辑节 |
| 项目动效：操作流程、数据对比、新功能 | HyperFrames（GSAP 时间线） | [engines.md](references/engines.md) HyperFrames 节 |
| 同上，但 HyperFrames 未装且用户不同意装 | selfrender：`window.__render(t)` + Playwright | engines.md selfrender 节 |
| 讲解片：概念、公式、长口播 | manim + 云希配音对位 | engines.md manim 节，[audio.md](references/audio.md) |
| 录屏剪辑：真机录屏重排、推镜、字幕条 | Python PIL 叠层 + ffmpeg 分段 | engines.md 录屏节 |
| 合集拼接、倍速 | ffmpeg | engines.md 合集节 |

工程目录默认放在 `~/Downloads/<片名>-motion/`。素材来自某个 Git 仓库时，只读那个仓库，工程不建在仓库里。

## 步骤

### 1. 素材盘点

通读项目的 README 与说明文档、落地页、新手引导流程、最新 CHANGELOG、数据图表与基准结果，写出 `material-ledger.md`：每条素材一行，含原文或数值、出处路径加行号（截图写文件路径）。从中挑三类候选：

- **流程**：一张截图讲不清的操作，例如新用户从零到第一次成功的步骤。
- **数据**：单看数字没感觉的变化，例如新旧版本速度对比。
- **新功能**：最新版本里最值得展示的一项。

完成标准：三类各至少一个候选，或者写明缺失并列出搜过哪些文件；每个候选里出现的每个文字、数字、截图都能在 ledger 里找到出处。缺的就说缺，宁可少一类也不补编。

### 2. 方案与分镜（停在这里等用户选）

按 [storyboard.md](references/storyboard.md) 的格式，给每个候选写一张方案卡：受众、播放场景、时长、对用户的实用价值，然后是逐幕分镜表，精确到每幕几秒、屏幕上出现的具体文字、数字和截图，每项标 ledger 编号。方案卡按对用户的实用程度从高到低排。

完成标准：每幕时长相加等于总时长；分镜里没有 ledger 之外的内容；展示后停下，等用户选定方案或提出修改。用户选定之前不写任何动效代码。

### 3. 环境检查

```bash
npx --no-install hyperframes --version
ffmpeg -version | head -1
node -v
```

HyperFrames 未装时，把安装方式（`npx hyperframes init <dir>` 会从 npm 下载）告诉用户，得到同意再装；不同意就改用 selfrender。Node 需要 22 以上。HyperFrames 各版本子命令有差异，以 `npx hyperframes --help` 的实际输出为准。

### 4. 搭工程

复制 `templates/hyperframes/`（或 `templates/selfrender/`）到工程目录，素材截图放进 `assets/`。所有文本、数值、时长、颜色只写进 `motion.config.js`，HTML 和动画代码只从 `window.MOTION` 读。场内动画用 0 到 1 的场内比例（`beats`）表达，这样改一幕的 `dur` 时场内节奏按比例拉伸。改完 config 跑：

```bash
node ~/.claude/skills/carl-video-cut/scripts/sync_config.mjs <工程目录>
npx hyperframes lint
```

完成标准：`grep` 工程的 HTML 和 JS，找不到任何写死的文案、数字或秒数；lint 零错误。

### 5. 草稿与逐秒自检（循环）

渲染草稿，再每秒抽一帧拼联系表：

```bash
npx hyperframes render --quality draft --output draft.mp4
~/.claude/skills/carl-video-cut/scripts/check_frames.sh draft.mp4
```

用 Read 逐张看完全部联系表，对照 [qa.md](references/qa.md) 的逐秒清单，写一张问题表：秒数、问题、改法、要动的 config 键。改完重渲、重抽、重看，直到问题表为空，再按 qa.md 的挑剔导演六项打分，每项 8 分以上才进入终版。

完成标准：最新一版草稿的每一张联系表都看过，问题表为空，六项分数都写出来了。

### 6. 终版导出与交付

```bash
npx hyperframes render --quality delivery --fps 30 --output final-raw.mp4   # 旧版无 delivery 档时用 high
~/.claude/skills/carl-video-cut/scripts/export_1080p.sh final-raw.mp4 <片名>-1080p.mp4
~/.claude/skills/carl-video-cut/scripts/check_frames.sh <片名>-1080p.mp4
```

`export_1080p.sh` 结束会自动跑 `probe.sh --expect 1920x1080`，必须 PASS。封面取最后一帧。交付时给出：MP4 绝对路径、probe 输出、终版联系表路径、可改参数清单（config 里每个键对应哪一幕哪句话）。只落盘到本地，发布、上传、推送等对外动作等用户下指令。

## 口播片型

用户自己录了口播（照稿逐句录或拿稿当提纲自由讲），手上有稿子和素材，要剪成带素材卡、人像小窗和概念文字动画的成片时走这一节，不走上面的盘点和方案卡。下面 `S` 指本 skill 自己的 `scripts` 目录（全局安装时是 `~/.claude/skills/carl-video-cut/scripts`，装在别处按实际位置），`W` 指工程目录。

依赖：macOS（人脸检测和繁简转换用系统自带的 Vision 和 ICU，`swift` 随 Xcode 命令行工具装好）；whisper.cpp 的 `whisper-cli` 加一个中文模型（默认 `~/.local/share/whisper.cpp/models/ggml-small.bin`，全在本机跑，不调付费接口）；ffmpeg、Node 22+、Python 3。缺哪样先告诉用户怎么装，得到同意再装。

1. **源归一和人声链**：`python3 S/normalize.py <原片...> --out W/norm --canvas 1920x1080`，每个源按文件名（不含扩展名，下面记作 `<源名>`）出 `<源名>.mp4` 和 `<源名>.voice.wav`。看 `normalize_report.md`：HDR、Log、旋转、无色彩标签这类情况脚本会停下来问，问题转给用户，不替他选。
2. **稿子**：用户的分镜脚本表（镜号、口播、素材列）用 `python3 S/script_table.py <脚本.md> --out W` 拆出 `稿子.txt` 和 `镜号表.json`。只有纯稿子时，先整理成至少两列的 markdown 表（`| 镜号 | 口播 |`，一镜一行）再拆，后面的分镜卡要用镜号表。
3. **转写对位**：`node S/transcribe.mjs --src A=W/norm/<源名>.voice.wav --script W/稿子.txt --out W/timings.json`，多段录音就多给几个 `--src B=...`；模型不在默认位置时加 `--model <ggml 模型路径>`。
4. **分镜卡，停在这里等用户**：`python3 S/koubo_card.py --timings W/timings.json --shots W/镜号表.json --out W` 写出 `口播分镜卡.md`、`takes.json`、`rough.json`。把卡上剪掉的地方、每句留哪一遍、没说到的稿子和要用户定的事讲给他，他确认或改完 `takes.json` 再剪。
5. **剪口播**：`python3 S/cut_takes.py --timings W/timings.json --takes W/takes.json --src A=W/norm/<源名> --out W/cut`（`--src` 给归一产物的前缀，和第 3 步的源名对应）。再补剪一两轮，直到 refine_cut 报告没有新的可剪：

   ```bash
   node S/transcribe.mjs --src A=W/cut/cut.voice.wav --script W/稿子.txt --out W/cut/verify/timings.json
   python3 S/refine_cut.py --takes W/takes.json --cut W/cut --verify W/cut/verify/timings.json
   # 有新增就重跑上面的 cut_takes.py，再从重转写开始
   ```

   要给用户对素材位置时出一版粗剪预览：`python3 S/rough_preview.py --cut W/cut --shots W/镜号表.json --rough W/rough.json --out W/粗剪预览.mp4`。
6. **大剪辑合成**：建 `W/motion/`，从 `templates/koubo/motion.config.example.js` 抄一份 `motion.config.js`，按分镜写场景、素材和文字动画，参数全部按口播锚定。然后：

   ```bash
   swift S/lib/faces.swift W/cut/cut.mp4 0.5 > W/motion/faces.json
   node S/koubo_motion.mjs --dir W/motion
   npx --no-install hyperframes lint W/motion
   npx --no-install hyperframes snapshot W/motion --at <几处关键秒> --no-end --output snap
   ```

   版式、素材选项、文字动画的写法和踩过的坑见 engines.md 口播大剪辑节。
7. **草稿和逐秒自检**：同上面第 5 步，另外对文字动画和换场时刻跑 `node S/check_determinism.mjs --dir <页面副本> --times ...`，必须逐像素一致。副本是把 `W/motion` 复制一份，在 `index.html` 的 `</style>` 前加一行 `video { visibility: hidden !important; }`：真人和素材视频由渲染器抽帧，这一步只比动效层。
8. **终版**：`HF_CAPTURE_PARALLEL_STREAM=true npx hyperframes render W/motion --quality delivery --output final-raw.mp4`，`S/koubo_mux.sh final-raw.mp4 W/cut/cut.voice.wav final.mov` 配口播人声（结尾垫素材原声加 `--bed`），`S/export_1080p.sh final.mov <片名>-1080p.mp4` 出终版，probe 必须 PASS。字幕不在画面里烧，交给用户自己的字幕工具。

完成标准：分镜卡经用户确认；终版 probe PASS；文字动画和换场时刻确定性检查通过；交付时给出成片路径、probe 输出、`timing.txt`（每一场落在第几秒）和可改参数说明。

## 改参协议

用户说一句修改，就只改 `motion.config.js` 里对应的键，再跑 sync、草稿、逐秒自检、终版。常见映射：

| 用户说 | 改哪里 |
|---|---|
| 第二幕放慢一点 | `scenes[1].dur` 乘 1.25（说多慢就按他说的） |
| 这个数字改成 18% | `numbers.<键>` 里对应的 `old` / `new`（或单值 `value`），单位在 `unit` |
| 某句文案换掉 | `scenes[i].text.<键>` |
| 换强调色 | `theme.accent` |
| 整体快一点 | 每幕 `dur` 同乘一个系数，不碰 `beats` |

完成标准：回复里贴出 config 的改动 diff，确认除了这些键没有任何文件被手改，并附新草稿受影响秒数的联系表。

## 硬规则

和市面上同类片拉开差距的三条，每支片都要做到：

- **先亮真实案例。** 开头几秒就让观众看到项目里真实存在的作品、作者和出处（真实媒体、真实链接、真实返回），再讲功能。
- **卡点。** 切点和关键动作落在音乐拍点上。音乐可以代码合成，也可以选无版权曲库的真实曲子，选曲、拍点、授权见 [audio.md](references/audio.md)。
- **仿 3D 但守住 2D。** 动中立体，停时平面：3D 只用在转场和媒体平面上，每一幕停住的那一刻回到正对镜头、对齐网格的平面版式。做法见 style.md 仿 3D 节。

- 素材只来自项目本身。产品界面只能裁切、放大、动画真实截图；示意性画面在画面上标注示意。
- 每一帧只由时间 t 决定：随机数带固定种子，不用 `Date.now`、CSS 动画、计时器。
- 换画幅时按新画幅重排文字和界面，16:9 不直接裁成 9:16。
- 视觉遵守 [style.md](references/style.md)：单强调色、0px 圆角、1px 线、Swiss grid，不用蓝紫渐变。
