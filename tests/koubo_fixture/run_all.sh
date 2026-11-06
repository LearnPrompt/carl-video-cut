#!/usr/bin/env bash
# 口播流程全链路夹具：TTS 合成 → 拼假录音 → P1 源归一 → P2 转写对位 → 分镜卡 → 按遍剪 → 剪后复核 → 人脸 → 合成 → lint。
#   bash tests/koubo_fixture/run_all.sh <临时目录>
# 需要：edge-tts（或 uvx）、uv（夹具脚本临时装 numpy）、whisper-cli + ggml-small.bin、ffmpeg、swift（macOS）、Node 22+、
# 合成那步要 hyperframes@0.8.181（第一次会从 npm 下载）。每一步打印一行结果，最后一行是 ALL DONE 或停在哪一步。
set -uo pipefail
[ $# -ge 1 ] || { echo "usage: run_all.sh <临时目录>" >&2; exit 2; }
F="$1"; mkdir -p "$F"; F="$(cd "$F" && pwd)"
S="$(cd "$(dirname "$0")/../.." && pwd)"
T="$S/tests/koubo_fixture"
step() { echo; echo "== $*"; }
die() { echo "STOP：$*"; exit 1; }
py() { uv run -q --no-project --with numpy python "$@"; }

step "TTS 合成（--gap 0：真值按发声起止算）"
rm -rf "$F/aud" "$F/src" "$F/norm" "$F/proj" && mkdir -p "$F/proj"
python3 "$S/scripts/tts_yunxi.py" --in "$T/utterances.txt" --out "$F" --gap 0 >/dev/null 2>&1 || die "tts_yunxi.py"
ls "$F/aud"/*.wav | wc -l | sed 's/^ */句数 /'

step "拼假录音"
py "$T/compose_fixture.py" --aud "$F/aud" --out "$F/src" 2>&1 | tail -1

step "P1 源归一 + 人声链"
python3 "$S/scripts/normalize.py" "$F/src/A.mov" "$F/src/B.mp4" "$F/src/C.mp4" --out "$F/norm" --canvas 1920x1080 >/dev/null 2>&1 || die "normalize.py"
py "$T/check_p1.py" --src "$F/src" --norm "$F/norm" 2>&1 | tail -1

step "P2 转写对位"
node "$S/scripts/transcribe.mjs" --src A="$F/norm/A.voice.wav" --src B="$F/norm/B.voice.wav" --src C="$F/norm/C.voice.wav" \
  --script "$T/稿子.txt" --out "$F/proj/timings.json" 2>&1 | grep -E "^\[(A|B|C|info)\]" || die "transcribe.mjs"
python3 "$T/check_p2.py" --truth "$F/src/truth.json" --timings "$F/proj/timings.json" 2>&1 | tail -2

step "分镜卡（照稿模式）"
{ echo "| 镜号 | 口播 |"; echo "|---|---|"; n=0; while IFS= read -r l; do n=$((n+1)); [ -n "$l" ] && echo "| $n | $l |"; done < "$T/稿子.txt"; } > "$F/proj/脚本.md"
python3 "$S/scripts/script_table.py" "$F/proj/脚本.md" --out "$F/proj" >/dev/null 2>&1 || die "script_table.py"
python3 "$S/scripts/koubo_card.py" --timings "$F/proj/timings.json" --shots "$F/proj/镜号表.json" --out "$F/proj" --mode scripted >/dev/null 2>&1 || die "koubo_card.py"
python3 - "$F/proj/takes.json" <<'PY'
import json, sys
t = json.load(open(sys.argv[1])); t = t.get('takes', t)
print(f"takes {len(t)} 条，留 {sum(1 for x in t if x.get('keep'))}，剪 {sum(1 for x in t if not x.get('keep'))}")
PY

step "按遍剪"
python3 "$S/scripts/cut_takes.py" --timings "$F/proj/timings.json" --takes "$F/proj/takes.json" \
  --src A="$F/norm/A" --src B="$F/norm/B" --src C="$F/norm/C" --out "$F/proj/cut" >/dev/null 2>&1 || die "cut_takes.py"
for f in cut.mp4 cut.voice.wav cutmap.json; do [ -s "$F/proj/cut/$f" ] || die "缺 cut/$f"; done
echo "成片口播 $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$F/proj/cut/cut.mp4") 秒"

