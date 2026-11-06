# 测试

两组：确定性检查的反例（静态页面），和口播流程的合成夹具（真实跑 TTS、源归一、转写，一直到合成和 lint）。下面的命令在 skill 目录里跑，`F` 是任意一个临时目录。

## 确定性检查

`tests/determinism/` 里三个页面各故意犯一种错（CSS 动画、未播种的随机数、跨帧残留状态），检查必须报 FAIL；模板页面必须 PASS。

```bash
for b in bad_css bad_random bad_state; do cp -R tests/determinism/$b $F/$b; node scripts/check_determinism.mjs --dir $F/$b --times 0.5,1.2,2.4; done
cp -R templates/hyperframes $F/tpl; node scripts/check_determinism.mjs --dir $F/tpl --times 0.5,1.5,3.0,5.0
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

## 最近一次实跑（2026-10-27，macOS，whisper ggml-small）

- 确定性：bad_css、bad_random、bad_state 都是 FAIL 3/3；模板 PASS（1 个时刻逐像素一致，3 个只有不超过 2 色阶的栅格噪声）。
- P1：A、B 两段 CFR30、bt709、音画时长差 0.0ms 全部 PASS；A 的故意偏移测得 +39.33ms（期望 +40ms），人声链延迟 -0.02ms。P1 ALL PASS。
- P2：run_all.sh 那次全部句界在 100ms 容差内，平均误差 7ms，最大 43ms，P2 ALL PASS。同一天早些时候单独跑的一次有一句没过：A 里的半句口误，TTS 读的写视频被 whisper 听成同音的显示屏，单独成了一个 3 字的稿外小段，半句的结尾因此早了 0.3 秒。它在照稿模式下本来就会被剪掉（半句重说），不影响成片；稿外小段默认也不留。那次还抓到 whisper 把提示词原样吐成识别结果，transcribe.mjs 已改成遇到提示词回声就去掉提示词重转那一块。
- 全链路：分镜卡 takes 16 条，留 10 剪 6；剪好的口播 26.133 秒；剪后复核没有新问题（refine_cut 报 0 处）；人脸 53 帧检测到 0 帧，没报错；合成出 5 场、4 段视频、时长 27.133 秒、找不到的锚点 0 个；lint 0 个错误。ALL DONE。

edge-tts 服务端的合成结果会随时间变化，P2 在不同日期的实跑结果可能不完全一样，以每次实跑的输出为准。
