#!/usr/bin/env python3
"""numpy 合成配乐床：和弦 pad + 琶音 + 贝斯 + 鼓，切点处放 whoosh。只依赖 numpy 与标准库 wave。

  python3 music_bed.py --dur 12 --bpm 120 --key "A minor" --cuts "2,3,4.5" --out bed.wav --lufs -14

切点会被量化到最近的拍（beat grid），偏差打印到 stderr：视频切点最好也挪到同一拍上。
--lufs 是用「两声道均方和 -0.691 dB」做的粗略近似（无 K 加权），不是 EBU R128 精确值；
需要精确响度请交给 ffmpeg loudnorm。若按目标增益会削波，则改为峰值 0.95 封顶并提示。
"""
import argparse, re, sys, wave
import numpy as np

SR = 44100
PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10]
MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
MINOR_PROG = [0, 5, 2, 6]   # i VI III VII  (Am F C G)
MAJOR_PROG = [0, 4, 5, 3]   # I V vi IV     (C G Am F)


def parse_key(s):
    m = re.match(r"^\s*([A-Ga-g])([#b]?)\s*(major|minor|maj|min|m)?\s*$", s)
    if not m:
        sys.exit(f"--key 无法解析：{s!r}（例：'A minor'、'C major'、'F# minor'）")
    pc = PC[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)]
    mode = (m.group(3) or "major").lower()
    return pc % 12, ("minor" if mode in ("minor", "min", "m") else "major")


