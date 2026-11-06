#!/usr/bin/env python3
"""卡拍回测：拍点网格拟合 + 渲完从成片里抽音轨重测，每个切点离最近的拍不超过 3 帧算合格。

  python3 beat_check.py --video final.mp4 [--music bgm.wav] [--cuts 1.5,3,4.5]
        [--fps 30] [--tol-frames 3] [--half] [--scene 0.3] [--out beat_check.png]

- 只对配乐驱动切点的片型跑。口播片型只回测落在 BGM 重音上的叠层动作，把那些动作的成片时刻用 --cuts 传进来。
- 拍点：librosa beat_track 抽拍，再用最小二乘把拍点拟合成等间隔网格 t = t0 + k*T（剔除离网格超过 1/4 拍的点后再拟合一次），
  曲子 BPM 只当参考，以拟合网格为准。--half 允许落在半拍上。
- 有 --music 时先在原曲上拟合一次，再在成片音轨上拟合一次，两张网格的相位差就是成片里配乐整体的偏移（混音、AAC 预滚、拼接出错都会体现）。
- 切点：缺省用 ffmpeg 场景检测从成片画面里找硬切（--scene 阈值，默认 0.2，按 RGB 算，颜色变化也算；
  脚本会打印找到几个，和分镜对不上就用 --cuts）；--cuts 给了就用给定的时刻（例如叠层动作）。
- 输出逐个切点的误差表（毫秒和帧），全部在容差内 BEAT PASS，否则 BEAT FAIL 退出码 1；--out 画一张切点对拍点的检查图。
没装 librosa 时脚本会自己用 `uv run --with librosa` 重跑一遍（librosa 只用 uv 临时跑，不装进全局环境）。
"""
import argparse, json, os, re, shutil, subprocess, sys

try:
    import numpy as np
    import librosa
except ImportError:
    if os.environ.get("CVC_BEAT_REEXEC") or not shutil.which("uv"):
        sys.exit("需要 librosa：uv run --with librosa --with pillow python3 beat_check.py ...")
    os.environ["CVC_BEAT_REEXEC"] = "1"
    os.execvp("uv", ["uv", "run", "-q", "--python", "3.12", "--with", "librosa", "--with", "pillow", "python3", *sys.argv])

SR = 22050


