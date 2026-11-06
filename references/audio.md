# 配音与配乐

项目动效默认无口播；需要声音时先问是配音、配乐还是都要。用户说要自己录口播时，交付一份口播对位表（每句对应的起止秒数），画面按对位表切。

## 配音：edge-tts 云希

```bash
python3 ~/.claude/skills/carl-video-cut/scripts/tts_yunxi.py --in narration.txt --out . --rate +5% --gap 0.35
```

`narration.txt` 每行 `key|整段文字`。脚本逐句合成，正反两遍 silenceremove 去掉首尾静音，句间补固定 0.35 秒，输出 `aud/<key>.wav` 和 `timings.json`（每段时长、每句起止）。画面一律按 `timings.json` 的句子起点对齐，配音改了重跑脚本，画面自动跟上。

- 声音默认 zh-CN-YunxiNeural，rate +5%。macOS say 的 Tingting 太机械，已经弃用。
- 脚本按 `--edge-tts`、`$EDGE_TTS`、`~/mvenv/bin`、PATH、`python3 -m edge_tts`、`uvx edge-tts` 的顺序找 edge-tts；都找不到时按 engines.md manim 节建 venv。
- 有配音的片子，每句字幕和配音同一个出处：narration.txt，不另写一份字幕稿。

## 选曲：无版权曲库

除了代码合成，也可以用真实曲子卡点，听感更好。来源优先 Mixkit 和 Pixabay Music，下载前把曲名、来源页、文件大小和许可要点给用户，得到同意再下。许可原文摘要写进工程的 `MUSIC.md`，交付说明里注明曲名、作者和许可。

- 选无人声、拍点清晰、BPM 110 到 130、开头 2 秒内就有拍子的曲子。
- 下载后先抽真实拍点：`npx hyperframes@<版本> beats`，或 librosa 的 beat_track，存成 `beats.json`。曲子 BPM 只当参考，以抽出来的拍点为准。
- 分镜时长按拍数写（`scenes[i].beats_len`），由拍点表换算成秒；切点、按钮按下、卡片落地、打字的每个字都对到拍或半拍上。
- 剪曲子：从一个强拍起剪，结尾落在乐句末的强拍，最后 0.5 秒淡出；时长不够就选段落而不是硬拉伸。

## 配乐：numpy 合成占位曲

```bash
python3 ~/.claude/skills/carl-video-cut/scripts/music_bed.py --dur 12 --bpm 120 --cuts "1.5,3,4.5,6" --out music.wav
```

A 小调 pad、琶音、贝斯、鼓组，切点上放 whoosh。脚本会把切点量化到最近的拍并提示偏差，偏差大就回去改分镜时长让切点落拍。时长比画面多留 1 秒接尾音。

- 用户自带音乐时，用 librosa 抽真实鼓点存成 `beats.json`，分镜时长按鼓点定，不按 BPM 估算。
- 有人声时配乐压低到人声下面，整体响度目标 -14 LUFS。`music_bed.py --lufs` 只是 RMS 近似，误差约 2 dB 且按峰值 0.95 封顶；准确的响度交给 `mix_audio.py`（见下面混音与响度节）。
- 程序合成的曲子在交付说明里标明是占位曲，可替换。

## 真人人声链

实拍口播的原声在 `normalize.py` 里处理一次，作用在整段源音频上：两级 80Hz 高通（单级在 50Hz 只压约 8 dB，压不住交流哼声）、afftdn 降噪 12 dB 并跟踪底噪、压缩器不补增益。转写和成片用的是同一份 `<名>.voice.wav`。afftdn 按块处理会让声音晚 25ms，脚本每次用脉冲现量这段延迟并裁掉，voice.wav 和画面严格对齐。处理前后各出一张全时长响度曲线和一张频谱图。

## 混音与响度

```bash
python3 ~/.claude/skills/carl-video-cut/scripts/mix_audio.py --out mix.wav --voice voice.wav --bgm bgm.wav [--sfx sfx.wav] --dur <片长>
```

人声、BGM、音效分轨进来。BGM 先降到 `--bgm-gain`（默认 -16 dB），再过侧链压缩：检测信号是人声（先按人声实测响度拉到 -18 LUFS，只作用在检测支路），人说话时 BGM 自动让路，夹具实测说话段压低约 4 到 7 dB，停顿处不动。最后两遍 loudnorm 到 I -14 LUFS、真峰值 -1.2 dBTP 以下。人声峰均比大，线性增益常会冲破峰值上限，这时 loudnorm 自动退到动态模式，脚本会提示，这是正常的。

已有成品音轨只做响度时用 `--in <音频或视频>`。`export_1080p.sh` 对有音轨的输入自动走这一步，不用手动混。

HyperFrames 工程也可以直接在合成里放 `<audio id="bgm" data-start="0" data-volume="0.6">`，由渲染器混音；导出时 `export_1080p.sh` 照样会统一响度。
