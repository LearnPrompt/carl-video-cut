#!/usr/bin/env bash
# export_1080p.sh <in> <out.mp4> [--size 1920x1080|1080x1920] [--fps 30|60] [--lufs -14] [--tp -1.2] [--no-loudnorm]
# 交付版导出：目标画幅（保持比例，pad 黑边不裁切）、x264 slow crf16、yuv420p、bt709 三件套、+faststart。
# 有音轨时先过两遍 loudnorm（mix_audio.py --in，I -14 / TP -1.2，在 AAC 编码之前），再 aac 192k 48kHz；
# 编码后实测真峰值，AAC 上冲超标就把目标峰值再收紧重导一次。没有音轨就不带音轨。
# 完成后自动 probe.sh --size <画幅> --color bt709，必须 PASS。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IN=""; OUT=""; FPS=""; SIZE="1920x1080"; LUFS="-14"; TP="-1.2"; LOUD=1
USAGE="usage: export_1080p.sh <in> <out.mp4> [--size 1920x1080|1080x1920] [--fps 30|60] [--lufs -14] [--tp -1.2] [--no-loudnorm]"
while [ $# -gt 0 ]; do
  case "$1" in
    --fps) FPS="$2"; shift 2;;
    --size) SIZE="$2"; shift 2;;
    --lufs) LUFS="$2"; shift 2;;
    --tp) TP="$2"; shift 2;;
    --no-loudnorm) LOUD=0; shift;;
    -h|--help) echo "$USAGE" >&2; exit 2;;
    *) if [ -z "$IN" ]; then IN="$1"; elif [ -z "$OUT" ]; then OUT="$1"; fi; shift;;
  esac
done
[ -f "$IN" ] && [ -n "$OUT" ] || { echo "$USAGE" >&2; exit 2; }
[[ "$SIZE" =~ ^([0-9]+)x([0-9]+)$ ]] || { echo "--size 写成 1920x1080 或 1080x1920" >&2; exit 2; }
W="${BASH_REMATCH[1]}"; H="${BASH_REMATCH[2]}"
mkdir -p "$(dirname "$OUT")"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# scale 带 out_color_matrix=bt709，保证 RGB/未标记源转 YUV 时矩阵正确；setparams 打色彩标签（ffmpeg 8 里命令行选项会被帧属性盖掉）
VF="scale=${W}:${H}:force_original_aspect_ratio=decrease:flags=lanczos+accurate_rnd:out_color_matrix=bt709:out_range=tv,format=yuv420p,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
[ -n "$FPS" ] && VF="fps=${FPS},${VF}"

HAS_AUDIO="$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_type -of csv=p=0 "$IN" | head -1)"
VOPTS=(-c:v libx264 -preset slow -crf 16 -pix_fmt yuv420p -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv)

if [ -z "$HAS_AUDIO" ]; then
  ffmpeg -v error -stats -y -i "$IN" -map 0:v:0 -an -vf "$VF" "${VOPTS[@]}" -movflags +faststart "$OUT"
elif [ "$LOUD" = 0 ]; then
  ffmpeg -v error -stats -y -i "$IN" -map 0:v:0 -map 0:a:0 -vf "$VF" "${VOPTS[@]}" -c:a aac -b:a 192k -ar 48000 -movflags +faststart "$OUT"
else
  # 画面只编一次；声音先做响度，AAC 编码后实测真峰值，超标就把目标峰值收紧（超多少收多少再多 0.1）重做声音
  ffmpeg -v error -stats -y -i "$IN" -map 0:v:0 -an -vf "$VF" "${VOPTS[@]}" "$TMP/v.mp4"
  VD="$(ffprobe -v error -select_streams v:0 -show_entries stream=duration -of csv=p=0 "$TMP/v.mp4")"
  T="$TP"
  for k in 1 2 3; do
    python3 "$HERE/mix_audio.py" --in "$IN" --out "$TMP/a.wav" --lufs "$LUFS" --tp "$T" --dur "$VD" > "$TMP/loud.json"
    ffmpeg -v error -y -i "$TMP/a.wav" -c:a aac -b:a 192k -ar 48000 "$TMP/a.m4a"
    MTP="$(ffmpeg -v info -nostats -i "$TMP/a.m4a" -af ebur128=peak=true -f null - 2>&1 | sed -n '/Summary:/,$p' | sed -n 's/^ *Peak: *\(-*[0-9.inf]*\) dBFS.*/\1/p' | tail -1)"
    echo "loudness pass $k: 目标峰值 $T → AAC 后实测 ${MTP} dBTP"
    awk -v p="$MTP" -v t="$TP" 'BEGIN{exit !(p <= t)}' && break
    T="$(awk -v t="$T" -v p="$MTP" -v lim="$TP" 'BEGIN{printf "%.2f", t - (p - lim) - 0.1}')"
  done
  ffmpeg -v error -y -i "$TMP/v.mp4" -i "$TMP/a.m4a" -map 0:v:0 -map 1:a:0 -c copy -movflags +faststart "$OUT"
fi

echo "exported: $OUT"
"$HERE/probe.sh" "$OUT" --size "$SIZE" --color bt709 ${FPS:+--fps "$FPS"} $([ -n "$HAS_AUDIO" ] && [ "$LOUD" = 1 ] && echo "--lufs $LUFS --tp $TP")
