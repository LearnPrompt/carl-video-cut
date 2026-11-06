#!/usr/bin/env python3
"""P1 验收：中间档 probe（bt709、CFR 30）、音画对齐、人声链有没有引入延迟。

  python3 check_p1.py --src <夹具 src> --norm <normalize 输出> [--expect-offset A=0.040,B=0]

对齐用互相关：normalize 输出的 raw.wav 和夹具的原始干声逐样本比，滞后应等于夹具里故意加的偏移
（A.mov 的音频比画面晚 40ms 开始，所以 raw.wav 里人声应在 +40ms）。人声链前后再比一次，滞后应为 0。
"""
import argparse, json, subprocess, sys
import numpy as np

SR = 48000


def load(path, ch=None):
    af = ["-af", f"pan=mono|c0=c{ch}"] if ch is not None else []
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", path, *af, "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"])
    return np.frombuffer(raw, dtype="<f4").astype(np.float64)


def lag(a, b, max_s=0.2):
    """b 相对 a 晚多少秒（正数 = b 晚）。"""
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    c = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)
    m = int(max_s * SR)
    cand = np.concatenate([c[-m:], c[:m + 1]])
    return (int(np.argmax(cand)) - m) / SR


def probe(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                                   "stream=width,height,r_frame_rate,avg_frame_rate,nb_read_packets,color_space,color_primaries,color_transfer,color_range,pix_fmt:format=duration",
                                   "-of", "json", path])
    j = json.loads(out)
    return j["streams"][0], float(j["format"]["duration"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--norm", required=True)
    ap.add_argument("--expect-offset", default="A=0.040,B=0")
    a = ap.parse_args()
    ok = True
    for item in a.expect_offset.split(","):
        name, exp = item.split("="); exp = float(exp)
        s, dur = probe(f"{a.norm}/{name}.mp4")
        cfr = s["r_frame_rate"] == s["avg_frame_rate"] == "30/1"
        tags = (s.get("color_space"), s.get("color_primaries"), s.get("color_transfer"), s.get("color_range")) == ("bt709", "bt709", "bt709", "tv")
        nfr = int(s["nb_read_packets"])
        wav_dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f"{a.norm}/{name}.voice.wav"]))
        dry = load(f"{a.src}/{name}_audio.wav", ch=0)
        raw = load(f"{a.norm}/{name}.raw.wav")
        voice = load(f"{a.norm}/{name}.voice.wav")
        l1 = lag(dry, raw)
        l2 = lag(raw, voice)
        # 容差 1ms：MOV 封装按毫秒取整音频起点（A.mov 实际在 39.33ms），离唇音同步能察觉的 20ms 很远
        r1 = abs(l1 - exp) <= 0.001
        r2 = abs(l2) <= 1 / SR * 2
        av = abs(wav_dur - nfr / 30) <= 1 / 30
        print(f"{name}: {s['width']}x{s['height']} {s['pix_fmt']} r={s['r_frame_rate']} avg={s['avg_frame_rate']} "
              f"color={s.get('color_space')}/{s.get('color_primaries')}/{s.get('color_transfer')}/{s.get('color_range')} "
              f"{nfr} 帧={nfr / 30:.3f}s  wav={wav_dur:.3f}s")
        print(f"   CFR30 {'PASS' if cfr else 'FAIL'} | bt709 {'PASS' if tags else 'FAIL'} | 音画时长差 {abs(wav_dur - nfr / 30) * 1000:.1f}ms {'PASS' if av else 'FAIL'}")
        print(f"   原声→raw 滞后 {l1 * 1000:+.2f}ms（期望 {exp * 1000:+.0f}ms）{'PASS' if r1 else 'FAIL'} | 人声链延迟 {l2 * 1000:+.2f}ms {'PASS' if r2 else 'FAIL'}")
        ok &= cfr and tags and av and r1 and r2
    print("P1 ALL PASS" if ok else "P1 FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
