#!/usr/bin/env python3
"""口播分镜卡（P3）：从转写结果定默认怎么剪，列出每镜的素材和叠层候选，写成一张给用户选的卡。只用 ffmpeg 和标准库。

  python3 koubo_card.py --timings timings.json --shots 镜号表.json --out <工程目录>
        [--cutmap cut/cutmap.json] [--assets-root <脚本目录>] [--title 片名] [--mode auto|scripted|freeform] [--gap 0.6]

两种录法，两套默认（--mode auto 按稿外句占比自动选：超过 15% 算自由口播）：
- scripted 照稿逐句录、说错整句重来：同一句说了几遍，留最后一遍完整的；没有完整的留覆盖最多的那遍；稿外句默认不留。
- freeform 拿稿子当提纲一口气讲、临场改词加话：默认全留，只剪三样：
  1. 重说时被放弃的那一段：后面某处开头的 m 个词和前面一个起句点（句首、停顿后、那/嗯/呃之后）开头的 m 个词
     有 75% 以上一样（英文一串字母算一个词；m 从 8 起，两处每隔 3 秒多比 1 个），或者放弃的整段和后面同样长的一段
     整体 65% 以上的字对得上（换了说法的重说），且相隔不到 25 秒，就剪掉前面那段；
     几遍都是完整整句时留最贴稿子（改字最少）的那遍；
  2. 没有汉字的打板（只有 OK）和录音头尾 3 秒内的杂音；中间的英文句可能是专名或被听成拼音的中文，不剪；
  3. transcribe 标出的句内重说（dups），由 cut_takes.py 剪。
  句间停顿超过 --gap 秒的压到约 0.25 秒，也由 cut_takes.py 执行。

输出（<out>/ 下）：
  takes.json     {takes: [{src, sent, line, keep, drop?, why?}]}，数组顺序就是成片顺序。
                 drop 是句内要剪的字区间 [[从第几个字, 到第几个字)]，下标对应 timings.json 里该句的 chars。
                 不想剪某处：把那句 keep 改回 true、删掉 drop；想多剪：加 drop 或把 keep 改 false。
  rough.json     粗剪预览每镜的版式：full / corner / carl / placeholder（见 rough_preview.py），已存在就不覆盖
  口播分镜卡.md   剪掉的地方或每句留哪遍、没说到的稿子、素材与叠层清单、要你定的事
有 --cutmap 时，素材清单写成片约第几秒（按默认 takes 剪出的粗剪里按字找稿子句首）。
"""
import argparse, bisect, json, os, re, subprocess, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import koubo_lines  # noqa: E402

FILLER = set("那嗯呃啊哎哦唉")
CJK = re.compile(r"[一-鿿]")


def mmss(t):
    if t is None: return "—"
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:04.1f}"


def short(s, n=22):
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


def media_dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except ValueError: return None


def lcs(a, b):
    if not a or not b: return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def corrections(s):
    """听到的和纠错后的字差多少（越少越贴稿子）。"""
    h = re.sub(r"[^\w]", "", (s.get("heard") or "").lower())
    c = "".join(x[0] for x in s.get("chars") or [])
    return max(len(h), len(c)) - lcs(h, c)


def silences(wav, noise_db=-48, dur=0.12):
    r = subprocess.run(["ffmpeg", "-v", "info", "-nostats", "-i", wav, "-af", f"silencedetect=n={noise_db}dB:d={dur}",
                        "-f", "null", "-"], capture_output=True, text=True)
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    return list(zip(starts, ends))


