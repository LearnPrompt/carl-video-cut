#!/usr/bin/env python3
"""口播粗剪预览（P3 的可看版）：在剪好的口播上按 rough.json 把素材铺上去，没到位的放占位卡。只用 ffmpeg、标准库和 Pillow。

  python3 rough_preview.py --cut <工程>/cut --shots 镜号表.json --rough rough.json --out 粗剪预览.mp4
        [--assets-root <脚本目录>] [--size 1280x720] [--jobs 4]

- 每镜的时段：在 cut/cutmap.json 的成片逐字时间里按稿子句首找到的该镜起点，到下一镜起点（lib/koubo_lines.py，和分镜卡同一套）。
- 版式：full 素材全屏依次播（素材数等于该镜句数或少一句时，每条素材从对应那句开口处切入，否则平分时长）；
  corner 真人出镜、左上角小窗依次放素材；carl 真人出镜；placeholder 真人出镜、左上角占位卡写 note（这里放什么）。
- 素材短于分到的时长就循环；图片静止显示；素材原声不进预览，只有口播人声。
- 每格左上角标 @来源，左下角标镜号和素材号，方便对着分镜卡说改哪里。
这是给人看的粗剪，不是交付：叠层动效、字幕、配乐都还没做，确定性门不适用。
"""
import argparse, json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import koubo_lines  # noqa: E402

FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
BG = (29, 29, 31)
ACCENT = (196, 72, 42)


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"命令失败：{' '.join(cmd[:10])} ...\n{r.stderr[-800:]}")
    return r


def font(px):
    try: return ImageFont.truetype(FONT, px)
    except OSError: sys.exit(f"找不到字体 {FONT}")