def midi(m):
    return 440 * 2 ** ((m - 69) / 12)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dur", type=float, required=True, help="总时长（秒）")
    ap.add_argument("--bpm", type=float, default=120)
    ap.add_argument("--key", default="A minor")
    ap.add_argument("--cuts", default="", help='切点秒数，逗号分隔，如 "1.5,3.0"')
    ap.add_argument("--out", required=True)
    ap.add_argument("--lufs", type=float, default=-14.0, help="目标响度（粗略近似）")
    ap.add_argument("--intro-beats", type=int, default=4, help="开头只有轻 hat 的拍数（hook 段），之后进四踩")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    DUR, B = a.dur, 60.0 / a.bpm
    N = int(SR * DUR)
    L = np.zeros(N); R = np.zeros(N)
    rng = np.random.default_rng(a.seed)
    tonic, mode = parse_key(a.key)
    scale = MINOR_SCALE if mode == "minor" else MAJOR_SCALE
    prog = MINOR_PROG if mode == "minor" else MAJOR_PROG

    def add(sig, t, pan=0.0, gain=1.0):
        i = int(t * SR)
        if i >= N or i < 0:
            return
        j = min(N, i + len(sig)); s = sig[: j - i] * gain
        L[i:j] += s * (1 - max(pan, 0)); R[i:j] += s * (1 + min(pan, 0))

    def env(n, at=0.005, d=0.2):
        t = np.arange(n) / SR
        return np.minimum(t / at, 1) * np.exp(-t / d)

    def noise(n):
        x = rng.standard_normal(n)
        return np.diff(x, prepend=0)

    def kick():
        n = int(0.35 * SR); t = np.arange(n) / SR
        f = 50 + 110 * np.exp(-t * 30)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 0.001, 0.12)

    def hat(open_=False):
        n = int((0.18 if open_ else 0.05) * SR)
        x = np.diff(noise(n), prepend=0)
        return x * env(n, 0.001, 0.06 if open_ else 0.015) * 0.12

    def clap():
        n = int(0.25 * SR); x = noise(n)
        e = env(n, 0.001, 0.07)
        for k in (0.01, 0.02):
            e[int(k * SR):] += env(n - int(k * SR), 0.001, 0.02) * 0.6
        return x * e * 0.25

    def tone(freq, dur, kind="saw", at=0.01, d=0.3, det=0.0):
        n = int(dur * SR); t = np.arange(n) / SR; out = np.zeros(n)
        for df in ((0,) if det == 0 else (-det, det)):
            ph = 2 * np.pi * freq * (1 + df) * t
            out += np.sin(ph) if kind == "sine" else sum(np.sin(ph * h) / h for h in range(1, 12))
        return out * env(n, at, d)

    # 终结点：最后一拍整点，留约 1s 给收尾长音
    end_t = max(0.0, np.floor((DUR - 1.0) / B) * B)
    bar = 4 * B
    intro_t = a.intro_beats * B

    # 和弦 / 贝斯 / 琶音，每小节（4 拍）换一个和弦
    nbar = int(np.ceil(end_t / bar)) + 1
    for b in range(nbar):
        t0 = b * bar
        if t0 >= end_t:
            break
        deg = prog[b % 4]
        r = tonic + scale[deg]
        while r < 52: r += 12
        while r >= 64: r -= 12
        third = scale[(deg + 2) % 7] - scale[deg]; fifth = scale[(deg + 4) % 7] - scale[deg]
        ch = [r, r + third % 12, r + fifth % 12]
        for m in ch:
            add(tone(midi(m), bar, "saw", 0.08, 0.6 * bar, 0.004) * 0.035, t0, pan=rng.uniform(-.3, .3))
        if t0 < intro_t:
            continue
        arp = ch + [ch[1] + 12, ch[2] + 12, ch[1] + 12]
        for k in range(8):
            tk = t0 + k * B / 2
            if tk >= end_t:
                break
            add(tone(midi(arp[k % len(arp)] + 12), 0.25, "saw", 0.002, 0.08) * 0.05, tk, pan=0.4 if k % 2 else -0.4)
            add(tone(midi(r - 12 + (12 if k % 2 else 0)), 0.24, "sine", 0.004, 0.12) * 0.32, tk)

    # 鼓：intro 只有轻 hat，之后四踩
    for k in range(int(DUR / (B / 2)) + 1):
        t = k * B / 2
        if t >= end_t:
            break
        if t >= intro_t:
            if k % 2 == 0: add(kick(), t, gain=0.85)
            if k % 4 == 2: add(clap(), t)
            add(hat(k % 2 == 1), t, pan=0.25)
        elif k % 2 == 1:
            add(hat(), t, gain=0.6, pan=0.25)

    # intro 结束前的上升噪声
    if intro_t > 0.6:
        n = int(min(1.5, intro_t) * SR)
        add(noise(n) * np.linspace(0, 1, n) ** 3 * 0.08, intro_t - n / SR)

    # 切点 whoosh：量化到最近拍
    cuts = [float(x) for x in a.cuts.split(",") if x.strip()]
    for c in cuts:
        q = round(c / B) * B
        dev = q - c
        print(f"cut {c:.3f}s -> beat {q:.3f}s (偏差 {dev * 1000:+.0f} ms, 第 {q / B:.0f} 拍)", file=sys.stderr)
        if abs(dev) > B / 4:
            print(f"  [warn] 偏差较大，建议把视频切点挪到 {q:.3f}s", file=sys.stderr)
        n = int(0.22 * SR); w = np.concatenate([np.linspace(0, 1, n // 3) ** 2, np.linspace(1, 0, n - n // 3) ** 2])
        add(noise(n) * w * 0.05, q - 0.11)

    # 收尾：终结点 kick + crash + 主和弦长音
    add(kick(), end_t, gain=1.2)
    n = int(1.4 * SR)
    add(noise(n) * env(n, 0.001, 0.45) * 0.1, end_t, gain=1.1)
    for m in (tonic + 36, tonic + 48, tonic + 48 + (3 if mode == "minor" else 4), tonic + 55, tonic + 60):
        add(tone(midi(m), max(0.5, DUR - end_t), "saw", 0.005, 0.9, 0.003) * 0.06, end_t)

    mix = np.stack([L, R], 1)
    fade = min(int(0.25 * SR), N)
    mix[-fade:] *= np.linspace(1, 0, fade)[:, None]

    # 响度：粗略近似 LUFS ≈ 10*log10(mean(L²)+mean(R²)) - 0.691（无 K 加权、无门限）
    ms = float(np.mean(mix[:, 0] ** 2) + np.mean(mix[:, 1] ** 2)) + 1e-12
    cur = 10 * np.log10(ms) - 0.691
    gain = 10 ** ((a.lufs - cur) / 20)
    peak = float(np.abs(mix).max()) + 1e-12
    if peak * gain > 0.95:
        print(f"[warn] 按 {a.lufs} LUFS（近似）增益会削波，改为峰值 0.95 封顶；实际约 {cur + 20 * np.log10(0.95 / peak):.1f} LUFS（近似）", file=sys.stderr)
        gain = 0.95 / peak
    mix *= gain

    with wave.open(a.out, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(mix, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"wrote {a.out}  dur={DUR}s bpm={a.bpm} key={a.key} end_hit={end_t:.2f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