def freeform(S, wav):
    """返回 (takes, cuts)。cuts 是剪掉的地方 [{a, b, text, before, after, why}]，供卡片列表。"""
    # 杂音：没有汉字（数字字不算）的句，且只是打板的 OK，或落在录音开头结尾 3 秒内。
    # 中间别的英文句不算杂音：常是专名或被听成拼音的中文（例如把"树、桥"听成 iSuchiao），留着给人判断
    dur = max((s["end"] for s in S), default=0)
    def is_noise(s):
        txt = "".join(c[0] for c in s.get("chars") or []) + (s.get("heard") or "")
        if koubo_lines.keys(txt): return False
        latin = re.sub(r"[^a-z]", "", txt.lower())
        return re.fullmatch(r"(ok)+", latin or "-") is not None or s["start"] < 3 or s["end"] > dur - 3
    noise = {s["id"] for s in S if is_noise(s)}
    # 逐词流：一串英文字母算一个词（opus、blender 按字母拆开会有七八个字一样，被误当成重说）
    st = []                                                     # [句下标, 首字下标, 词, 起, 止, 末字下标]
    for si, s in enumerate(S):
        if s["id"] in noise: continue
        for k, (c, t0, t1) in enumerate(s.get("chars") or []):
            c = c.lower()
            if c.isascii() and c.isalpha() and st and st[-1][0] == si and st[-1][5] == k - 1 and st[-1][2].isascii() and st[-1][2].isalpha():
                p_ = st[-1]; st[-1] = (si, p_[1], p_[2] + c, p_[3], t1, k)
            else:
                st.append((si, k, c, t0, t1, k))
    sil = silences(wav) if wav and os.path.exists(wav) else []
    sil_end = [b for _, b in sil]
    phrase = set()
    for i, it in enumerate(st):
        if i == 0 or it[1] == 0 or st[i - 1][2] in FILLER or st[i - 1][2] == "ok":
            phrase.add(i); continue
        j = bisect.bisect_left(sil_end, st[i - 1][4] - 0.05)     # 前一字结束之后、本字开口附近有停顿结束
        if j < len(sil_end) and sil_end[j] <= it[3] + 0.08:
            phrase.add(i)
    # 比较时跳过单个英文字母：whisper 常在停顿里幻听出一个 a、i，夹在两遍之间会把重说挡住；剪的时候它们照样被包进去
    real = [k for k, x in enumerate(st) if not (len(x[2]) == 1 and x[2].isascii() and x[2].isalpha())]
    rpos = {k: q for q, k in enumerate(real)}
    rw = [st[k][2] for k in real]
    drops = []
    for qj in range(2, len(real)):
        j, best = real[qj], None
        for qi in range(qj - 2, max(-1, qj - 120), -1):
            i = real[qi]
            if st[j][3] - st[i][3] > 25: break
            if i not in phrase or rw[qi] != rw[qj]: continue
            if qj + 1 < len(rw) and rw[qi + 1] != rw[qj + 1]: continue
            n = qj - qi
            m = min(8 + int((st[j][3] - st[i][3]) / 3), n, len(rw) - qj)   # 隔得越远比得越长：真重说越比越像，撞词的越比越不像
            if m < 3: continue
            r = lcs(rw[qi:qi + m], rw[qj:qj + m]) / m
            whole = n >= 6 and lcs(rw[qi:qj], rw[qj:qj + n]) / n >= 0.65   # 换了说法的重说：放弃的整段和后面同样长的一段整体比
            short = n <= 4 and r >= 0.6                         # 三四个词的磕巴（画面跟、画面和声音）：开头两个词已相同，六成就算
            if r >= 0.75 or whole or short: best = i            # 往前找最远的那个起点，连续几遍重说一次剪干净
        if best is not None: drops.append([best, j])
    # 起点落在已剪掉那段里面的匹配，是新的一遍把整段又说了一次，不能再往后扩（否则会把新的那遍也剪掉）
    merged = []
    for a, b in sorted(drops):
        if merged and merged[-1][0] < a < merged[-1][1] and b > merged[-1][1]: continue
        if merged and a <= merged[-1][1]: merged[-1][1] = max(merged[-1][1], b)
        else: merged.append([a, b])
    # 几遍都是完整整句：留最贴稿子的那遍
    whole = {}
    for a, b in merged:
        if st[a][1] != 0 or b >= len(st) or st[b][1] != 0: continue
        sis = sorted({st[x][0] for x in range(a, b)})
        if any(sum(st[x][5] - st[x][1] + 1 for x in range(a, b) if st[x][0] == si) != len(S[si]["chars"]) for si in sis): continue
        cand = sis + [st[b][0]]
        lines = {S[si].get("line") for si in cand}
        if len(lines) == 1 and None not in lines and not any(S[si].get("partial") for si in cand):
            keep = min(cand, key=lambda si: (corrections(S[si]), -si))
            if keep != st[b][0]: whole[(a, b)] = [si for si in cand if si != keep]
    dropped = {}                                                 # 句下标 -> set(字下标)
    cuts = []
    for a, b in merged:
        if (a, b) in whole:
            for si in whole[(a, b)]: dropped.setdefault(si, set()).update(range(len(S[si]["chars"])))
            keep = [si for si in sorted({st[x][0] for x in range(a, b)} | {st[b][0]}) if si not in whole[(a, b)]][0]
            cuts.append({"a": st[a][3], "b": S[st[b][0]]["end"], "why": f"同一句录了 {len(whole[(a, b)]) + 1} 遍，留最贴稿子的 #{S[keep]['id']}",
                         "text": " / ".join("".join(c[0] for c in S[si]["chars"]) for si in whole[(a, b)]), "before": "", "after": ""})
            continue
        for x in range(a, b): dropped.setdefault(st[x][0], set()).update(range(st[x][1], st[x][5] + 1))
        cuts.append({"a": st[a][3], "b": st[b][3], "why": "重说，剪掉前面放弃的那段",
                     "text": "".join(x[2] for x in st[a:b]), "before": "".join(x[2] for x in st[max(0, a - 6):a]),
                     "after": "".join(x[2] for x in st[b:b + 8])})
    takes = []
    for si, s in enumerate(S):
        t = {"src": None, "sent": s["id"], "line": s.get("line"), "keep": True}
        if s["id"] in noise:
            t["keep"] = False; t["why"] = "打板或头尾杂音"
            cuts.append({"a": s["start"], "b": s["end"], "why": t["why"], "text": s.get("heard") or "", "before": "", "after": ""})
        elif si in dropped:
            ks = sorted(dropped[si])
            if len(ks) >= len(s["chars"]):
                t["keep"] = False; t["why"] = "重说"
            else:
                rng = []
                for k in ks:
                    if rng and k == rng[-1][1]: rng[-1][1] = k + 1
                    else: rng.append([k, k + 1])
                t["drop"] = rng; t["why"] = "句内有一段重说"
        takes.append(t)
    cuts.sort(key=lambda c: c["a"])
    return takes, cuts