def label_png(path, W, H, src, tag):
    """透明全幅标签：左上 @来源（黑底白字），左下 镜号·素材号（强调色底）。"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    f = font(max(14, H // 32)); pad = H // 60
    if src:
        x0, y0 = W // 48, H // 30
        bb = d.textbbox((0, 0), src, font=f)
        d.rectangle([x0, y0, x0 + bb[2] + 2 * pad, y0 + bb[3] + 2 * pad], fill=(17, 17, 17, 235))
        d.text((x0 + pad, y0 + pad), src, font=f, fill=(255, 255, 255, 255))
    bb = d.textbbox((0, 0), tag, font=f)
    x0, y1 = W // 48, H - H // 30
    d.rectangle([x0, y1 - bb[3] - 2 * pad, x0 + bb[2] + 2 * pad, y1], fill=ACCENT + (235,))
    d.text((x0 + pad, y1 - bb[3] - pad), tag, font=f, fill=(255, 255, 255, 255))
    im.save(path)


def card_png(path, W, H, title, note):
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im)
    d.rectangle([W // 12, H // 6, W - W // 12, H - H // 6], outline=(120, 120, 120), width=1)
    f1, f2 = font(max(16, H // 8)), font(max(13, H // 13))   # 占位卡放在小窗里，字号按卡片高度取大一些
    d.text((W // 8, H // 4), title, font=f1, fill=(240, 240, 240))
    y, line = H // 4 + H // 6, ""
    for ch in note:                                   # 按宽度折行
        if d.textlength(line + ch, font=f2) > W * 0.74:
            d.text((W // 8, y), line, font=f2, fill=(190, 190, 190)); y += int(H / 10); line = ""
        line += ch
    if line: d.text((W // 8, y), line, font=f2, fill=(190, 190, 190))
    im.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cut", required=True); ap.add_argument("--shots", required=True)
    ap.add_argument("--rough", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--assets-root"); ap.add_argument("--size", default="1280x720"); ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    W, H = map(int, a.size.split("x"))
    cm = json.load(open(os.path.join(a.cut, "cutmap.json"), encoding="utf-8"))
    sm = json.load(open(a.shots, encoding="utf-8"))
    rough = json.load(open(a.rough, encoding="utf-8"))["shots"]
    root = a.assets_root or os.path.dirname(sm["source"])
    fps, N = cm["fps"], round(cm["duration"] * cm["fps"])
    # 每镜起点：在成片逐字时间里按稿子句首找（和分镜卡同一套），镜内每句的起点留着给素材切入用
    ls = koubo_lines.line_starts(cm["sentences"], [r["text"] for r in sm["lines"]])
    order = [r["shot"] for r in rough]
    starts, line_starts = {}, {}
    for r in sm["lines"]:
        if r["line"] in ls:
            f = round(ls[r["line"]] * fps)
            line_starts.setdefault(r["shot"], []).append(f)
    for sh_, fs in line_starts.items():
        fs.sort(); starts[sh_] = fs[0]
    last = -1
    for sh_ in order:                                            # 镜序要单调，找错位的镜并进前一镜
        if sh_ in starts and starts[sh_] <= last: del starts[sh_]
        elif sh_ in starts: last = starts[sh_]
    seq = [x for x in order if x in starts]
    pieces = []
    for k, shot in enumerate(seq):
        f0 = 0 if k == 0 else starts[shot]
        f1 = starts[seq[k + 1]] if k + 1 < len(seq) else N
        if f1 <= f0: continue
        r = next(x for x in rough if x["shot"] == shot)
        lay, assets = r["layout"], [x for x in r.get("assets", []) if os.path.exists(os.path.join(root, x["path"]))]
        if lay in ("full", "corner") and not assets: lay = "carl"
        if lay in ("carl", "placeholder"):
            pieces.append({"shot": shot, "lay": lay, "f0": f0, "f1": f1, "note": r.get("note", "")}); continue
        if k == 0: line_starts[shot] = [0] + line_starts.get(shot, [])[1:]
        ls = line_starts.get(shot, [f0])
        n = len(assets)
        if len(ls) - n in (0, 1) and n > 1:
            cuts = [f0] + ls[len(ls) - n + 1:]                       # 每条素材从对应那句开口处切入
        else:
            cuts = [f0 + (f1 - f0) * j // n for j in range(n)]
        cuts.append(f1)
        for j, x in enumerate(assets):
            if cuts[j + 1] > cuts[j]:
                pieces.append({"shot": shot, "lay": lay, "f0": cuts[j], "f1": cuts[j + 1], "asset": x})

    tmp = os.path.join(os.path.dirname(os.path.abspath(a.out)), "_rough"); os.makedirs(tmp, exist_ok=True)
    cutmp4 = os.path.join(a.cut, "cut.mp4")
    tags = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
    enc = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-r", str(fps),
           "-video_track_timescale", str(fps * 512), "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]

    def render(i):
        p = pieces[i]; n = p["f1"] - p["f0"]; out = os.path.join(tmp, f"p{i:04d}.mp4")
        x = p.get("asset")
        mid = re.match(r"(M\d+)", os.path.basename(x["path"])).group(1) if x and re.match(r"M\d+", os.path.basename(x["path"])) else ""
        lab = os.path.join(tmp, f"l{i:04d}.png")
        label_png(lab, W, H, (x or {}).get("src") if p["lay"] == "full" else None, f"镜 {p['shot']}" + (f" · {mid}" if mid else ""))
        base = ["-ss", f"{(p['f0'] - 0.25) / fps:.6f}", "-i", cutmp4]
        if p["lay"] == "carl":
            cmd = base + ["-loop", "1", "-i", lab, "-filter_complex",
                          f"[0:v]scale={W}:{H}[b];[b][1:v]overlay=0:0,{tags}"]
        elif p["lay"] == "placeholder":                          # 真人出镜，左上角占位卡写这里放什么
            card = os.path.join(tmp, f"c{i:04d}.png")
            cw, ch = W * 2 // 5, H * 2 // 5
            card_png(card, cw, ch, f"占位：镜 {p['shot']}", p["note"] or "这里的素材还没到位")
            cmd = base + ["-loop", "1", "-i", card, "-loop", "1", "-i", lab, "-filter_complex",
                          f"[1:v]pad={cw + 2}:{ch + 2}:1:1:color=white[s];[0:v]scale={W}:{H}[b];"
                          f"[b][s]overlay={W // 40}:{H // 22}[c];[c][2:v]overlay=0:0,{tags}"]
        else:
            ap_ = os.path.join(root, x["path"])
            img = not ap_.lower().endswith((".mp4", ".mov", ".webm"))
            ain = (["-loop", "1", "-framerate", str(fps)] if img else ["-stream_loop", "-1", "-ss", str(x.get("in", 0))]) + ["-i", ap_]
            if p["lay"] == "full":
                cmd = ain + ["-loop", "1", "-i", lab, "-filter_complex",
                             f"[0:v]fps={fps},scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:-1:-1:color=0x1d1d1f,setsar=1[b];"
                             f"[b][1:v]overlay=0:0,{tags}"]
            else:
                cw, ch = W * 2 // 5, H * 2 // 5
                cmd = base + ain + ["-loop", "1", "-i", lab, "-filter_complex",
                                    f"[1:v]fps={fps},scale={cw}:{ch}:force_original_aspect_ratio=decrease,pad={cw}:{ch}:-1:-1:color=0x1d1d1f,"
                                    f"pad={cw + 2}:{ch + 2}:1:1:color=white[s];[0:v]scale={W}:{H}[b];"
                                    f"[b][s]overlay={W // 40}:{H // 22}[c];[c][2:v]overlay=0:0,{tags}"]
        sh(["ffmpeg", "-v", "error", "-y"] + cmd + ["-frames:v", str(n)] + enc + [out])
        return out

    with ThreadPoolExecutor(a.jobs) as ex:
        files = list(ex.map(render, range(len(pieces))))
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w") as f: f.writelines(f"file '{os.path.abspath(x)}'\n" for x in files)
    vcat = os.path.join(tmp, "video.mp4")
    sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", vcat])
    sh(["ffmpeg", "-v", "error", "-y", "-i", vcat, "-i", cutmp4, "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
        "-movflags", "+faststart", a.out])
    for f in os.listdir(tmp): os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    print(json.dumps({"out": a.out, "pieces": len(pieces), "frames": N,
                      "layouts": {k: sum(1 for p in pieces if p["lay"] == k) for k in ("full", "corner", "carl", "placeholder")}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
