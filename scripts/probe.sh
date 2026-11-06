#!/usr/bin/env bash
# probe.sh <video> [--expect 1920x1080 | --size 1080x1920] [--fps N] [--color bt709] [--lufs -14] [--tp -1.2] [--quick]
# 打印时长、分辨率、帧率（CFR 还是 VFR）、编码、色彩标签、音轨、音画时长差、全片解码错误数、响度（I / LRA / 真峰值）。
# 给了 --expect（或同义的 --size）时按交付标准判：分辨率、CFR、音画时长差不超过 1 帧、全片解码无错、
# 有音轨时真峰值不超过 --tp（默认 -1.2 dBTP）；--fps 查帧率，--lufs 查 I 在 ±1 LU 内，--color 查三项色彩标签。
# --quick 跳过全片解码（长片省时间）。不匹配时逐条 FAIL 并 exit 1，全部匹配 PASS。
set -uo pipefail
VIDEO=""; EXPECT=""; EXFPS=""; EXCOLOR=""; EXLUFS=""; EXTP="-1.2"; QUICK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --expect|--size) EXPECT="$2"; shift 2;;
    --fps) EXFPS="$2"; shift 2;;
    --color) EXCOLOR="$2"; shift 2;;
    --lufs) EXLUFS="$2"; shift 2;;
    --tp) EXTP="$2"; shift 2;;
    --quick) QUICK=1; shift;;
    -h|--help) echo "usage: probe.sh <video> [--expect|--size WxH] [--fps N] [--color bt709] [--lufs -14] [--tp -1.2] [--quick]" >&2; exit 2;;
    *) VIDEO="$1"; shift;;
  esac
done
[ -n "$VIDEO" ] && [ -f "$VIDEO" ] || { echo "FAIL: 找不到视频 '$VIDEO'" >&2; exit 2; }

kv() { ffprobe -v error "$@" -of default=nw=1 "$VIDEO"; }
get() { printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -1; }
V="$(kv -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate,avg_frame_rate,pix_fmt,color_space,color_primaries,color_transfer,duration)"
CODEC="$(get "$V" codec_name)"; WIDTH="$(get "$V" width)"; HEIGHT="$(get "$V" height)"
RFR="$(get "$V" r_frame_rate)"; AFR="$(get "$V" avg_frame_rate)"; PIX="$(get "$V" pix_fmt)"
CSP="$(get "$V" color_space)"; CPR="$(get "$V" color_primaries)"; CTR="$(get "$V" color_transfer)"; VDUR="$(get "$V" duration)"
DUR="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$VIDEO")"
fr() { awk -v r="$1" 'BEGIN{split(r,a,"/"); if(a[2]==0||a[2]=="")a[2]=1; printf "%.3f", a[1]/a[2]}'; }
FPS="$(fr "$RFR")"; AVG="$(fr "${AFR:-0/1}")"
A="$(kv -select_streams a:0 -show_entries stream=codec_name,sample_rate,channels,duration)"
ADUR="$(get "$A" duration)"

printf 'file      : %s\n' "$VIDEO"
printf 'duration  : %.3f s\n' "$DUR"
if [ -z "$CODEC" ]; then
  WIDTH=0; HEIGHT=0; FPS=0; CFR=0
  echo 'video     : none'
else
  CFR=0; awk -v a="$FPS" -v b="$AVG" 'BEGIN{d=a-b; if(d<0)d=-d; exit !(d<0.01)}' && CFR=1
  printf 'size      : %sx%s\n' "$WIDTH" "$HEIGHT"
  printf 'fps       : %s  (avg %s, %s)\n' "$FPS" "$AVG" "$([ "$CFR" = 1 ] && echo CFR || echo VFR)"
  printf 'video     : %s  %s  (color: %s/%s/%s)\n' "$CODEC" "$PIX" "${CSP:-?}" "${CPR:-?}" "${CTR:-?}"
