#!/usr/bin/env python3
"""混音与响度：人声、BGM、音效分轨混合，BGM 按人声侧链压缩，再两遍 loudnorm。只依赖 ffmpeg 和标准库。

  # 分轨混合
  python3 mix_audio.py --out mix.wav --voice voice.wav [--bgm bgm.wav] [--sfx sfx.wav] [--dur 61.2]
        [--bgm-gain -16] [--duck 0.05] [--lufs -14] [--tp -1.2]
  # 已有成品音轨（或带音轨的视频）只做响度
  python3 mix_audio.py --out loud.wav --in final-raw.mp4

口径：I -14 LUFS，真峰值不超过 -1.2 dBTP，在 AAC 编码之前做。loudnorm 第一遍测量、第二遍按测量值线性调整
（linear=true，不压动态；目标达不到时 ffmpeg 自动退回动态模式，报告里会写）。第二遍目标峰值比 --tp 再低 0.3 dB，
给后面 AAC 编码的峰值上冲留余量；交付文件以 probe.sh 的 ebur128 实测为准。

侧链：BGM 先过 sidechaincompress，检测信号是人声（先按人声实测响度拉到 -18 LUFS，只作用在检测支路），
人说话时 BGM 自动让路；threshold 是线性值（0.05 约 -26 dBFS），夹具实测说话段 BGM 压低约 6 到 9 dB。
输出：48k 双声道 pcm_s24le WAV，stdout 打印一行 JSON（两遍测量值和最终 I、TP）。
"""
import argparse, json, re, subprocess, sys


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg 失败：\n{r.stderr[-1200:]}")
    return r.stderr


def measure_json(stderr):
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.S)
    if not m:
        sys.exit("loudnorm 没有输出测量 JSON")
    return json.loads(m[-1])


def ebur128(path):
    err = run(["ffmpeg", "-v", "info", "-nostats", "-i", path, "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"])
    tail = err[err.rfind("Summary:"):]
    get = lambda k: float((re.search(k + r":\s+(-?[\d.]+|-inf)", tail) or [None, "nan"])[1].replace("-inf", "-200"))
    return {"I": get("I"), "LRA": get("LRA"), "TP": get("Peak")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--in", dest="inp", help="已混好的音轨或带音轨的视频，只做响度")
    ap.add_argument("--voice"); ap.add_argument("--bgm"); ap.add_argument("--sfx")
    ap.add_argument("--dur", type=float, help="输出时长（秒），缺省取人声或输入的长度")
    ap.add_argument("--bgm-gain", type=float, default=-16.0, help="BGM 进侧链前的增益 dB")
    ap.add_argument("--duck", type=float, default=0.05, help="侧链压缩阈值（线性，越小让得越早）")
    ap.add_argument("--lufs", type=float, default=-14.0)
    ap.add_argument("--tp", type=float, default=-1.2)
    ap.add_argument("--lra", type=float, default=11.0)
    a = ap.parse_args()
    if not (a.inp or a.voice or a.bgm):
        sys.exit("至少给 --in，或 --voice / --bgm 之一")

    inputs, graph = [], []
    if a.inp:
        inputs += ["-i", a.inp]
        graph.append("[0:a:0]aformat=sample_rates=48000:channel_layouts=stereo[mix]")
    else:
        idx = {}
        for k in ("voice", "bgm", "sfx"):
            f = getattr(a, k)
            if f:
                idx[k] = len(inputs) // 2; inputs += ["-i", f]
        parts = []
        if "voice" in idx:
            # 侧链检测信号按人声实测响度拉到 -18 LUFS，让路深度不随人声录得大小变化
            scg = -18.0 - ebur128(a.voice)["I"] if "bgm" in idx else 0.0
            graph.append(f"[{idx['voice']}:a:0]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[v][vsc0]")
            graph.append(f"[vsc0]volume={scg:.2f}dB[vsc]")
            parts.append("[v]")
        if "bgm" in idx:
            graph.append(f"[{idx['bgm']}:a:0]aformat=sample_rates=48000:channel_layouts=stereo,volume={a.bgm_gain}dB[b0]")
            if "voice" in idx:
                graph.append(f"[b0][vsc]sidechaincompress=threshold={a.duck}:ratio=8:attack=20:release=350:makeup=1[b]")
            else:
                graph.append("[b0]anull[b]")
            parts.append("[b]")
        elif "voice" in idx:
            graph.append("[vsc]anullsink")
        if "sfx" in idx:
            graph.append(f"[{idx['sfx']}:a:0]aformat=sample_rates=48000:channel_layouts=stereo[s]")
            parts.append("[s]")
        graph.append(f"{''.join(parts)}amix=inputs={len(parts)}:normalize=0:duration=first[mix]" if len(parts) > 1 else f"{parts[0]}anull[mix]")
    pre = ";".join(graph)
    fit = f"apad,atrim=end={a.dur}," if a.dur else ""   # 给了时长就补齐或裁到这么长

    tp2 = a.tp - 0.3
    ln1 = f"loudnorm=I={a.lufs}:TP={tp2}:LRA={a.lra}:print_format=json"
    m = measure_json(run(["ffmpeg", "-v", "info", "-nostats", *inputs, "-filter_complex", f"{pre};[mix]{fit}{ln1}[o]",
                          "-map", "[o]", "-f", "null", "-"]))
    ln2 = (f"loudnorm=I={a.lufs}:TP={tp2}:LRA={a.lra}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
           f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true:print_format=json")
    m2 = measure_json(run(["ffmpeg", "-v", "info", "-nostats", "-y", *inputs, "-filter_complex", f"{pre};[mix]{fit}{ln2},aresample=48000[o]",
                           "-map", "[o]", "-ac", "2", "-c:a", "pcm_s24le", a.out]))
    fin = ebur128(a.out)
    print(json.dumps({"out": a.out, "pass1": {k: m[k] for k in ("input_i", "input_tp", "input_lra")},
                      "mode": m2.get("normalization_type"), "target": {"I": a.lufs, "TP": a.tp}, "measured": fin}, ensure_ascii=False))
    if m2.get("normalization_type") != "linear":
        print("[note] loudnorm 退回动态模式：线性增益会让真峰值超标，动态模式会轻微压缩响度范围", file=sys.stderr)


if __name__ == "__main__":
    main()
