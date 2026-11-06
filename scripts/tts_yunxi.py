#!/usr/bin/env python3
"""逐句 edge-tts 配音 + 去首尾静音 + 句间补停顿，输出每段 wav 与 timings.json。

输入 narration.txt：每行 `key|整段文字`；空行与 # 开头的行忽略。
文字按 。！？ 拆句，逐句合成；每句 ffmpeg silenceremove 正反各一遍去首尾静音，
句间统一补 --gap 秒静音（句间停顿只由 gap 控制）。

输出：
  <out>/aud/<key>.wav        该 key 的整段（每句后都补了 gap，含末尾 gap）
  <out>/timings.json         {key: {duration, sentences: [{text, start, end}]}}
                             text 为去掉句末标点的字幕文本；duration 为 wav 实测时长

edge-tts 查找顺序：--edge-tts > $EDGE_TTS > mvenv/bin/edge-tts 候选
  (~/Downloads/mvenv, ~/mvenv, 脚本上两级/mvenv) > PATH 中的 edge-tts > python3 -m edge_tts > uvx edge-tts
注意：写本脚本时没找到现成的 mvenv，若你有，用 --edge-tts 指过去。
"""
import argparse, json, os, re, shutil, subprocess, sys, time

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
MVENV_CANDIDATES = [
    os.path.join(HOME, "Downloads", "mvenv", "bin", "edge-tts"),
    os.path.join(HOME, "mvenv", "bin", "edge-tts"),
    os.path.join(HERE, "..", "..", "mvenv", "bin", "edge-tts"),
]


def resolve_edge_tts(explicit):
    """返回 edge-tts 命令前缀（list）。"""
    if explicit:
        if not os.path.exists(explicit) and not shutil.which(explicit):
            sys.exit(f"--edge-tts 指定的路径不存在：{explicit}")
        return [explicit]
    env = os.environ.get("EDGE_TTS")
    if env:
        return [env]
    for c in MVENV_CANDIDATES:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return [c]
    w = shutil.which("edge-tts")
    if w:
        return [w]
    probe = subprocess.run([sys.executable, "-c", "import edge_tts"], capture_output=True)
    if probe.returncode == 0:
        return [sys.executable, "-m", "edge_tts"]
    if shutil.which("uvx"):
        return ["uvx", "edge-tts"]
    sys.exit("找不到 edge-tts。用 --edge-tts /path/to/venv/bin/edge-tts 指定，或 `uv tool install edge-tts`。")


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def probe_dur(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    return float(out)


def split_sentences(text):
    return [s.strip() for s in re.findall(r"[^。！？]+[。！？]?", text) if s.strip()]


def synth(edge, voice, rate, text, mp3, retries=3):
    cmd = edge + ["--voice", voice, f"--rate={rate}", "--text", text, "--write-media", mp3]
    for i in range(retries):
        try:
            run(cmd, capture_output=True)
            return
        except subprocess.CalledProcessError as e:
            if i == retries - 1:
                sys.exit(f"edge-tts 失败（已重试 {retries} 次）：{text!r}\n{e.stderr.decode(errors='ignore')[-400:]}")
            time.sleep(1.5)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", default="narration.txt", help="narration.txt 路径")
    ap.add_argument("--out", default=".", help="输出目录（生成 aud/ 与 timings.json）")
    ap.add_argument("--voice", default="zh-CN-YunxiNeural")
    ap.add_argument("--rate", default="+5%", help="语速，如 +5%% / -10%%")
    ap.add_argument("--gap", type=float, default=0.35, help="句间停顿秒数")
    ap.add_argument("--edge-tts", default=None, help="edge-tts 可执行文件路径（覆盖默认查找）")
    a = ap.parse_args()

    edge = resolve_edge_tts(a.edge_tts)
    aud = os.path.join(a.out, "aud")
    parts_dir = os.path.join(aud, "_parts")
    os.makedirs(parts_dir, exist_ok=True)
    sil = os.path.join(parts_dir, "sil.wav")
    run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", str(a.gap), sil])

    result = {}
    with open(a.inp, encoding="utf-8") as fh:
        lines = [l.strip() for l in fh]
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if "|" not in line:
            sys.exit(f"格式错误（需要 key|文字）：{line[:40]}")
        key, text = line.split("|", 1)
        key = key.strip()
        sents = split_sentences(text)
        parts, timeline, t = [], [], 0.0
        for i, s in enumerate(sents):
            mp3 = os.path.join(parts_dir, f"{key}_{i}.mp3")
            wav = os.path.join(parts_dir, f"{key}_{i}.wav")
            synth(edge, a.voice, a.rate, s, mp3)
            run(["ffmpeg", "-loglevel", "error", "-y", "-i", mp3, "-af",
                 "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
                 "silenceremove=start_periods=1:start_threshold=-50dB,areverse",
                 "-ar", "44100", "-ac", "1", wav])
            d = probe_dur(wav)
            timeline.append({"text": s.rstrip("。！？"), "start": round(t, 3), "end": round(t + d, 3)})
            parts.append(wav)
            t += d + a.gap
        lst = os.path.join(parts_dir, f"{key}.txt")
        with open(lst, "w") as f:
            for p in parts:
                f.write(f"file '{os.path.abspath(p)}'\nfile '{os.path.abspath(sil)}'\n")
        final = os.path.join(aud, f"{key}.wav")
        run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", final])
        result[key] = {"duration": round(probe_dur(final), 3), "sentences": timeline}
        print(f"{key}: {result[key]['duration']:.2f}s, {len(sents)} 句", file=sys.stderr)

    tj = os.path.join(a.out, "timings.json")
    with open(tj, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(tj)


if __name__ == "__main__":
    main()
