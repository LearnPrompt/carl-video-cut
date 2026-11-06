#!/usr/bin/env python3
"""源归一 + 人声链（口播片型第一步）。只依赖 ffmpeg / ffprobe 与 Python 标准库。

  python3 normalize.py <源文件...> --out <工程>/norm --canvas 1920x1080 [--fps 30]
        [--color auto|sdr|hlg|pq|log] [--lut <官方 Log 转 709 的 .cube>] [--hdr-method vt]
        [--fit pad|crop] [--voice-chain on|off]

每个源先 probe 再分类，分类结果写进 normalize_report.md：
- 色彩：SDR / HDR-HLG / HDR-PQ / 疑似 Log / 信息不足。
  SDR 不做任何色彩转换，只转 CFR 和目标画幅；
  HDR 先出 5 秒转 SDR 对比（左：不做映射直接当 709，右：VideoToolbox 映射到 bt709）然后停下，
      看过对比再带 --hdr-method vt 重跑；
  疑似 Log、信息不足：停下报告，不猜。确认是普通 SDR 用 --color sdr，是 Log 用 --color log --lut <文件>。
- 帧率：r_frame_rate 与 avg_frame_rate 不一致按 VFR 报出，转 CFR 时明说是补帧还是抽帧。
- 旋转：读 displaymatrix，解码时自动转正，报出转了多少度。
- 画幅：源和 --canvas 比例不同就停下，要 --fit pad（加边）或 --fit crop（居中裁）由人定。
- 音频：双声道里一路比另一路低 15 dB 以上（无线麦单声道录进左声道的常见情况）就只取响的那路，否则混成单声道；
  音频起点和画面起点的偏差补齐或裁掉，保证两者 t=0 对齐；长度裁到和画面一样。

人声链只做一次，作用在整段源音频上：两级 highpass 80Hz → afftdn → acompressor（不补增益）。转写和成片用的是同一份 voice.wav。

输出（<out>/ 下，<名> 取源文件名）：
  <名>.mp4                 只有画面：CFR、bt709、目标画幅、x264 crf 14、每秒一个关键帧
  <名>.raw.wav             48k 单声道，未处理
  <名>.voice.wav           48k 单声道，人声链之后（--voice-chain off 时与 raw 相同）
  <名>.probe.json          源文件 ffprobe 原始输出
  <名>_voice_{before,after}_{loudness,spectrum}.png
  normalize_report.md      每个源的分类、做了什么、没做什么
退出码：0 全部归一完成；3 有源需要人来定（HDR / Log / 信息不足 / 画幅不符 / 没有音轨）；2 参数或文件错误。
"""
import argparse, json, os, re, struct, subprocess, sys
from fractions import Fraction

SDR_TRC = {"bt709", "smpte170m", "bt470bg", "bt470m", "iec61966-2-1", "gamma22", "gamma28", "bt2020-10", "bt2020-12", "linear"}
UNKNOWN = {None, "", "unknown", "unspecified", "reserved"}
# 两级 80Hz 高通（24dB/oct，单级在 50Hz 只压约 8dB 压不住交流哼声）→ afftdn 降 12dB 并跟踪底噪 → 压缩器不补偿增益，
# 响度统一留到混音时两遍 loudnorm。夹具实测：哼声 -40 → -55 dBFS，语音与静音段差 18.8 → 24.9 dB。
VOICE_CHAIN = "highpass=f=80,highpass=f=80,afftdn=nr=12:nf=-45:tn=1,acompressor=threshold=-21dB:ratio=3:attack=10:release=120:makeup=1"


def sh(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"命令失败：{' '.join(cmd[:6])} ...\n{r.stderr[-800:]}")
    return r


def probe(path):
    r = sh(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path])
    return json.loads(r.stdout)


def frac(s):
    try:
        f = Fraction(s)
        return float(f) if f.denominator else 0.0
    except (ValueError, ZeroDivisionError, TypeError):
        return 0.0


def bit_depth(v):
    m = re.search(r"p(9|10|12|14|16)", v.get("pix_fmt", ""))
    if m:
        return int(m.group(1))
    return int(v.get("bits_per_raw_sample") or 8)


def rotation(v):
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            return int(round(float(sd["rotation"])))
    rot = (v.get("tags") or {}).get("rotate")
    return -int(rot) if rot else 0


