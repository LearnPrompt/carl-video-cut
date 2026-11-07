# 测试

两组：确定性检查的反例（静态页面），和口播流程的合成夹具（真实跑 TTS、源归一、转写，一直到合成和 lint）。下面的命令在 skill 目录里跑，`F` 是任意一个临时目录。

## 确定性检查

`tests/determinism/` 里三个页面各故意犯一种错（CSS 动画、未播种的随机数、跨帧残留状态），检查必须报 FAIL；模板页面必须 PASS。

```bash
for b in bad_css bad_random bad_state; do cp -R tests/determinism/$b $F/$b; node scripts/check_determinism.mjs --dir $F/$b --times 0.5,1.2,2.4; done
cp -R templates/hyperframes $F/tpl; node scripts/check_determinism.mjs --dir $F/tpl --times 0.5,1.5,3.0,5.0
```

## Chrome 启动与中途故障

`tests/cdp/check_launch.mjs` 用假 Chrome（`tests/cdp/fake_chrome.mjs`）跑四种故障：马上退出、不给调试地址、连上后退出、连上后不回。每种都必须在时限内报 `DETERMINISM ERROR`、退出码 2，假 Chrome 和临时 profile 都要回收。之后用真 Chrome 跑正常路径，再跑一个 1 秒后死循环的页面（拿到页面之后才超时，进程退出时也必须把 Chrome 带走）；macOS 上再套一层 `sandbox-exec` 外层沙箱，要求要么通过、要么 30 秒内报错，最后在外层沙箱里显式设 `CVC_CHROME_FLAGS=--no-sandbox` 必须通过。`--no-real` 只跑假 Chrome。被测进程的 `TMPDIR` 指到测试自己的临时目录，回收检查不受机器上别的 Chrome 影响。

profile 清理另有一组故障注入，都要求不留进程、不留目录，或者明确报错：

- 假 Chrome 带一个同 `--user-data-dir` 的子进程（像 Chrome Helper），主进程退出后还往 profile 里写 0.8 秒、或一直写不退；
- 同上一直写不退，旁边再起一个独立进程占着前缀相同的 profile（`<这次的 profile>-decoy`），清理完它必须还活着、目录完整；
- profile 里有个目录暂时只读（0.5 秒后由不相关的进程改回来），要靠重试删掉；
- 一直只读，必须报 `临时 profile 清理失败：<路径>（原因）`，原故障的报错也要保留；
- PATH 里的 `pgrep` 总是出错，不能当作没有进程，同样要报清理失败；
- 真 Chrome 外面包一层带晚写子进程的壳，跑正常路径和页面死循环（后者走进程退出钩子）；
- 真 Chrome 正常路径，但 profile 一直删不掉：逐像素检查本身通过也不能报 PASS，要退出码 2 写明路径。

```bash
node tests/cdp/check_launch.mjs
```

## 口播夹具（P1 源归一、P2 转写对位）

`utterances.txt` 是测试句，不含私人内容。TTS 合成后拼成三段假录音，句界真值精确已知，录音里埋了半句口误、整句重录、稿外插话、长短停顿、句内重说，画面带 VFR、10bit HEVC、音画偏移和几种边界源（HLG、无色彩标签、旋转、疑似 Log）。

```bash
python3 scripts/tts_yunxi.py --in tests/koubo_fixture/utterances.txt --out $F --gap 0      # 必须 --gap 0：真值按发声起止算
uv run --no-project --with numpy python tests/koubo_fixture/compose_fixture.py --aud $F/aud --out $F/src
python3 scripts/normalize.py $F/src/A.mov $F/src/B.mp4 $F/src/C.mp4 --out $F/norm --canvas 1920x1080
uv run --no-project --with numpy python tests/koubo_fixture/check_p1.py --src $F/src --norm $F/norm
node scripts/transcribe.mjs --src A=$F/norm/A.voice.wav --src B=$F/norm/B.voice.wav --src C=$F/norm/C.voice.wav \
  --script tests/koubo_fixture/稿子.txt --out $F/proj/timings.json
python3 tests/koubo_fixture/check_p2.py --truth $F/src/truth.json --timings $F/proj/timings.json
```

需要：edge-tts（找不到时脚本会用 `uvx edge-tts`，会联网调用微软的 TTS 服务合成测试句）、whisper.cpp 的 `whisper-cli` 和 `ggml-small.bin`、numpy（只有夹具脚本要，用 uv 临时装）。

## 口播全链路

一条命令把上面两步接着往后跑：分镜卡（照稿模式）、按遍剪、剪后重转写加 refine_cut 复核、人脸检测、按示例参数改成夹具素材后合成、HyperFrames lint。每一步打印一行结果，最后一行是 ALL DONE，或者停在哪一步。夹具画面里没有人脸，人脸那步应该全是 null 且不报错。lint 要 `hyperframes@0.8.181`，第一次会从 npm 下载。

```bash
bash tests/koubo_fixture/run_all.sh $F
```

## 人像覆盖

