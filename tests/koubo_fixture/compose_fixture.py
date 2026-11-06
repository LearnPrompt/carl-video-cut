#!/usr/bin/env python3
"""把 tts_yunxi.py 合成的单句拼成两段假口播录音，句界真值精确已知，再配几种测试画面。

  python3 compose_fixture.py --aud <tts 输出>/aud --out <夹具目录>

录音 A 里有：半句口误（u02a）、同一句重录（u03 说两遍）、稿外插话（x01）、长短不一的停顿；
录音 B 里有一段 2 秒长停顿；录音 C 句间只停 50 到 80ms，末句句内重说半句。两段都叠了约 -50 dBFS 的底噪和 50Hz 交流哼声，给人声链去处理。

输出：
  truth.json          {源名: [{key, start, end}]}，秒，按源时间
  A.mov               1920x1080 H.264 8bit bt709，VFR（60 帧里周期性丢帧），双声道：左声道人声、右声道几乎无声，音频比画面晚 40ms 开始
  B.mp4               1920x1080 HEVC 10bit bt709，29.97 CFR，单声道
  C.mp4               1920x1080 H.264 8bit bt709，30 CFR，单声道
  edge_hlg.mp4        10bit HEVC，标 bt2020 + arib-std-b67（HLG）：应停下并出 5 秒对比
  edge_untagged.mp4   没有任何色彩标签：应报信息不足
  edge_rot.mp4        1920x1080 带 90° 旋转标签，显示为竖屏：在横屏画幅下应停下问 pad 还是 crop
  edge_flat10.mp4     10bit、标 bt709、画面很灰很平：应报疑似 Log
"""
import argparse, json, os, subprocess
import numpy as np

SR = 48000
PLAN = {
    "A": [(1.0, "u01"), (0.7, "u02a"), (1.2, "u02"), (0.5, "u03"), (0.9, "x01"), (0.6, "u03"),
          (1.5, "u04"), (0.4, "u05"), (0.8, "u06")],
    "B": [(0.8, "u07"), (0.6, "u08"), (2.0, "u09"), (0.3, "u10")],
    # C：句间几乎不停（50/80ms），最后一句句内磕巴重说半句（分镜先给人看，分镜先给人看）
    "C": [(0.6, "u07"), (0.05, "u08"), (0.08, "u06s")],
}
TAIL = 1.0


def load(path):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"])
    return np.frombuffer(raw, dtype="<f4").astype(np.float64)


def write_wav(path, x, ch=1):
    data = (np.clip(x, -1, 1) * 32767).astype("<i2")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", str(ch), "-i", "-", path],
                   input=data.tobytes(), check=True)


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aud", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rng = np.random.default_rng(20261021)
    truth = {}
    for src, plan in PLAN.items():
        pieces, rows, t = [], [], 0.0
        for gap, key in plan:
            pieces.append(np.zeros(int(round(gap * SR)))); t += gap
            x = load(os.path.join(a.aud, f"{key}.wav"))
            pieces.append(x)
            rows.append({"key": key, "start": round(t, 4), "end": round(t + len(x) / SR, 4)})
            t += len(x) / SR
        pieces.append(np.zeros(int(TAIL * SR)))
        voice = np.concatenate(pieces)
        n = len(voice)
        white = rng.standard_normal(n)
        pink = np.convolve(white, np.ones(8) / 8, mode="same")
        pink *= 10 ** (-50 / 20) / (np.sqrt(np.mean(pink ** 2)) + 1e-12)
        hum = 10 ** (-40 / 20) * np.sqrt(2) * np.sin(2 * np.pi * 50 * np.arange(n) / SR)
        mix = voice + pink + hum
        truth[src] = rows
        if src == "A":
            right = rng.standard_normal(n) * 10 ** (-70 / 20)
            st = np.stack([mix, right], 1).reshape(-1)
            write_wav(os.path.join(a.out, "A_audio.wav"), st, ch=2)
        else:
            write_wav(os.path.join(a.out, f"{src}_audio.wav"), mix)
        truth[src + "_duration"] = round(n / SR, 4)
    with open(os.path.join(a.out, "truth.json"), "w") as f:
        json.dump(truth, f, ensure_ascii=False, indent=1)

    da, db = truth["A_duration"], truth["B_duration"]
    # ffmpeg 8 里滤镜链输出的帧属性会盖过命令行的色彩选项，标签要在滤镜里用 setparams 打
    sp709 = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
    tags709 = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
    # A：60fps 源里每 7 帧丢 1 帧，保留原时间戳 → VFR；音频起点 +40ms
    ff("-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=60:d={da}", "-itsoffset", "0.040", "-i", os.path.join(a.out, "A_audio.wav"),
       "-vf", "select='not(eq(mod(n\\,7)\\,3))'," + sp709, "-fps_mode", "vfr", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
       *tags709, "-c:a", "aac", "-b:a", "192k", "-map", "0:v", "-map", "1:a", os.path.join(a.out, "A.mov"))
    # B：29.97 CFR，10bit HEVC
    ff("-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=30000/1001:d={db}", "-i", os.path.join(a.out, "B_audio.wav"), "-vf", sp709,
       "-c:v", "libx265", "-x265-params", "log-level=error", "-crf", "22", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
       *tags709, "-c:a", "aac", "-b:a", "192k", "-shortest", os.path.join(a.out, "B.mp4"))
    # C：30fps CFR 8bit，单声道
    ff("-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=30:d={truth['C_duration']}", "-i", os.path.join(a.out, "C_audio.wav"), "-vf", sp709,
       "-c:v", "libx264", "-crf", "22", "-pix_fmt", "yuv420p", *tags709, "-c:a", "aac", "-b:a", "192k", "-shortest", os.path.join(a.out, "C.mp4"))
    short = os.path.join(a.out, "B_audio.wav")
    ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=8", "-i", short, "-t", "8",
       "-vf", "setparams=colorspace=bt2020nc:color_primaries=bt2020:color_trc=arib-std-b67:range=tv",
       "-c:v", "libx265", "-x265-params", "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
       "-crf", "22", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
       "-colorspace", "bt2020nc", "-color_primaries", "bt2020", "-color_trc", "arib-std-b67",
       "-c:a", "aac", os.path.join(a.out, "edge_hlg.mp4"))
    ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=4", "-i", short, "-t", "4",
       "-vf", "setparams=colorspace=unknown:color_primaries=unknown:color_trc=unknown",
       "-c:v", "libx264", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", os.path.join(a.out, "edge_untagged.mp4"))
    ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=4", "-i", short, "-t", "4",
       "-vf", sp709, "-c:v", "libx264", "-crf", "22", "-pix_fmt", "yuv420p", *tags709, "-c:a", "aac", os.path.join(a.out, "_rot_tmp.mp4"))
    ff("-display_rotation", "90", "-i", os.path.join(a.out, "_rot_tmp.mp4"), "-c", "copy", os.path.join(a.out, "edge_rot.mp4"))
    os.remove(os.path.join(a.out, "_rot_tmp.mp4"))
    ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=4", "-i", short, "-t", "4",
       "-vf", "hue=s=0.15,eq=contrast=0.30:brightness=0.02," + sp709,
       "-c:v", "libx265", "-x265-params", "log-level=error", "-crf", "22", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
       *tags709, "-c:a", "aac", os.path.join(a.out, "edge_flat10.mp4"))
    print(json.dumps({k: v for k, v in truth.items() if k.endswith("_duration")}))


if __name__ == "__main__":
    main()