def signal_stats(path, dur, depth):
    """抽 8 帧量亮度分布和饱和度，用来判断是否疑似 Log。返回比例值（0..1）。"""
    n = 8
    pts = [dur * (i + 0.5) / n for i in range(n)]
    ylo = yhi = sat = 0.0
    got = 0
    for t in pts:
        r = sh(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
                "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"], check=False)
        kv = dict(re.findall(r"lavfi\.signalstats\.(\w+)=([\d.]+)", r.stdout))
        if "YLOW" in kv:
            ylo += float(kv["YLOW"]); yhi += float(kv["YHIGH"]); sat += float(kv["SATAVG"]); got += 1
    if not got:
        return None
    full = (1 << depth) - 1
    return {"ylow": ylo / got / full, "yhigh": yhi / got / full, "sat": sat / got / full}


def classify(v, fmt_tags, args, path, dur):
    """返回 (类别, 说明)。类别：sdr / hlg / pq / log / log? / unknown。"""
    if args.color != "auto":
        return args.color, f"按 --color {args.color} 处理（人工确认）"
    trc, prim, depth = v.get("color_transfer"), v.get("color_primaries"), bit_depth(v)
    if trc == "arib-std-b67":
        return "hlg", f"传递特性 arib-std-b67（HLG），色域 {prim or '?'}，{depth} bit"
    if trc == "smpte2084":
        return "pq", f"传递特性 smpte2084（PQ/HDR10），色域 {prim or '?'}，{depth} bit"
    if trc in UNKNOWN:
        return "unknown", f"传递特性没有标注（{trc or '缺失'}），色域 {prim or '缺失'}，{depth} bit：信息不足，不猜"
    if trc in SDR_TRC:
        if depth >= 10:
            st = signal_stats(path, dur, depth)
            if st is None:
                return "unknown", f"{trc}，{depth} bit，抽帧统计失败：信息不足"
            spread = st["yhigh"] - st["ylow"]
            desc = f"{trc}，{depth} bit；亮度 10%-90% 分位 {st['ylow']:.2f}-{st['yhigh']:.2f}，平均饱和度 {st['sat']:.3f}"
            # Log 素材的特征：10bit、亮度挤在中间一段、饱和度很低。只用来决定停不停，不用来自动转换。
            if spread < 0.40 and st["sat"] < 0.10:
                return "log?", desc + "：画面很灰很平，疑似 Log（如 D-Log M），请确认拍摄时的色彩模式"
            return "sdr", desc
        return "sdr", f"{trc}，{depth} bit"
    return "unknown", f"传递特性 {trc} 不在已知列表里：信息不足"


def channel_rms(path):
    r = sh(["ffmpeg", "-v", "info", "-nostats", "-i", path, "-map", "0:a:0", "-af",
            "astats=measure_overall=none:measure_perchannel=RMS_level", "-f", "null", "-"], check=False)
    return [float(x) if x not in ("-inf", "inf") else -200.0 for x in re.findall(r"RMS level dB: (\S+)", r.stderr)]


def chain_latency(chain, sr=48000):
    """人声链的固定延迟（样本数）。afftdn 按块处理，48k 下实测晚 1200 样本（25ms），不补的话声音比嘴型晚。
    每次用一个脉冲现量，ffmpeg 升级改了块长也不会错。"""
    n = sr * 2
    buf = bytearray(n * 4)
    struct.pack_into("<f", buf, sr * 4, 0.5)
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-", "-af", chain,
                        "-f", "f32le", "-"], input=bytes(buf), capture_output=True, check=True)
    y = struct.unpack(f"<{len(r.stdout) // 4}f", r.stdout)
    k = max(range(len(y)), key=lambda i: abs(y[i]))
    return k - sr


def first_audio_pts(path):
    """第一个解码出来的音频帧时间戳（已扣掉编码器预滚），秒。"""
    r = sh(["ffprobe", "-v", "error", "-select_streams", "a:0", "-read_intervals", "%+#1", "-show_entries",
            "frame=pts_time,best_effort_timestamp_time", "-of", "json", path], check=False)
    try:
        fr = json.loads(r.stdout)["frames"][0]
        return float(fr.get("pts_time") or fr.get("best_effort_timestamp_time"))
    except (KeyError, IndexError, ValueError, TypeError):
        return 0.0


