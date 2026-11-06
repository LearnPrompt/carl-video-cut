#!/usr/bin/env python3
"""剪完复核补剪：在剪好的口播上重新转写，再找一遍重说和句内磕巴，换算回原片秒数追加进 takes.json 的 cuts_src。只用 ffmpeg 和标准库。

  python3 refine_cut.py --takes takes.json --cut <工程>/cut --verify <工程>/cut/verify/timings.json [--dry-run]

为什么要有这一步：whisper 在一块里遇到连说两遍的话常只写一遍（自带去重），两遍之间没有停顿时切块再碎也躲不开，
原片转写里这种重复根本不存在，koubo_card.py 也就检测不到。剪掉前后别的话以后上下文变了，重复往往就露出来了。
所以剪完要重转写（transcribe.mjs --src A=<cut>/cut.voice.wav），本脚本在那份转写上按 koubo_card.py 的同一套规则找重说，
连同 transcribe 标出的句内磕巴，切点用剪好片子的逐字时间并吸附到真实停顿（原片的逐字时间在被合并的重复处是拉长的，不能用），
再按 cut/cutmap.json 换算成原片秒数。原片不会变，按原片秒数记的剪辑不会因为重跑转写失效。
然后重跑 cut_takes.py，再转写、再跑本脚本，直到它报告没有新的可剪（一般一两轮）。

输出：takes.json 里的 cuts_src 追加 [{src, from, to, why, text}]（from/to 是原片秒）；stdout 打印一行 JSON（新增几处）。
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import koubo_card  # noqa: E402
import cut_takes  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--takes", required=True); ap.add_argument("--cut", required=True); ap.add_argument("--verify", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cm = json.load(open(os.path.join(a.cut, "cutmap.json"), encoding="utf-8"))
    vt = json.load(open(a.verify, encoding="utf-8"))
    names = [k for k in vt if not k.startswith("_")]
    if len(names) != 1: sys.exit("复核转写应只有一个源（剪好的 cut.voice.wav）")
    V = vt[names[0]]
    S = V["sentences"]
    wav = V.get("file") or os.path.join(a.cut, "cut.voice.wav")
    sil = cut_takes.silences(wav)

    found = []                                                   # (成片起, 成片止, 原因, 文字)
    takes_v, _ = koubo_card.freeform(S, wav)
    by = {s["id"]: s for s in S}
    for k, t in enumerate(takes_v):
        s = by[t["sent"]]
        if t.get("why", "").startswith("打板"): continue          # 复核里不再剪打板（原片剪辑已处理），只找重说
        if not t["keep"]:
            nxt = S[k + 1]["start"] if k + 1 < len(S) else s["end"]
            found.append((s["start"], min(nxt - 0.12, s["end"] + 0.4) if nxt - s["end"] < 1 else s["end"], "复核：重说",
                          s.get("heard") or ""))
        for c0, c1 in t.get("drop") or []:
            ch = s["chars"]
            d0 = s["start"] if c0 <= 0 else (ch[c0 - 1][2] + ch[c0][1]) / 2
            d1 = s["end"] if c1 >= len(ch) else (ch[c1 - 1][2] + ch[c1][1]) / 2
            n0, n1 = cut_takes.snap(d0, d1, sil, ch)
            found.append((n0, n1, "复核：重说", "".join(x[0] for x in ch[c0:c1])))
    for s in S:
        for d0, d1 in s.get("dups") or []:
            n0, n1 = cut_takes.snap(d0, d1, sil, s.get("chars") or [])
            found.append((n0, n1, "复核：句内磕巴", s.get("heard") or ""))

    # 成片秒 → 原片秒（跨段的拆开）
    add = []
    for t0, t1, why, text in sorted(found):
        for m in cm["segments"]:
            m1 = m["t0"] + m["s1"] - m["s0"]
            a0, a1 = max(t0, m["t0"]), min(t1, m1)
            if a1 - a0 > 0.02:
                add.append({"src": m["src"], "from": round(m["s0"] + a0 - m["t0"], 3), "to": round(m["s0"] + a1 - m["t0"], 3),
                            "why": why, "text": text[:40], "final": round(t0, 2)})
    tk = json.load(open(a.takes, encoding="utf-8"))
    old = tk.get("cuts_src", [])
    new = [x for x in add if not any(o["src"] == x["src"] and o["from"] <= x["from"] + 0.05 and o["to"] >= x["to"] - 0.05 for o in old)]
    if not a.dry_run and new:
        tk["cuts_src"] = old + new
        json.dump(tk, open(a.takes, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"new": len(new), "cuts_src": len(old) + (0 if a.dry_run else len(new)),
                      "items": [f"成片 {x['final']}s → 原片 {x['from']}–{x['to']} {x['why']} {x['text']}" for x in new]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
