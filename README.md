# 大剪辑 carl-video-cut

```bash
npx skills add LearnPrompt/carl-video-cut -a claude-code -g
```

`-g` 装到 `~/.claude/skills/`，skill 里的命令都按这个位置写。不用 npx 也行：`git clone https://github.com/LearnPrompt/carl-video-cut ~/.claude/skills/carl-video-cut`

## 中文

大剪辑是一个 Claude Code skill，把你录好的口播、项目文档、录屏或讲解稿，做成代码渲染的 1080p 成片。每一帧都由代码按时间算出来，所以成片背后是一份能改的工程：以后想把第二幕放慢、把某个数字改成 18%，只动 `motion.config.js` 里的一个参数。

**真人口播大剪辑**是 0.2 新加的片型。你录一段口播，给它稿子和素材，对 Claude 说：

> 用大剪辑，把这段口播和素材剪成成片

它先转写对位，按你的录法判断每句留哪一遍、哪里是重说要剪掉，写成一张分镜卡给你确认；确认后剪好口播，再把素材卡、你的人像小窗、章节角标和概念文字动画（整屏字卡、脸旁大字、重点词橙底、说到哪个词就把它划掉）按你说话的时刻合成上去。所有出现时刻都锚在你说的话上，重剪口播以后画面自动跟着走。交付前会逐秒抽帧自检、做确定性检查，终版两遍响度归一到 -14 LUFS 并跑 probe。作者用自己的一支 8 分 32 秒教程口播从原片走到了成片（素材不随仓库发布）；仓库里的合成夹具 `tests/koubo_fixture/run_all.sh` 能从 TTS 假录音一路跑到合成和 lint，见 [tests/README.md](tests/README.md)。

项目动效、3B1B 式讲解片、录屏重剪、合集拼接和变速这几种 0.1 就有的片型照旧可用：对 Claude 说用大剪辑给这个项目做动效视频，它会先盘点素材、给出按实用度排好的分镜方案，你选定后才写代码。

字幕不烧进画面，成片交给你自己的字幕工具（比如剪映智能识别）。工程请用一个新目录，脚本会覆盖目录里的同名输出。

### 依赖

只在 macOS 上验证过（ffmpeg 8.1.1、whisper.cpp 1.9.5、Swift 6.4、Node 25.6、Python 3.9.6）。要求 Node 22 起、Python 3.9 起，还要装好 Google Chrome。口播片型离不开 macOS：人脸检测和繁简转换用的是系统自带的 Vision 和 ICU。其他片型理论上能在别的系统跑，没测过；确定性检查默认找 macOS 的 Chrome，装在别处时设 `CVC_CHROME=<Chrome 可执行文件>`。

```bash
xcode-select --install                     # swift
brew install ffmpeg whisper-cpp node       # whisper-cli 要支持 -dtw 的版本
python3 -m pip install pillow              # 粗剪预览和联系表
mkdir -p ~/.local/share/whisper.cpp/models
curl -L -o ~/.local/share/whisper.cpp/models/ggml-small.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin
```

转写全在本机跑，不调用付费接口。HyperFrames 首次使用时会先征求你同意再从 npm 下载。讲解片需要 manim 0.21 和 edge-tts（edge-tts 调用的是微软的在线 TTS 服务）。跑测试还需要 uv。

## English

carl-video-cut is a Claude Code skill that turns your recorded talking-head footage, project docs, screen recordings or explainer scripts into code-rendered 1080p videos. Every frame is computed from time by code, so the video is backed by an editable project: later changes like "slow down scene 2" or "change this number to 18%" touch a single key in `motion.config.js`.

**Talking-head editing** is new in 0.2. Record yourself, hand over the script and your media, and tell Claude:

> Use carl-video-cut to edit this talking-head recording with these materials

It transcribes and aligns the recording to your script, decides which take of each line to keep and which restarts to cut, and writes a shot card for you to confirm. Then it cuts the voice track and composes media cards, a picture-in-picture of you, chapter tags and kinetic text (full-screen statement cards, big text beside your face, highlighted keywords, words struck out the moment you say them), all anchored to what you say, so a re-cut moves the visuals with it. Before delivery it checks one frame per second, runs a determinism check, normalizes loudness to -14 LUFS in two passes and runs a probe. The author took an 8 min 32 s tutorial of their own from raw footage to final cut (the footage is not published); the bundled synthetic fixture `tests/koubo_fixture/run_all.sh` runs from TTS recordings all the way through composition and lint, see [tests/README.md](tests/README.md).

The 0.1 formats still work: project motion videos, 3B1B-style explainers, screen-recording re-edits, compilations and speed changes. Ask Claude to make a motion video for a project and it audits the material, proposes storyboards ranked by usefulness, and writes code only after you pick one.

Subtitles are not burned in; use your own captioning tool on the final video. Use a fresh project directory, since the scripts overwrite same-named outputs.

### Requirements

Verified on macOS only (ffmpeg 8.1.1, whisper.cpp 1.9.5, Swift 6.4, Node 25.6, Python 3.9.6). Requires Node 22+, Python 3.9+ and Google Chrome. The talking-head format needs macOS: face detection and Traditional-to-Simplified conversion use the built-in Vision and ICU. Other formats may work elsewhere but are untested; the determinism check looks for macOS Chrome by default, set `CVC_CHROME=<chrome binary>` otherwise. Install commands are in the Chinese section above.

Transcription runs fully locally with no paid API. HyperFrames is downloaded from npm only with your consent. Explainers need manim 0.21 and edge-tts (which calls Microsoft's online TTS service). Running the tests also needs uv.

## 致谢 Credits

- Claude Motion 官方指南 / Get started with Claude Motion: https://support.claude.com/en/articles/17454997-get-started-with-claude-motion
- HyperFrames (HeyGen, Apache-2.0): https://github.com/heygen-com/hyperframes
- whisper.cpp (MIT): https://github.com/ggml-org/whisper.cpp
- EianLu 在 X 上的 Opus 5.5 动效视频指南（八步法，提示词一成、流程九成），以及 eian-riso-broll 的 B-roll 思路（只借思路，未用其代码）
- xilo2991 的 Opus 5.5 做视频万字拆解与开源 Skill: https://github.com/Kianzzz/xilo-opus-video
- 0xMovez 的十二步代码动画工作室课程（seek 渲染器、contact sheet 校验）
- GoSailGlobal 整理的 962 个 Opus 5.5 视频作品库
- 作者自己的实战工程：AI-MAP 七大功能、AI 做小程序 10 秒动画、画幅宣传片、三支 3B1B 风格讲解片、Opus 5.5 代码动画教程口播大剪辑

## License

MIT