`tests/presence/check_presence.mjs` 先用合成分镜直接测 `scripts/lib/presence.mjs`：8 场全屏首尾相接要报一条连续不见真人、改成 pip 不报、20 秒上限的边界、宫格并入不见段、脸旁大字在 pip、side、split、seek、beats、text 里要报而在 talk 和 full 加 card 里不报；再造一个最小口播工程（ffmpeg 合成的占位画面、三句的 cutmap）跑 `koubo_motion.mjs`，全屏 25 秒那版 timing.txt 要写出警告，改成 pip 后要无警告。不联网，只要 node 和 ffmpeg（含 ffprobe）。按版式算，整屏字卡和章节字卡盖住真人的那一两秒不计。

```bash
node tests/presence/check_presence.mjs
```

## 0.2.2 实跑（2026-11-07，macOS，Chrome 正式版，Node 25.6）

- 起因：0.2.1 发布后一轮实跑里 `真：正常路径` 退出码 0、检查 PASS，但留下了临时 profile。之后约 220 次插桩诊断没再出现，原始那次的具体时序没有查明。可以确定的是旧的清理有几处会静默留下：主进程退出后不管还活着的 Helper、删目录失败被吞掉、强杀后不等进程真的退出。
- 同一份测试在 0.2.1 的 `cdp.mjs` 上跑了两遍，新增的 9 项两遍都 FAIL：子进程晚写和不退留下进程；删除暂时失败留下目录；删除一直失败、列不出进程时没有报错；真 Chrome 正常路径加晚写退出码 0、PASS，却留下两个 profile 和一个进程；正常路径删不掉时也报 PASS。原有 8 项第二遍全 PASS；第一遍里没加任何注入的页面死循环、外层沙箱两项也各留下了 Chrome 进程（2 个和 1 个）。
- 修后同一份测试 17 项全 PASS，连跑三遍。子进程晚写或不退 2.2 到 2.7s 收回，旁边独立 profile 的进程活着、目录完整。暂时只读 0.8 到 1.1s 删掉，一直只读 2.3 到 2.6s 报清理失败。真 Chrome 正常路径删不掉时 3.6 到 3.9s 以退出码 2 结束并写明路径。外层 `sandbox-exec` 两项仍按原要求通过：macOS 的 `ps` 是 setuid，在沙箱里起不来，所以列进程用 `pgrep`。
- 确定性：bad_css、bad_random、bad_state FAIL 3/3、退出码 1；hyperframes、selfrender 模板 PASS、退出码 0，跑完没有留下 profile。
- 人像覆盖：`check_presence.mjs` 19 项全 PASS（#2 合入的改动）。口播全链路这次没重跑。

## 0.2.1 实跑（2026-11-07，macOS，Chrome 正式版，Node 25.6）

- Chrome 启动与中途故障：`check_launch.mjs` 8 项全 PASS。假 Chrome 马上退出 0.3s、不给调试地址 10.1s、连上后退出 0.2s、连上后不回 5.3s（`CVC_CDP_TIMEOUT=3`），都是退出码 2，假进程和 profile 都回收了；真 Chrome 正常路径通过，页面死循环 4.1s 报错且没有留下 Chrome 进程；外层 `sandbox-exec` 里 1.9s 报 `Chrome 退出（SIGTRAP）`，输出里有 `sandbox initialization failed: Operation not permitted`；同一外层沙箱里显式 `CVC_CHROME_FLAGS=--no-sandbox` 通过。修之前，连上后退出、连上后不回、外层沙箱三种都一直等到被外部超时杀掉，不给调试地址那种报错后留下 Chrome 进程。
- 确定性：bad_css、bad_random、bad_state 仍是 FAIL 3/3、退出码 1；hyperframes 模板和 selfrender 模板 PASS、退出码 0。
- 口播全链路不经过 Chrome 驱动，这次没改；它在 0.2.0（同一天）上跑过一遍 ALL DONE：P2 平均误差 8ms、最大 61ms，lint 0 error。

## 最近一次实跑（2026-10-27，macOS，whisper ggml-small）

- 确定性：bad_css、bad_random、bad_state 都是 FAIL 3/3；模板 PASS（1 个时刻逐像素一致，3 个只有不超过 2 色阶的栅格噪声）。
- P1：A、B 两段 CFR30、bt709、音画时长差 0.0ms 全部 PASS；A 的故意偏移测得 +39.33ms（期望 +40ms），人声链延迟 -0.02ms。P1 ALL PASS。
- P2：run_all.sh 那次全部句界在 100ms 容差内，平均误差 7ms，最大 43ms，P2 ALL PASS。同一天早些时候单独跑的一次有一句没过：A 里的半句口误，TTS 读的写视频被 whisper 听成同音的显示屏，单独成了一个 3 字的稿外小段，半句的结尾因此早了 0.3 秒。它在照稿模式下本来就会被剪掉（半句重说），不影响成片；稿外小段默认也不留。那次还抓到 whisper 把提示词原样吐成识别结果，transcribe.mjs 已改成遇到提示词回声就去掉提示词重转那一块。
- 全链路：分镜卡 takes 16 条，留 10 剪 6；剪好的口播 26.133 秒；剪后复核没有新问题（refine_cut 报 0 处）；人脸 53 帧检测到 0 帧，没报错；合成出 5 场、4 段视频、时长 27.133 秒、找不到的锚点 0 个；lint 0 个错误。ALL DONE。

edge-tts 服务端的合成结果会随时间变化，P2 在不同日期的实跑结果可能不完全一样，以每次实跑的输出为准。