def load_audio(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4").astype(np.float32)


def fit_grid(y):
    """返回 (t0, T, beats, resid_ms)：拟合网格的起点和拍长，检测到的拍点，拍点离网格的残差。"""
    rough = librosa.beat.beat_track(y=y, sr=SR, units="time", tightness=400)[1]
    # beat_track 给的是起音强度曲线的峰，比鼓的实际起音晚约一个分析帧（夹具实测 30ms）。
    # 每个拍点在 [-80, +40]ms 内用 64 样本（约 3ms）一格的能量包络找上升最陡的位置，定到真正的起音
    hop = 64
    env = librosa.feature.rms(y=y, frame_length=256, hop_length=hop, center=True)[0]
    rise = np.diff(np.log(env + 1e-6), prepend=np.log(env[0] + 1e-6))
    beats = []
    for b in rough:
        a, z = int((b - 0.08) * SR / hop), int((b + 0.04) * SR / hop)
        a, z = max(a, 1), min(z, len(rise))
        beats.append((a + int(np.argmax(rise[a:z]))) * hop / SR if z > a else b)
    beats = np.array(beats)
    if len(beats) < 4:
        sys.exit(f"只检测到 {len(beats)} 个拍点，曲子太短或拍子不清楚")
    T = float(np.median(np.diff(beats)))
    keep = np.ones(len(beats), bool)
    for _ in range(2):
        k = np.round((beats - beats[0]) / T)
        A = np.stack([np.ones(keep.sum()), k[keep]], 1)
        t0, T = np.linalg.lstsq(A, beats[keep], rcond=None)[0]
        resid = beats - (t0 + k * T)
        keep = np.abs(resid) < T / 4
    return float(t0), float(T), beats, resid * 1000


def scene_cuts(video, thr):
    # 先转 RGB：select 的场景分数只看第一个平面，YUV 下等于只看亮度，灰切到同亮度的蓝会漏检
    r = subprocess.run(["ffmpeg", "-v", "info", "-nostats", "-i", video, "-map", "0:v:0", "-vf",
                        f"format=rgb24,select='gt(scene,{thr})',showinfo", "-f", "null", "-"], capture_output=True, text=True)
    return [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True)
    ap.add_argument("--music")
    ap.add_argument("--cuts", default="")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--tol-frames", type=float, default=3)
    ap.add_argument("--half", action="store_true")
    ap.add_argument("--scene", type=float, default=0.2)
    ap.add_argument("--out")
    a = ap.parse_args()

    yv = load_audio(a.video)
    t0, T, beats, resid = fit_grid(yv)
    print(f"成片音轨：拟合 {60 / T:.2f} BPM，首拍 {t0:.3f}s，{len(beats)} 个拍点，残差中位数 {np.median(np.abs(resid)):.1f}ms")
    if a.music:
        m0, mT, mb, mres = fit_grid(load_audio(a.music))
        ph = ((t0 - m0) / T + 0.5) % 1 - 0.5
        print(f"原曲：拟合 {60 / mT:.2f} BPM，首拍 {m0:.3f}s；成片里配乐整体偏移 {ph * T * 1000:+.1f}ms（{ph * T * a.fps:+.2f} 帧）")
    cuts = [float(x) for x in a.cuts.split(",") if x.strip()]
    if not cuts:
        cuts = scene_cuts(a.video, a.scene)
        print(f"场景检测（阈值 {a.scene}）找到 {len(cuts)} 个硬切；和分镜的切点数对不上时，用 --cuts 直接给时刻")
    if not cuts:
        sys.exit("没有切点：成片里没检测到硬切，也没传 --cuts")
    step = T / 2 if a.half else T
    tol = a.tol_frames / a.fps
    rows, bad = [], 0
    for c in cuts:
        k = round((c - t0) / step)
        g = t0 + k * step
        e = c - g
        ok = abs(e) <= tol
        bad += not ok
        rows.append((c, g, e))
        print(f"切点 {c:7.3f}s → 最近{'半' if a.half else ''}拍 {g:7.3f}s  误差 {e * 1000:+7.1f}ms（{e * a.fps:+5.2f} 帧）{'PASS' if ok else 'FAIL'}")
    if a.out:
        try:
            from PIL import Image, ImageDraw, ImageFont
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", 18)
            except OSError:
                font = ImageFont.load_default()
            dur = len(yv) / SR; W, H = 1600, 220
            im = Image.new("RGB", (W, H), (243, 237, 226)); d = ImageDraw.Draw(im)
            X = lambda t: 20 + (W - 40) * t / dur
            k = -int(t0 // T)   # 网格往前延伸到 0（前奏拍子弱，检测不到，但网格照样成立）
            while t0 + k * T <= dur:
                d.line([(X(t0 + k * T), 30), (X(t0 + k * T), 120)], fill=(20, 20, 20), width=1); k += 1
            for b in beats: d.line([(X(b), 120), (X(b), 150)], fill=(150, 140, 130), width=1)
            for c, g, e in rows:
                col = (196, 72, 42) if abs(e) > tol else (20, 120, 60)
                d.line([(X(c), 20), (X(c), 200)], fill=col, width=3)
            d.text((20, 2), "黑：拟合网格  灰：检测拍点  绿：切点在容差内  红：超差", fill=(20, 20, 20), font=font)
            im.save(a.out); print(f"检查图：{a.out}")
        except ImportError:
            print("[note] 没有 Pillow，不画检查图", file=sys.stderr)
    print(f"BEAT {'PASS' if not bad else 'FAIL'}：{len(cuts) - bad}/{len(cuts)} 个切点在 {a.tol_frames:g} 帧以内")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