def scripted(allS, nlines):
    """allS：[(源名, 句)]，按录音顺序（源按命令行顺序，源内按时间）。所有源放在一起挑，同一句只留一遍。"""
    by_line = {}
    for src, s in allS:
        if s.get("line"): by_line.setdefault(s["line"], []).append((src, s))
    takes, picks = [], {}
    for L in range(1, nlines + 1):
        c = by_line.get(L, [])
        if not c: continue
        full = [x for x in c if not x[1].get("partial")]
        pick = full[-1] if full else max(c, key=lambda x: x[1].get("coverage", 0))
        picks[L] = pick
        takes.append({"src": pick[0], "sent": pick[1]["id"], "line": L, "keep": True})
        takes += [{"src": x[0], "sent": x[1]["id"], "line": L, "keep": False} for x in c if x is not pick]
    for src, s in allS:                                          # 稿外句插在录音里它前面那句的条目后面
        if s.get("line"): continue
        best = max([k for k, t in enumerate(takes) if t["src"] == src and t["sent"] < s["id"]], key=lambda k: takes[k]["sent"], default=-1)
        pos = best + 1
        while pos < len(takes) and not takes[pos]["keep"] and best >= 0 and takes[pos]["line"] == takes[best]["line"]:
            pos += 1
        takes.insert(pos, {"src": src, "sent": s["id"], "line": None, "keep": False, "why": "稿外"})
    return takes, by_line, picks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timings", required=True); ap.add_argument("--shots", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--cutmap")
    ap.add_argument("--assets-root"); ap.add_argument("--title", default="口播")
    ap.add_argument("--mode", default="auto", choices=["auto", "scripted", "freeform"])
    ap.add_argument("--gap", type=float, default=0.6)
    a = ap.parse_args()

    tj = json.load(open(a.timings, encoding="utf-8"))
    sm = json.load(open(a.shots, encoding="utf-8"))
    script = tj["_script"]
    line_shot = {r["line"]: r["shot"] for r in sm["lines"]}
    if len(sm["lines"]) != len(script):
        print(f"[warn] 镜号表 {len(sm['lines'])} 句，timings 的稿子 {len(script)} 句，镜号可能错位", file=sys.stderr)
    root = a.assets_root or os.path.dirname(sm["source"])
    srcs = [(k, v) for k, v in tj.items() if not k.startswith("_")]
    allS = [s for _, v in srcs for s in v["sentences"]]
    off_ratio = sum(1 for s in allS if not s.get("line")) / max(1, len(allS))
    mode = a.mode if a.mode != "auto" else ("freeform" if off_ratio > 0.15 else "scripted")

    takes, cuts, by_line, picks = [], [], {}, {}
    if mode == "freeform":                                       # 自由口播：各源各自按录音顺序全留、剪重说，源按命令行顺序接
        for name, v in srcs:
            t, c = freeform(v["sentences"], v.get("file"))
            for x in c: x["src"] = name
            for x in t: x["src"] = name
            cuts += c; takes += t
    else:
        takes, by_line, picks = scripted([(name, s) for name, v in srcs for s in v["sentences"]], len(script))
    os.makedirs(a.out, exist_ok=True)
    rule = ("数组顺序是成片顺序；keep 改 true/false 决定留不留这句；drop 是句内要剪的字区间（对应 timings.json 该句 chars 的下标）；"
            "pad_in/pad_out 可单独给某句加减气口（秒）；cuts_src 是按原片秒指定要剪的区间（复核补剪、人工指定）")
    tp = os.path.join(a.out, "takes.json")
    keep_cuts = json.load(open(tp, encoding="utf-8")).get("cuts_src", []) if os.path.exists(tp) else []
    if keep_cuts: print(f"[note] 保留 takes.json 里已有的 {len(keep_cuts)} 处 cuts_src（复核补剪或人工指定）", file=sys.stderr)
    json.dump({"_rule": rule, "_mode": mode, "takes": takes, "cuts_src": keep_cuts}, open(tp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    cm = json.load(open(a.cutmap, encoding="utf-8")) if a.cutmap else None
    # 每句稿子在成片里的起点（按字找），每镜起点取镜内最早找到的那句
    ls = koubo_lines.line_starts(cm["sentences"], script) if cm else {}
    shots = sm["shots"]
    shot_lines = {}
    for r in sm["lines"]: shot_lines.setdefault(r["shot"], []).append(r["line"])
    starts = []
    for s in shots:
        ts = [ls[L] for L in shot_lines.get(s["shot"], []) if L in ls]
        starts.append((s["shot"], min(ts) if ts else None))
    win = {}
    for k, (sh, t0) in enumerate(starts):
        nxt = next((t for _, t in starts[k + 1:] if t is not None), cm["duration"] if cm else None)
        win[sh] = (t0, nxt)
    st_final = koubo_lines.stream(cm["sentences"]) if cm else []
    def kw_time(sh, phrase):
        t0 = win[sh][0]
        if t0 is None: return None
        pos = next((i for i, x in enumerate(st_final) if x[1] >= t0 - 0.05), 0)
        hit = koubo_lines.find(st_final, phrase, pos, n=4, window=200)
        return hit[1] if hit else None
    # 语速偏慢的保留句：whisper 遇到连说两遍的话常只写一遍，没写出来的重复后面检测不到，
    # 只能从"有声时长对不上字数"看出来。每秒字数低于全片中位数 72% 的列出来请人听
    tk_by = {(t["src"], t["sent"]): t for t in takes}
    fin = {(x["src"], x["sent"]): x.get("t0") for x in cm["sentences"]} if cm else {}
    slow = []
    for name, v in srcs:
        sil = silences(v["file"]) if v.get("file") and os.path.exists(v["file"]) else []
        rates = []
        for s in v["sentences"]:
            t = tk_by.get((name, s["id"]))
            if not t or not t["keep"]: continue
            vo = (s["end"] - s["start"]) - sum(max(0.0, min(s["end"], b) - max(s["start"], a)) for a, b in sil)
            n = len(re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s.get("heard") or ""))
            if vo > 1.5: rates.append((n / vo, vo, name, s))
        if rates:
            med = sorted(r[0] for r in rates)[len(rates) // 2]
            slow += [(r, vo, med, nm, x) for r, vo, nm, x in rates if r < 0.72 * med]
    # 没说到的稿子句：按成片顺序（takes 顺序）把保留的字接起来，找不到开头的稿子句。几个源的源时间不能混排
    kept_sents, byid = [], {(name, s["id"]): s for name, v in srcs for s in v["sentences"]}
    for t in takes:
        if not t["keep"]: continue
        s = byid[(t["src"], t["sent"])]
        dk = {k for r0, r1 in t.get("drop", []) for k in range(r0, r1)}
        kept_sents.append({"chars": [c for k, c in enumerate(s.get("chars") or []) if k not in dk]})
    said = koubo_lines.line_starts(kept_sents, script, ordered=True)
    unsaid = [L for L in range(1, len(script) + 1) if L not in said]

    # 粗剪版式
    rough = []
    for s in shots:
        assets = [x for x in s["assets"] if not os.path.basename(x["path"]).startswith("Winhao_")]  # 完整原片只供选段
        need = re.search(r"待补充|@carl", s["material"] + s["action"])
        if "真人出镜" in s["idea"] and "左上角" in s["idea"]: lay = "corner"
        elif assets: lay = "full"
        elif s["material"].strip(): lay = "placeholder"
        else: lay = "carl"
        note = re.sub(r"\s+", " ", re.sub(r"https?://\S+", "", s["material"]))[:60] if (lay == "placeholder" or need) else ""
        rough.append({"shot": s["shot"], "layout": lay, "assets": [{"path": x["path"], "src": x["src"], "in": 0} for x in assets],
                      "note": note})
    rp = os.path.join(a.out, "rough.json")
    if os.path.exists(rp):
        print(f"[note] {rp} 已存在，保留上一轮的改动，不覆盖", file=sys.stderr)
        rough = json.load(open(rp, encoding="utf-8"))["shots"]
    else:
        json.dump({"_rule": "layout 取 full / corner / carl / placeholder；assets 的 in 是素材入点（秒）；改完重跑 rough_preview.py",
                   "shots": rough}, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    rough_by = {r["shot"]: r for r in rough}

    # ---------- 写卡 ----------
    md = [f"# 口播分镜卡：{a.title}", ""]
    for k, v in srcs:
        md.append(f"- 源 {k}：`{os.path.basename(v.get('file', ''))}`，{mmss(v['duration'])}，转写出 {len(v['sentences'])} 句"
                  f"（稿外 {sum(1 for s in v['sentences'] if not s.get('line'))}）")
    md.append(f"- 稿子 {len(script)} 句，{len(shots)} 镜；录法判断：**{'自由口播（稿子当提纲）' if mode == 'freeform' else '照稿逐句录'}**"
              f"（稿外句占 {off_ratio:.0%}）")
    if cm:
        tot = sum(v["duration"] for _, v in srcs)
        md.append(f"- 按默认剪完约 **{mmss(cm['duration'])}**（原始 {mmss(tot)}），{len(cm['segments'])} 段")
    md.append("")
    if mode == "freeform":
        md += ["默认规则：你讲的话默认全留（临场加的连接话也留），只剪重说时放弃的那段、打板杂音、句内磕巴；句间停顿超过 "
               f"{a.gap:g} 秒压到约 0.25 秒。哪处不该剪、还有哪处该剪，说编号就行。", "",
               "## 一、剪掉的地方", "", "| 编号 | 源时刻 | 剪掉的 | 前文…｜后文 | 原因 |", "|---|---|---|---|---|"]
        cuts = sorted(cuts + [{"a": x["from"], "b": x["to"], "text": x.get("text", ""), "before": "", "after": "",
                               "why": x.get("why", "按原片秒指定")} for x in keep_cuts], key=lambda c: c["a"])
        for n, c in enumerate(cuts, 1):
            md.append(f"| {n} | {mmss(c['a'])}–{mmss(c['b'])} | {short(c['text'], 30)} | {c['before']}…｜{c['after']} | {c['why']} |")
        if not cuts: md.append("| — | — | 没有检测到重说 | — | — |")
    else:
        md += ["默认规则：同一句说了几遍留最后一遍完整的；稿外句不留；句内重说剪掉；句间停顿超过 "
               f"{a.gap:g} 秒压到约 0.25 秒。想换就改 `takes.json` 里的 keep。", "",
               "## 一、每句留哪一遍", "", "| 稿 | 镜 | 稿子 | 遍数 | 默认留 | 其他遍 | 说明 |", "|---|---|---|---|---|---|---|"]
        for L in range(1, len(script) + 1):
            sh, c = line_shot.get(L, "?"), by_line.get(L, [])
            if L not in picks:
                md.append(f"| {L} | {sh} | {short(script[L - 1]['text'])} | 0 | **没找到** | — | |"); continue
            src, s = picks[L]
            others = [f"{x[0]}#{x[1]['id']} {mmss(x[1]['start'])}" + ("（半句）" if x[1].get("partial") else "") for x in c if x[1] is not s]
            tags = ([f"只有半句 {round(s.get('coverage', 0) * 100)}%"] if s.get("partial") else []) + \
                   ([f"句内重说 {len(s['dups'])} 处已剪"] if s.get("dups") else [])
            md.append(f"| {L} | {sh} | {short(script[L - 1]['text'])} | {len(c)} | {src}#{s['id']} {mmss(s['start'])}–{mmss(s['end'])} | "
                      f"{'；'.join(others) or '—'} | {'；'.join(tags)} |")
    md += ["", "### 语速偏慢的句（可能有没转写出来的重复，听一下）", ""]
    if slow:
        md += [f"全片中位数每秒 {slow[0][2]:.1f} 字，下面这些保留句明显偏慢：可能是说得慢、中间犹豫，"
               "也可能连说了两遍但转写只写了一遍。听到重复就告诉我成片时刻。", "",
               "| 成片 | 源时刻 | 有声 | 字/秒 | 听到的 |", "|---|---|---|---|---|"]
        for r, vo, med, nm, x in slow:
            md.append(f"| {mmss(fin.get((nm, x['id'])))} | {mmss(x['start'])}–{mmss(x['end'])} | {vo:.1f}s | {r:.1f} | {short(x.get('heard') or '', 30)} |")
    else:
        md.append("没有，保留句的语速都在正常范围。")
    dupn = sum(len(s.get("dups") or []) for s in allS)
    if dupn: md += ["", f"另有句内磕巴 {dupn} 处（转写时标出的回到分句开头重说），剪辑时自动剪掉，不单列。"]

    md += ["", "## 二、稿子里没说到的句", ""]
    if unsaid:
        md.append("在保留下来的话里按字找不到开头的稿子句。多数是临场换了说法（意思说到了），也可能是真漏了，对一下：")
        md.append("")
        for L in unsaid: md.append(f"- 稿 {L}（镜 {line_shot.get(L, '?')}）：{script[L - 1]['text']}")
    else:
        md.append("没有，稿子每句都说到了。")

    md += ["", "## 三、素材与叠层（marks）", "",
           "成片时间来自按默认剪出的粗剪，在逐字时间里按稿子句首找；换了剪法重跑本脚本更新。编号沿用脚本的 M 号，当作素材台账号。", "",
           "| 镜 | 成片 | 长 | 粗剪版式 | 素材（来源，时长） | 叠层关键词（约第几秒） | 脚本里的动作 |", "|---|---|---|---|---|---|---|"]
    for s in shots:
        t0, t1 = win[s["shot"]]
        r = rough_by.get(s["shot"], {})
        al = []
        for x in r.get("assets", []):
            d = media_dur(os.path.join(root, x["path"])) if x["path"].lower().endswith((".mp4", ".mov")) else None
            nm = re.sub(r"[｜|].*", "", os.path.basename(x["path"]))
            al.append(f"{nm}（{x['src'] or '来源待补'}{'，%.1fs' % d if d else '，图'}）")
        kws = [f"{b}（{mmss(kw_time(s['shot'], b))}）" for b in s["bold"]]
        act = short(re.sub(r"构图示意[^\n]*\n?", "", s["action"]) or s["material"], 40)
        lay = {"full": "素材全屏", "corner": "真人+左上小窗", "carl": "真人出镜", "placeholder": "占位卡"}.get(r.get("layout"), "?")
        if r.get("note"): lay += f"：{short(r['note'], 24)}"
        md.append(f"| {s['shot']} | {mmss(t0)} | {('%.1fs' % (t1 - t0)) if t0 is not None and t1 is not None else '—'} | {lay} | "
                  f"{'<br>'.join(al) or '—'} | {'<br>'.join(kws) or '—'} | {act} |")
    md += ["", "## 四、要你定的", ""]
    if mode == "freeform":
        md += ["1. 第一节剪掉的地方，有不该剪的说编号；粗剪里听到还该剪的，说大概第几秒。"]
    else:
        md += ["1. 第一节每句留的那一遍，有要换的说稿号和换成哪遍（例如 稿 12 换 A#40）。"]
    md += ["2. 第二节没说到的稿子句，是改了说法还是要补录。",
           "3. 第三节的版式：粗剪里只是把素材按顺序铺上，真正的叠层动效（关键词花字、分屏、要点卡）等这一版定了再写代码。",
           "4. 标了占位卡或来源待补的镜，素材谁来补。", ""]
    open(os.path.join(a.out, "口播分镜卡.md"), "w", encoding="utf-8").write("\n".join(md))
    print(json.dumps({"mode": mode, "takes": len(takes), "kept": sum(t["keep"] for t in takes),
                      "partial_drops": sum(1 for t in takes if t.get("drop")), "cuts": len(cuts), "unsaid": unsaid,
                      "card": os.path.join(a.out, "口播分镜卡.md")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