fi
if [ -n "$A" ]; then
  printf 'audio     : %s %sHz %sch\n' "$(get "$A" codec_name)" "$(get "$A" sample_rate)" "$(get "$A" channels)"
  AVDIFF="$(awk -v v="${VDUR:-$DUR}" -v a="${ADUR:-$DUR}" 'BEGIN{d=v-a; if(d<0)d=-d; printf "%.1f", d*1000}')"
  printf 'av diff   : %s ms  (video %.3f s / audio %.3f s)\n' "$AVDIFF" "${VDUR:-$DUR}" "${ADUR:-$DUR}"
  LOUD="$(ffmpeg -v info -nostats -i "$VIDEO" -map 0:a:0 -af ebur128=peak=true -f null - 2>&1 | sed -n '/Summary:/,$p')"
  LI="$(printf '%s\n' "$LOUD" | sed -n 's/^ *I: *\(-*[0-9.inf]*\) LUFS.*/\1/p' | head -1)"
  LRA="$(printf '%s\n' "$LOUD" | sed -n 's/^ *LRA: *\(-*[0-9.]*\) LU$/\1/p' | head -1)"
  LTP="$(printf '%s\n' "$LOUD" | sed -n 's/^ *Peak: *\(-*[0-9.inf]*\) dBFS.*/\1/p' | tail -1)"
  printf 'loudness  : I %s LUFS  LRA %s LU  true peak %s dBTP\n' "${LI:-?}" "${LRA:-?}" "${LTP:-?}"
else
  echo 'audio     : none'
fi
DECERR="skip"
if [ "$QUICK" = 0 ]; then
  DECERR="$(ffmpeg -v error -nostats -i "$VIDEO" -map 0 -f null - 2>&1 | grep -c . || true)"
  printf 'decode    : %s 条错误\n' "$DECERR"
fi

RC=0; REASONS=()
fail() { REASONS+=("$1"); RC=1; }
if [ -n "$EXPECT" ]; then
  [ "${WIDTH}x${HEIGHT}" = "$EXPECT" ] || fail "分辨率 ${WIDTH}x${HEIGHT} != 期望 $EXPECT"
  [ "$CFR" = 1 ] || fail "VFR：r_frame_rate $RFR 与 avg_frame_rate $AFR 不一致"
  if [ -n "$A" ]; then
    awk -v d="$AVDIFF" -v f="$FPS" 'BEGIN{exit !(d <= 1000/(f>0?f:30) + 0.5)}' || fail "音画时长差 ${AVDIFF}ms 超过 1 帧"
    awk -v p="${LTP:-0}" -v t="$EXTP" 'BEGIN{exit !(p <= t)}' || fail "真峰值 ${LTP} dBTP 超过 ${EXTP}"
  fi
  [ "$DECERR" = "skip" ] || [ "$DECERR" = 0 ] || fail "全片解码有 $DECERR 条错误"
fi
if [ -n "$EXFPS" ] && ! awk -v a="$FPS" -v b="$EXFPS" 'BEGIN{d=a-b; if(d<0)d=-d; exit !(d<0.01)}'; then
  fail "帧率 $FPS != 期望 $EXFPS"
fi
if [ -n "$EXLUFS" ]; then
  if [ -z "$A" ]; then fail "没有音轨，查不了响度"
  else awk -v i="${LI:-0}" -v t="$EXLUFS" 'BEGIN{d=i-t; if(d<0)d=-d; exit !(d<=1)}' || fail "响度 ${LI} LUFS 偏离 ${EXLUFS} 超过 1 LU"; fi
fi
if [ -n "$EXCOLOR" ] && [ "${CSP}/${CPR}/${CTR}" != "${EXCOLOR}/${EXCOLOR}/${EXCOLOR}" ]; then
  fail "色彩标签 ${CSP:-?}/${CPR:-?}/${CTR:-?} != ${EXCOLOR}"
fi
if [ -n "$EXPECT$EXFPS$EXLUFS$EXCOLOR" ]; then
  if [ "$RC" = 0 ]; then echo "PASS"; else for r in "${REASONS[@]}"; do echo "FAIL: $r"; done; fi
fi
exit $RC
