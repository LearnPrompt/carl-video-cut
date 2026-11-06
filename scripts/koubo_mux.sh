#!/usr/bin/env bash
# koubo_mux.sh <渲好的画面.mp4> <口播人声.wav> <out.mov> [--bed <素材> --bed-at <秒> [--bed-in <秒>] [--under 0.22] [--up 0.7]]
# 口播大剪辑配声音：画面原样拷贝，口播人声（cut/cut.voice.wav，人声链之后）为主，补到画面时长。
# 结尾要垫素材原声时加 --bed：素材从 --bed-in 秒起，对齐到成片 --bed-at 秒（通常是结尾那一场的 t0，见 timing.txt），
# 人声还在说时压在 --under，人声说完抬到 --up，头尾各做淡入淡出。
# 输出 pcm_s24le 的 .mov，交付再走 export_1080p.sh（两遍 loudnorm 到 -14 LUFS、probe 必须 PASS）。
set -euo pipefail
USAGE="usage: koubo_mux.sh <render.mp4> <voice.wav> <out.mov> [--bed <media> --bed-at <sec> [--bed-in <sec>] [--under 0.22] [--up 0.7]]"
[ $# -ge 3 ] || { echo "$USAGE" >&2; exit 2; }
IN="$1"; VOICE="$2"; OUT="$3"; shift 3
BED=""; AT=""; BIN=0; UNDER=0.22; UP=0.7
while [ $# -gt 0 ]; do
  case "$1" in
    --bed) BED="$2"; shift 2;;
    --bed-at) AT="$2"; shift 2;;
    --bed-in) BIN="$2"; shift 2;;
    --under) UNDER="$2"; shift 2;;
    --up) UP="$2"; shift 2;;
    *) echo "$USAGE" >&2; exit 2;;
  esac
done
dur() { ffprobe -v error -show_entries format=duration -of csv=p=0 "$1"; }
TOTAL=$(dur "$IN")
VDUR=$(dur "$VOICE")
if [ -z "$BED" ]; then
  ffmpeg -v error -y -i "$IN" -i "$VOICE" -filter_complex \
    "[1:a]aformat=sample_rates=48000:channel_layouts=mono,apad=whole_dur=$TOTAL,aformat=channel_layouts=stereo[a]" \
    -map 0:v:0 -map "[a]" -c:v copy -c:a pcm_s24le -t "$TOTAL" "$OUT"
else
  [ -n "$AT" ] || { echo "--bed 需要同时给 --bed-at（素材对齐到成片第几秒）" >&2; exit 2; }
  BDUR=$(python3 -c "print(round($TOTAL-$AT,3))")
  MS=$(python3 -c "print(int(round($AT*1000)))")
  FO=$(python3 -c "print(round(max(0.0,$BDUR-0.6),3))")
  ffmpeg -v error -y -i "$IN" -i "$VOICE" -ss "$BIN" -t "$BDUR" -i "$BED" -filter_complex "
    [1:a]aformat=sample_rates=48000:channel_layouts=mono,apad=whole_dur=$TOTAL[v];
    [2:a]aformat=sample_rates=48000:channel_layouts=mono,volume='if(lt(t+$AT,$VDUR),$UNDER,$UP)':eval=frame,
         afade=t=in:st=0:d=0.3,afade=t=out:st=$FO:d=0.6,adelay=$MS:all=1,apad=whole_dur=$TOTAL[m];
    [v][m]amix=inputs=2:normalize=0:duration=first,aformat=channel_layouts=stereo[a]" \
    -map 0:v:0 -map "[a]" -c:v copy -c:a pcm_s24le -t "$TOTAL" "$OUT"
fi
echo "$OUT"
