#!/usr/bin/env python3
"""按 takes 剪口播：留选中的那一遍，剪掉没选的遍、句内重说（dups）和长气口，画面和人声一起切。只依赖 ffmpeg 和标准库。

  python3 cut_takes.py --timings timings.json --takes takes.json --src A=<norm>/<名> [--src B=...] --out <工程>/cut
        [--fps 30] [--gap 0.6] [--pad 0.125] [--fade 0.01] [--size 1920x1080] [--crf 16] [--preset medium] [--jobs 4]

- takes.json：[{src, sent, keep, drop?, pad_in?, pad_out?}]（或 {takes: [...]}），数组顺序就是成片顺序；sent 是 timings.json 里该源的句 id；
  drop 是句内要剪的字区间 [[从, 到)]，下标对应该句 chars，切点落在两字之间（koubo_card.py 自由口播模式标重说用）。
  {takes, cuts_src} 形式时，cuts_src 是按原片秒指定要剪的区间 [{src, from, to, why}]：剪完复核补剪（refine_cut.py）
  和人工指定"这段剪掉"都记在这里，原片不变，秒数不会因为重跑转写失效；照原样剪，不再吸附。
  只存句号不存秒数，重跑转写也不失效（两个时间域的规则）。
- --src 给源名对应的归一产物前缀：<前缀>.mp4（画面）和 <前缀>.voice.wav（人声链之后）。
- 气口：同一源里前后相邻的两句都留、中间停顿不超过 --gap 秒时整段连着不切；超过就切开，
  前句尾留 pad_out、后句头留 pad_in（缺省各 --pad 秒，合起来约 0.25 秒），且不越过相邻句的边界。
- dups（句内回到分句开头重说的那段）和 drop 都剪掉，挖掉的区间两端先吸附到附近的真实停顿（silencedetect -45dB），
  逐字时间的误差就不会留下半个字；切点两侧同样做淡入淡出。
- 每段起止对齐到帧（画面 1/fps 秒、音频 48000/fps 个样本），音画天然同步；每个切点两侧各 --fade 秒线性淡入淡出。
- 画面逐段精确起点重编码后 concat；人声按样本切，标准库直接拼，不重采样。

输出（<out>/ 下）：
  cut.mp4        画面 + 人声（aac 192k），给预览和后续叠层用
  cut.voice.wav  48k 单声道 pcm_s24le，混音用
  cutmap.json    {fps, duration, segments:[{src, s0, s1, t0}], sentences:[{src, sent, line, t0, t1, text, chars}]}
                 成片时间只在这里出现，每次剪辑现算；chars 是成片时间的逐字 [字, 起, 止]，剪掉的字不在里面。
"""
import argparse, array, json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

SR = 48000


def sh(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"命令失败：{' '.join(cmd[:8])} ...\n{r.stderr[-800:]}")
    return r


def load_pcm(path):
    """读成 32 位整数样本（s24 左移 8 位），标准库 array，单声道。"""
    raw = sh_bytes(["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "s32le", "-"])
    a = array.array("i"); a.frombytes(raw)
    if sys.byteorder != "little": a.byteswap()
    return a


def sh_bytes(cmd):
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"命令失败：{' '.join(cmd[:8])} ...\n{r.stderr[-800:].decode(errors='replace')}")
    return r.stdout


def silences(wav, noise_db=-45, dur=0.06):
    r = subprocess.run(["ffmpeg", "-v", "info", "-nostats", "-i", wav, "-af", f"silencedetect=n={noise_db}dB:d={dur}",
                        "-f", "null", "-"], capture_output=True, text=True)
    return list(zip([float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)],
                    [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]))


def snap(d0, d1, sil, ch):
    """挖掉的区间两端吸附到真实停顿：转写给的边界常差几百毫秒，按时间直接切会留下半个字。
    以字为锚：终点（话接上）在"重新开口那个字的前一个字之后、它开口之前"找停顿，取最靠后的停顿结尾；
    起点（话停下）在"最后保留的字之后、第一个被剪的字开口之前"找停顿，取最靠前的停顿开头。
    找不到停顿就不动（连读处只能切在两字之间）。各留 20 毫秒余量。ch 是该句逐字 [字, 起, 止]。"""
    t = [c[1] for c in ch]
    k1 = next((k for k, x in enumerate(t) if x >= d1 - 0.05), None)     # 重新开口的字
    k0 = next((k for k, x in enumerate(t) if x >= d0 - 0.05), None)     # 第一个被剪的字
    n0, n1 = d0, d1
    if k0 is not None and k0 > 0:
        a = [s0 for s0, s1 in sil if t[k0 - 1] + 0.05 <= s0 <= t[k0] + 0.1]
        if a: n0 = min(a) + 0.02
    if k1 is not None and k1 > 0:
        b = [s1 for s0, s1 in sil if t[k1 - 1] + 0.05 <= s1 <= t[k1] + 0.1]
        if b: n1 = max(b) - 0.02
    return (n0, n1) if n1 > n0 else (d0, d1)


