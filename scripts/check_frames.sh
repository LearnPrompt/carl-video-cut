#!/usr/bin/env bash
# check_frames.sh <video> [outdir]
# 每秒抽一帧（左上角烧录秒数）+ 每 12 帧拼一张 4x3 联系表，供人眼快速过片。
# 输出：outdir/frames/sec_%03d.png、outdir/sheets/sheet_%02d.jpg
# 默认 outdir = <video 同目录>/_check_<basename>
# 抽帧用 select 精确落在整秒（fps=1 会取到 n+0.47 秒附近，标签和画面错位半秒）。
# 烧录秒数优先用 ffmpeg drawtext；当前 ffmpeg 若没编 freetype（drawtext 不可用），自动回退到 python3 + Pillow。
set -euo pipefail

if [ $# -lt 1 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  echo "usage: check_frames.sh <video> [outdir]" >&2; exit 2
fi
VIDEO="$1"
[ -f "$VIDEO" ] || { echo "FAIL: 找不到视频 $VIDEO" >&2; exit 2; }
VDIR="$(cd "$(dirname "$VIDEO")" && pwd)"
BASE="$(basename "$VIDEO")"; BASE="${BASE%.*}"
OUT="${2:-$VDIR/_check_$BASE}"
FRAMES="$OUT/frames"; SHEETS="$OUT/sheets"
rm -rf "$FRAMES" "$SHEETS"
mkdir -p "$FRAMES" "$SHEETS"

# 字体：Menlo 优先，回退到其他系统字体
FONT=""
for f in /System/Library/Fonts/Menlo.ttc /System/Library/Fonts/Monaco.ttf /Library/Fonts/Arial.ttf /System/Library/Fonts/Helvetica.ttc /usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf; do
  [ -f "$f" ] && { FONT="$f"; break; }
done

W="$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of csv=p=0 "$VIDEO")"
FS=$(( W / 24 )); [ "$FS" -lt 18 ] && FS=18      # 字号随分辨率走，缩到 480 宽后仍清晰可读
BORDER=$(( FS / 10 + 1 ))

HAS_DRAWTEXT=0
ffmpeg -hide_banner -filters 2>/dev/null | grep -q ' drawtext ' && [ -n "$FONT" ] && HAS_DRAWTEXT=1

if [ "$HAS_DRAWTEXT" = 1 ]; then
  VF="select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,0.999)',drawtext=fontfile='${FONT}':text='%{eif\\:n\\:d}s':x=$((FS/2)):y=$((FS/3)):fontsize=${FS}:fontcolor=white:box=1:boxcolor=black@0.7:boxborderw=${BORDER}"
  ffmpeg -v error -y -i "$VIDEO" -vf "$VF" -fps_mode vfr -start_number 0 "$FRAMES/sec_%03d.png"
else
  echo "[note] ffmpeg 无 drawtext，回退 Pillow 烧录秒数" >&2
  ffmpeg -v error -y -i "$VIDEO" -vf "select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,0.999)'" -fps_mode vfr -start_number 0 "$FRAMES/sec_%03d.png"
  python3 - "$FRAMES" "$FONT" "$FS" <<'PY'
import sys, glob, os
frames, font, fs = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("[warn] 没有 Pillow，联系表不带秒数标签（文件名即秒数）", file=sys.stderr); sys.exit(0)
try:
    f = ImageFont.truetype(font, fs) if font else ImageFont.load_default()
except Exception:
    f = ImageFont.load_default()
for p in sorted(glob.glob(os.path.join(frames, "sec_*.png"))):
    n = int(os.path.basename(p)[4:7])
    im = Image.open(p).convert("RGB"); d = ImageDraw.Draw(im)
    txt = f"{n}s"; b = d.textbbox((0, 0), txt, font=f); pad = fs // 3
    x0, y0 = fs // 2, fs // 3
    d.rectangle([x0 - pad, y0 - pad, x0 + (b[2] - b[0]) + pad, y0 + (b[3] - b[1]) + pad * 2], fill=(0, 0, 0))
    d.text((x0 - b[0], y0 - b[1]), txt, font=f, fill=(255, 255, 255))
    im.save(p)
PY
fi

NF="$(ls "$FRAMES"/sec_*.png 2>/dev/null | wc -l | tr -d ' ')"
[ "$NF" -gt 0 ] || { echo "FAIL: 没有抽出任何帧" >&2; exit 1; }

# 联系表：每 12 帧一张 4x3，每格缩到 480 宽（-2 保证偶数高度，tile 对不满的末张补黑）
ffmpeg -v error -y -framerate 1 -start_number 0 -i "$FRAMES/sec_%03d.png" \
  -vf "scale=480:-2,tile=4x3:padding=4:margin=4:color=black" -fps_mode passthrough -q:v 3 -start_number 1 "$SHEETS/sheet_%02d.jpg"

echo "frames: $NF  ($FRAMES)"
echo "sheets:"
ls "$SHEETS"/sheet_*.jpg