step "剪后复核（重转写 + refine_cut --dry-run）"
node "$S/scripts/transcribe.mjs" --src A="$F/proj/cut/cut.voice.wav" --script "$T/稿子.txt" --out "$F/proj/cut/verify/timings.json" >/dev/null 2>&1 || die "复核转写"
python3 "$S/scripts/refine_cut.py" --takes "$F/proj/takes.json" --cut "$F/proj/cut" --verify "$F/proj/cut/verify/timings.json" --dry-run 2>&1 | tail -1

step "人脸检测（夹具画面里没有人脸，应全为 null 且不报错）"
mkdir -p "$F/proj/motion"
swift "$S/scripts/lib/faces.swift" "$F/proj/cut/cut.mp4" 0.5 > "$F/proj/motion/faces.json" 2>/dev/null || die "faces.swift"
python3 -c "import json,sys; j=json.load(open(sys.argv[1])); fr=j['frames']; print(f'{len(fr)} 帧，检测到脸 {sum(1 for x in fr if x[\"box\"])} 帧')" "$F/proj/motion/faces.json"

step "合成（示例参数改成夹具素材）"
ffmpeg -v error -y -i "$F/src/C.mp4" -frames:v 1 "$F/proj/shot.png"
cat > "$F/proj/motion/motion.config.js" <<EOF
window.MOTION = {
  meta: { width: 1920, height: 1080, fps: 30, title: '夹具', tail: 1 },
  theme: { bg: '#0e0f12', ink: '#EDEAE4', accent: '#C4482A', font: '"PingFang SC", sans-serif', mono: 'Menlo, monospace' },
  brand: { pattern: { bg: '#1c1c1e', word: '#3a3a3c', solid: 'Your', outline: 'NAME', size: 150, rowH: 172, gap: 0.62, stroke: 2 }, drift: { loop: 60 } },
  source: { cut: '../cut', faces: 'faces.json', assetsRoot: '$F' },
  media: {
    b: { src: 'src/B.mp4', by: '@夹具' },
    c: { src: 'src/C.mp4', by: '@夹具', crop: [0, 0, 1280, 720] },
    shot: { src: 'proj/shot.png', by: '夹具', img: true },
  },
  parts: [{ from: { t: 0 }, tag: '夹具 · 全链路' }, { from: { line: 4 }, tag: 'PART 1 · 第一章' }],
  scenes: [
    { id: 's01', from: { line: 1 }, layout: 'talk', pops: [{ style: 'card', text: '提示词只占\n一成', hl: ['一成'], dur: 1.6 }] },
    { id: 's02', from: { line: 3 }, layout: 'pip', kicker: '小标题', title: '素材卡加小窗', items: [{ m: 'b', in: 1 }],
      chips: [{ text: '素材', at: { line: 3 } }, { text: '分镜', at: { line: 3, p: '分镜' } }] },
    { id: 's03', from: { line: 4 }, layout: 'talk', pops: [{ num: '01', kicker: '第一步', text: '先盘点素材', hl: ['盘点'] }] },
    { id: 's04', from: { line: 6 }, layout: 'grid', cols: 2, items: [{ m: 'c', in: 0 }, { m: 'shot' }] },
    { id: 's05', from: { line: 8 }, layout: 'full', card: true, items: [{ m: 'b', in: 4, label: '全屏卡片' }] },
  ],
};
EOF
( cd "$F/proj/motion" && node "$S/scripts/koubo_motion.mjs" --dir . 2>&1 | head -1 ) || die "koubo_motion.mjs"
[ -s "$F/proj/motion/index.html" ] && [ -s "$F/proj/motion/timing.txt" ] || die "合成没写出 index.html / timing.txt"
npx -y hyperframes@0.8.181 lint "$F/proj/motion" 2>&1 | tail -1

echo; echo "ALL DONE"