def build_segments(T, takes, gap, pad, fps, sil, extra=None):
    """takes → 源时间段 [(src, a, b, [(sent_key)...])]，已合并相邻句、去掉 dups 和 drop、对齐到帧。"""
    segs, prev = [], None
    drops = {(tk["src"], tk["sent"]): tk.get("drop") for tk in takes if tk.get("keep", True) and tk.get("drop")}
    for tk in takes:
        if not tk.get("keep", True):
            continue
        src, sid = tk["src"], tk["sent"]
        S = T[src]["by_id"]
        if sid not in S:
            sys.exit(f"takes 里的 {src}#{sid} 在 timings.json 里找不到")
        s = S[sid]
        lo = S[sid - 1]["end"] if sid - 1 in S else 0.0           # 不越过前一句的尾
        hi = S[sid + 1]["start"] if sid + 1 in S else T[src]["duration"]
        p_in = min(tk.get("pad_in", pad), max(0.0, s["start"] - lo))
        p_out = min(tk.get("pad_out", pad), max(0.0, hi - s["end"]))
        a, b = s["start"] - p_in, s["end"] + p_out
        if prev and prev["src"] == src and prev["sent"] == sid - 1 and s["start"] - prev["end"] <= gap:
            a = prev["b"]                                           # 相邻且停顿不长：接着上一段，不切
            segs[-1][2] = b
            segs[-1][3].append((src, sid))
        else:
            segs.append([src, a, b, [(src, sid)]])
        prev = {"src": src, "sent": sid, "end": s["end"], "b": b}
    # 去掉 dups 和 drop：把落在段里的重说区间、takes 指定要剪的字区间挖掉
    def drop_times(src, sid, rngs):
        S = T[src]["by_id"]; s = S[sid]; ch = s.get("chars") or []
        lo = (S[sid - 1]["end"] + s["start"]) / 2 if sid - 1 in S else 0.0
        hi = (s["end"] + S[sid + 1]["start"]) / 2 if sid + 1 in S else T[src]["duration"]
        out = []
        for c0, c1 in rngs:
            if not ch or c0 >= c1: continue
            t0 = lo if c0 <= 0 else (ch[c0 - 1][2] + ch[c0][1]) / 2      # 两字之间切
            t1 = hi if c1 >= len(ch) else (ch[c1 - 1][2] + ch[c1][1]) / 2
            out.append([t0, t1])
        return out
    out = []
    for src, a, b, keys in segs:
        cuts = sorted([snap(d[0], d[1], sil[src], T[src]["by_id"][k[1]].get("chars") or []) for k in keys for d in
                       (T[src]["by_id"][k[1]].get("dups") or []) + drop_times(src, k[1], drops.get(k) or [])] +
                      [(x0, x1) for x0, x1 in (extra or {}).get(src, []) if x1 > a and x0 < b])   # cuts_src：按原片秒指定，已吸附过
        cur = a
        for d0, d1 in cuts:
            if d1 <= cur or d0 >= b:
                continue
            if d0 > cur: out.append((src, cur, d0, keys))
            cur = max(cur, d1)
        if b > cur: out.append((src, cur, b, keys))
    fr = []
    for src, a, b, keys in out:
        fa, fb = round(a * fps), round(b * fps)
        if fb - fa >= max(1, round(0.2 * fps)):                 # 挖完剩下不到 0.2 秒的碎段（多是被剪那句前留的气口）直接丢，免得多一个跳切
            fr.append({"src": src, "fa": fa, "fb": fb, "keys": keys})
    return fr


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timings", required=True); ap.add_argument("--takes", required=True)
    ap.add_argument("--src", action="append", required=True, help="源名=归一产物前缀")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=int, default=30); ap.add_argument("--gap", type=float, default=0.6)
    ap.add_argument("--pad", type=float, default=0.125); ap.add_argument("--fade", type=float, default=0.01)
    ap.add_argument("--size", default=None, help="缺省保持归一画幅；预览可给 960x540")
    ap.add_argument("--crf", type=int, default=16); ap.add_argument("--preset", default="medium")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()

    tj = json.load(open(a.timings, encoding="utf-8"))
    takes = json.load(open(a.takes, encoding="utf-8"))
    extra = {}
    if isinstance(takes, dict):
        for x in takes.get("cuts_src", []): extra.setdefault(x["src"], []).append((x["from"], x["to"]))
        takes = takes["takes"]
    pre = dict(s.split("=", 1) for s in a.src)
    T = {}
    for name, v in tj.items():
        if name.startswith("_"): continue
        T[name] = {"duration": v["duration"], "by_id": {s["id"]: s for s in v["sentences"]}}
    for name in {t["src"] for t in takes if t.get("keep", True)}:
        if name not in pre: sys.exit(f"takes 用到源 {name}，没给 --src {name}=<前缀>")
        if name not in T: sys.exit(f"timings.json 里没有源 {name}")
        for ext in (".mp4", ".voice.wav"):
            if not os.path.exists(pre[name] + ext): sys.exit(f"找不到 {pre[name] + ext}")

    fps, spf = a.fps, SR // a.fps
    sil = {name: silences(pre[name] + ".voice.wav") for name in {t["src"] for t in takes if t.get("keep", True)}}
    segs = build_segments(T, takes, a.gap, a.pad, fps, sil, extra)
    if not segs: sys.exit("takes 里没有留下任何一句")
    os.makedirs(a.out, exist_ok=True)
    tmp = os.path.join(a.out, "_segs"); os.makedirs(tmp, exist_ok=True)

    # 画面：每段精确起点重编码（-ss 取比目标帧早 1/4 帧的时刻，保证第一帧就是目标帧），再 concat
    vf = ["setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"]
    if a.size:
        w, h = a.size.split("x"); vf.insert(0, f"scale={w}:{h}:flags=lanczos")
    def enc(k):
        s = segs[k]; f = os.path.join(tmp, f"v{k:04d}.mp4")
        sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{(s['fa'] - 0.25) / fps:.6f}", "-i", pre[s["src"]] + ".mp4",
            "-frames:v", str(s["fb"] - s["fa"]), "-an", "-vf", ",".join(vf), "-c:v", "libx264", "-preset", a.preset,
            "-crf", str(a.crf), "-g", str(fps), "-pix_fmt", "yuv420p", "-r", str(fps), "-video_track_timescale", str(fps * 512),
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv", f])
        got = int(sh(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                      "stream=nb_read_frames", "-of", "csv=p=0", f]).stdout.strip() or 0)
        if got != s["fb"] - s["fa"]:
            sys.exit(f"第 {k} 段帧数 {got} != {s['fb'] - s['fa']}（源 {s['src']} 帧 {s['fa']}-{s['fb']}）")
        return f
    with ThreadPoolExecutor(a.jobs) as ex:
        files = list(ex.map(enc, range(len(segs))))
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w") as f:
        f.writelines(f"file '{os.path.abspath(x)}'\n" for x in files)
    vcat = os.path.join(tmp, "video.mp4")
    sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", vcat])

    # 人声：按样本切，切点两侧线性淡入淡出
    pcm = {name: load_pcm(pre[name] + ".voice.wav") for name in {s["src"] for s in segs}}
    nf = max(1, int(a.fade * SR))
    out = array.array("i")
    for s in segs:
        src = pcm[s["src"]]
        i0, i1 = s["fa"] * spf, s["fb"] * spf
        chunk = src[i0:i1]
        if len(chunk) < i1 - i0:
            chunk.extend([0] * (i1 - i0 - len(chunk)))               # 源音频比画面短的尾巴补静音
        n = min(nf, len(chunk) // 2)
        for j in range(n):
            g = j / n
            chunk[j] = int(chunk[j] * g); chunk[-1 - j] = int(chunk[-1 - j] * g)
        out.extend(chunk)
    if sys.byteorder != "little": out.byteswap()
    voice = os.path.join(a.out, "cut.voice.wav")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s32le", "-ar", str(SR), "-ac", "1", "-i", "-",
                        "-c:a", "pcm_s24le", voice], input=out.tobytes(), capture_output=True)
    if r.returncode != 0: sys.exit(f"写人声失败：{r.stderr[-400:].decode(errors='replace')}")
    final = os.path.join(a.out, "cut.mp4")
    sh(["ffmpeg", "-v", "error", "-y", "-i", vcat, "-i", voice, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-movflags", "+faststart", final])

    # 时间换算：源时间 → 成片时间
    t, mseg = 0, []
    for s in segs:
        mseg.append({"src": s["src"], "s0": s["fa"] / fps, "s1": s["fb"] / fps, "t0": t / fps}); t += s["fb"] - s["fa"]
    def to_final(src, x):
        for m in mseg:
            if m["src"] == src and m["s0"] <= x < m["s1"]:
                return round(m["t0"] + x - m["s0"], 3)
        return None
    sents, seen = [], set()
    for s in segs:
        for key in s["keys"]:
            if key in seen: continue
            seen.add(key)
            S = T[key[0]]["by_id"][key[1]]
            chars = []
            for c, c0, c1 in S.get("chars") or []:
                f0 = to_final(key[0], (c0 + c1) / 2)
                if f0 is not None:
                    chars.append([c, to_final(key[0], c0) if to_final(key[0], c0) is not None else f0,
                                  to_final(key[0], c1 - 1e-6) if to_final(key[0], c1 - 1e-6) is not None else f0])
            t0 = to_final(key[0], S["start"]); t1 = to_final(key[0], S["end"] - 1e-6)
            sents.append({"src": key[0], "sent": key[1], "line": S.get("line"), "text": S.get("text") or S.get("heard"),
                          "t0": t0 if t0 is not None else (chars[0][1] if chars else None),
                          "t1": t1 if t1 is not None else (chars[-1][2] if chars else None), "chars": chars})
    cm = {"fps": fps, "duration": t / fps, "segments": mseg, "sentences": sents}
    json.dump(cm, open(os.path.join(a.out, "cutmap.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for f in files: os.remove(f)
    os.remove(lst); os.remove(vcat); os.rmdir(tmp)
    src_total = sum(T[k]["duration"] for k in pcm)
    print(json.dumps({"out": final, "segments": len(segs), "sentences": len(sents), "duration": round(t / fps, 3),
                      "source_duration": round(src_total, 3)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