def loudness(path):
    r = sh(["ffmpeg", "-v", "info", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"], check=False)
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    get = lambda k: (re.search(k + r":\s+(-?[\d.]+|-inf)", tail) or [None, "nan"])[1]
    return {"I": get("I"), "LRA": get("LRA"), "TP": get("Peak")}


def loud_curve(wav, png, title):
    """全时长响度曲线：瞬时（M，400ms）浅线、短时（S，3s）强调色线，参考线 -14 和 -23 LUFS。
    ebur128 自带的图是滚动窗口，长片只剩最后两分钟，所以自己画。没有 Pillow 时退回 ebur128 的图。"""
    r = sh(["ffmpeg", "-v", "error", "-i", wav, "-af",
            "ebur128=metadata=1,ametadata=print:file=-", "-f", "null", "-"], check=False)
    pts, M, S = [], [], []
    for blk in r.stdout.split("frame:")[1:]:
        t = re.search(r"pts_time:([\d.]+)", blk); m = re.search(r"lavfi\.r128\.M=(-?[\d.]+)", blk); s = re.search(r"lavfi\.r128\.S=(-?[\d.]+)", blk)
        if t and m and s:
            pts.append(float(t.group(1))); M.append(float(m.group(1))); S.append(float(s.group(1)))
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        sh(["ffmpeg", "-v", "error", "-y", "-i", wav, "-filter_complex", "[0:a]ebur128=video=1:meter=18:size=1600x500[v][a]",
            "-map", "[v]", "-update", "1", png, "-map", "[a]", "-f", "null", "-"])
        return
    W, H, L, R, T, B = 1600, 500, 70, 20, 50, 40
    lo, hi = -60.0, 0.0
    im = Image.new("RGB", (W, H), (243, 237, 226)); d = ImageDraw.Draw(im)
    font = None
    for fp in ("/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"):
        try:
            font = ImageFont.truetype(fp, 18); break
        except OSError:
            pass
    font = font or ImageFont.load_default()
    X = lambda t: L + (W - L - R) * t / max(pts[-1] if pts else 1, 1e-6)
    Y = lambda v: T + (H - T - B) * (hi - min(max(v, lo), hi)) / (hi - lo)
    for v in range(-60, 1, 10):
        d.line([(L, Y(v)), (W - R, Y(v))], fill=(214, 207, 196)); d.text((8, Y(v) - 10), f"{v}", fill=(20, 20, 20), font=font)
    for v, lab in ((-14, "-14 交付"), (-23, "-23 广播")):
        d.line([(L, Y(v)), (W - R, Y(v))], fill=(20, 20, 20)); d.text((W - R - 110, Y(v) - 22), lab, fill=(20, 20, 20), font=font)
    if pts:
        d.line([(X(t), Y(v)) for t, v in zip(pts, M)], fill=(160, 152, 140), width=1)
        d.line([(X(t), Y(v)) for t, v in zip(pts, S)], fill=(196, 72, 42), width=3)
        for k in range(0, int(pts[-1]) + 1, max(1, int(pts[-1] / 10) or 1)):
            d.text((X(k) - 8, H - B + 8), f"{k}s", fill=(20, 20, 20), font=font)
    d.rectangle([L, T, W - R, H - B], outline=(20, 20, 20))
    d.text((L, 14), title + "    灰线：瞬时 M    红线：短时 S", fill=(20, 20, 20), font=font)
    im.save(png)


def plots(wav, prefix, title):
    sh(["ffmpeg", "-v", "error", "-y", "-i", wav, "-lavfi",
        "showspectrumpic=s=1600x500:legend=1:fscale=log:scale=log:color=intensity", f"{prefix}_spectrum.png"])
    loud_curve(wav, f"{prefix}_loudness.png", title)


def fit_filter(dw, dh, cw, ch, fit):
    if fit == "crop":
        return f"scale={cw}:{ch}:force_original_aspect_ratio=increase:flags=lanczos,crop={cw}:{ch}"
    if fit == "pad":
        return (f"scale={cw}:{ch}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:color=black")
    return f"scale={cw}:{ch}:flags=lanczos"


def hdr_compare(path, out, name, v, dur, cw, ch):
    """5 秒对比：左边不做映射直接当 709 显示，右边走 VideoToolbox 映射到 bt709。"""
    t0 = max(0.0, dur / 2 - 2.5)
    hw, hh = cw // 2 // 2 * 2, ch // 2 // 2 * 2
    clip = os.path.join(out, f"{name}_hdr_compare.mp4")
    png = os.path.join(out, f"{name}_hdr_compare.png")
    naive = f"[0:v]scale={hw}:{hh}:flags=lanczos,format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709[l]"
    vt = (f"[1:v]scale_vt=w={hw}:h={hh}:color_matrix=bt709:color_primaries=bt709:color_transfer=bt709,"
          f"hwdownload,format=p010le,format=yuv420p[r]")
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-t", "5", "-i", path,
           "-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld",
           "-ss", f"{t0:.3f}", "-t", "5", "-i", path,
           "-filter_complex", f"{naive};{vt};[l][r]hstack=inputs=2[o]", "-map", "[o]", "-an",
           "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", clip]
    r = sh(cmd, check=False)
    if r.returncode != 0:
        return None, f"VideoToolbox 映射失败：{r.stderr.strip()[-300:]}"
    sh(["ffmpeg", "-v", "error", "-y", "-ss", "2.5", "-i", clip, "-frames:v", "1", png])
    return (clip, png), None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--canvas", default="1920x1080")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--color", default="auto", choices=["auto", "sdr", "hlg", "pq", "log"])
    ap.add_argument("--lut", default=None, help="Log 素材用的官方 .cube（--color log 时必填）")
    ap.add_argument("--hdr-method", default=None, choices=["vt"], help="看过 5 秒对比后才加：vt = VideoToolbox 映射")
    ap.add_argument("--fit", default=None, choices=["pad", "crop"], help="源和画幅比例不同时由人定")
    ap.add_argument("--voice-chain", default="on", choices=["on", "off"])
    a = ap.parse_args()

    m = re.fullmatch(r"(\d+)x(\d+)", a.canvas)
    if not m:
        sys.exit("--canvas 写成 1920x1080 或 1080x1920")
    cw, ch = int(m.group(1)), int(m.group(2))
    if a.color == "log" and not (a.lut and os.path.isfile(a.lut)):
        sys.exit("--color log 需要 --lut 指向官方的 Log 转 Rec.709 .cube 文件")
    os.makedirs(a.out, exist_ok=True)

    report, blocked = ["# 源归一报告", "", f"画幅 {cw}x{ch}，CFR {a.fps}，人声链 {'开' if a.voice_chain == 'on' else '关'}：`{VOICE_CHAIN}`", ""], []
    for src in a.src:
        if not os.path.isfile(src):
            sys.exit(f"找不到源文件：{src}")
        name = os.path.splitext(os.path.basename(src))[0]
        info = probe(src)
        with open(os.path.join(a.out, f"{name}.probe.json"), "w") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        vs = [s for s in info["streams"] if s["codec_type"] == "video" and not (s.get("disposition") or {}).get("attached_pic")]
        as_ = [s for s in info["streams"] if s["codec_type"] == "audio"]
        fmt = info["format"]
        dur = float(fmt.get("duration") or 0)
        rows, notes, stop = [], [], []
        report += [f"## {name}", "", f"源：`{os.path.abspath(src)}`", ""]
        if not vs:
            stop.append("没有视频流")
        else:
            v = vs[0]
            rot = rotation(v)
            w, h = int(v["width"]), int(v["height"])
            dw, dh = (h, w) if abs(rot) % 180 == 90 else (w, h)
            rfr, afr = frac(v.get("r_frame_rate")), frac(v.get("avg_frame_rate"))
            vfr = afr > 0 and abs(rfr - afr) / afr > 0.005
            depth = bit_depth(v)
            cls, cdesc = classify(v, fmt.get("tags") or {}, a, src, dur)
            rows += [("编码", f"{v.get('codec_name')} {v.get('profile', '')} {v.get('pix_fmt')}（{depth} bit）"),
                     ("尺寸", f"{w}x{h}，旋转 {rot}°，显示为 {dw}x{dh}"),
                     ("帧率", f"r_frame_rate {v.get('r_frame_rate')}（{rfr:.3f}），avg_frame_rate {v.get('avg_frame_rate')}（{afr:.3f}）" + ("，**VFR**" if vfr else "，CFR")),
                     ("色彩标签", f"matrix {v.get('color_space', '缺失')} / primaries {v.get('color_primaries', '缺失')} / transfer {v.get('color_transfer', '缺失')} / range {v.get('color_range', '缺失')}"),
                     ("色彩判断", cdesc),
                     ("时长", f"{dur:.3f}s"),
                     ("容器标签", ", ".join(f"{k}={val}" for k, val in (fmt.get("tags") or {}).items() if k in ("encoder", "make", "model", "com.apple.quicktime.make", "com.apple.quicktime.model", "major_brand")) or "无")]
            if vs[0].get("tags", {}).get("handler_name"):
                rows.append(("handler", vs[0]["tags"]["handler_name"]))
            same_ar = abs(dw / dh - cw / ch) / (cw / ch) < 0.01
            if not same_ar and not a.fit:
                stop.append(f"源显示比例 {dw}x{dh} 和画幅 {cw}x{ch} 不同：要加边（--fit pad）还是居中裁（--fit crop），由人定")
            if cls in ("unknown", "log?"):
                stop.append(cdesc)
            if cls in ("hlg", "pq") and a.hdr_method is None:
                res, err = hdr_compare(src, a.out, name, v, dur, cw, ch)
                if err:
                    stop.append(f"HDR：{err}；可选：录制时关 HDR，或另行决定换装带 libzimg 的 ffmpeg")
                else:
                    stop.append(f"HDR：已出 5 秒对比 `{res[0]}`（截图 `{res[1]}`），左边不映射、右边 VideoToolbox 映射；看过再决定是否带 --hdr-method vt")
            if rfr and abs(rfr - a.fps) > 0.05 and not vfr:
                notes.append(f"帧率 {rfr:.3f} → {a.fps}：" + ("抽帧" if rfr > a.fps else "补帧（重复帧，动作会有轻微顿挫）"))
            if vfr:
                notes.append(f"VFR（平均 {afr:.3f}）→ CFR {a.fps}：按时间戳重采样，时长不变")
            if v.get("color_range") == "pc":
                notes.append("源是全范围（pc），转成 tv 范围：这是范围换算，不是调色")
        if not as_:
            stop.append("没有音轨：口播片型需要原声")

        if stop:
            report += ["| 项 | 值 |", "|---|---|"] + [f"| {k} | {val} |" for k, val in rows] + [""]
            report += ["**停下，等人定：**", ""] + [f"- {s}" for s in stop] + [""]
            blocked.append(name)
            for s in stop:
                print(f"[{name}] 停下：{s}", file=sys.stderr)
            continue

        # ---- 画面 ----
        vf = []
        hw_in = []
        if cls in ("hlg", "pq"):
            hw_in = ["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld"]
            vf.append(f"scale_vt=color_matrix=bt709:color_primaries=bt709:color_transfer=bt709,hwdownload,format=p010le")
            notes.append("HDR → SDR：VideoToolbox 映射到 bt709（人工看过 5 秒对比后确认）")
        elif cls == "log":
            vf.append(f"lut3d=file='{a.lut}':interp=tetrahedral")
            notes.append(f"Log → Rec.709：套用 LUT `{a.lut}`")
        else:
            notes.append("SDR：不做色彩转换")
        # start_time=0：画面从容器零点起算，和下面音频的 first_pts=0 是同一个零点
        vf.append(f"fps=fps={a.fps}:start_time=0")
        in_mtx = {"smpte170m": "bt601", "bt470bg": "bt601", "bt2020nc": "bt2020"}.get(v.get("color_space"), "bt709")
        in_rng = "pc" if v.get("color_range") == "pc" else "tv"
        fit = fit_filter(dw, dh, cw, ch, a.fit if not same_ar else None)
        vf.append(f"{fit}:in_color_matrix={in_mtx}:in_range={in_rng}:out_color_matrix=bt709:out_range=tv" if cls not in ("hlg", "pq") else fit)
        vf += ["format=yuv420p", "setsar=1", "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"]
        if (dw, dh) != (cw, ch):
            notes.append(f"{dw}x{dh} → {cw}x{ch}" + (f"（{a.fit}）" if a.fit and not same_ar else ""))
        vout = os.path.join(a.out, f"{name}.mp4")
        sh(["ffmpeg", "-v", "error", "-y", *hw_in, "-i", src, "-map", "0:v:0", "-an", "-vf", ",".join(vf),
            "-c:v", "libx264", "-preset", "medium", "-crf", "14", "-g", str(a.fps), "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
            "-movflags", "+faststart", vout])
        nfr = int(sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                      "stream=nb_read_packets", "-of", "csv=p=0", vout]).stdout.strip())
        vdur = nfr / a.fps

        # ---- 声音 ----
        a0 = as_[0]
        chs = int(a0.get("channels") or 1)
        af = []
        if chs >= 2:
            rms = channel_rms(src)
            if len(rms) >= 2 and abs(rms[0] - rms[1]) > 15:
                k = 0 if rms[0] > rms[1] else 1
                af.append(f"pan=mono|c0=c{k}")
                notes.append(f"双声道 RMS {rms[0]:.1f} / {rms[1]:.1f} dB，相差超过 15 dB，只取第 {k + 1} 路")
            else:
                af.append("pan=mono|c0=0.5*c0+0.5*c1")
                notes.append("双声道混成单声道" + (f"（RMS {rms[0]:.1f} / {rms[1]:.1f} dB）" if len(rms) >= 2 else ""))
        # 音画对齐靠解码后的真实时间戳：first_pts=0 让 aresample 在容器零点前补静音、零点前的样本裁掉。
        # 不能用 stream start_time 相减，AAC 的 1024 样本预滚会让它差约 21ms。
        off = first_audio_pts(src) - float(fmt.get("start_time") or 0)
        if abs(off) >= 0.0005:
            notes.append(f"音频首个样本在 {off * 1000:+.0f} ms（相对画面零点），已{'补静音' if off > 0 else '裁掉'}对齐")
        af += ["aresample=48000:async=1:first_pts=0", "apad", f"atrim=end={vdur:.6f}"]
        raw = os.path.join(a.out, f"{name}.raw.wav")
        voice = os.path.join(a.out, f"{name}.voice.wav")
        sh(["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0:a:0", "-af", ",".join(af), "-ac", "1", "-c:a", "pcm_s24le", raw])
        if a.voice_chain == "on":
            lat = chain_latency(VOICE_CHAIN)
            comp = f",atrim=start_sample={lat},asetpts=PTS-STARTPTS,apad,atrim=end={vdur:.6f}" if lat > 0 else ""
            sh(["ffmpeg", "-v", "error", "-y", "-i", raw, "-af", VOICE_CHAIN + comp, "-c:a", "pcm_s24le", voice])
            if lat:
                notes.append(f"人声链固定延迟 {lat} 样本（{lat / 48:.1f}ms），已裁掉补齐，voice.wav 和画面对齐")
        else:
            sh(["ffmpeg", "-v", "error", "-y", "-i", raw, "-c:a", "copy", voice])
        lb, la = loudness(raw), loudness(voice)
        plots(raw, os.path.join(a.out, f"{name}_voice_before"), f"{name} 处理前  I {lb['I']} LUFS  LRA {lb['LRA']} LU  峰值 {lb['TP']} dBFS")
        plots(voice, os.path.join(a.out, f"{name}_voice_after"), f"{name} 人声链后  I {la['I']} LUFS  LRA {la['LRA']} LU  峰值 {la['TP']} dBFS")
        rows += [("中间档", f"`{vout}`，{nfr} 帧 = {vdur:.3f}s"),
                 ("响度 处理前", f"I {lb['I']} LUFS，LRA {lb['LRA']} LU，TP {lb['TP']} dBFS"),
                 ("响度 处理后", f"I {la['I']} LUFS，LRA {la['LRA']} LU，TP {la['TP']} dBFS")]
        report += ["| 项 | 值 |", "|---|---|"] + [f"| {k} | {val} |" for k, val in rows] + [""]
        report += ["做了什么：", ""] + [f"- {n}" for n in notes] + [""]
        report += [f"图：`{name}_voice_before_loudness.png`、`{name}_voice_after_loudness.png`、"
                   f"`{name}_voice_before_spectrum.png`、`{name}_voice_after_spectrum.png`", ""]
        print(f"[{name}] OK  {nfr} 帧  {vdur:.3f}s  响度 {lb['I']} → {la['I']} LUFS", file=sys.stderr)

    rp = os.path.join(a.out, "normalize_report.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(rp)
    if blocked:
        print(f"需要人来定：{', '.join(blocked)}（见报告）", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
