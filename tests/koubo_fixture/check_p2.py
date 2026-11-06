#!/usr/bin/env python3
"""P2 自动验收：transcribe.mjs 的句界、句号、半句、稿外标记，对照夹具真值。

  python3 check_p2.py --truth <夹具>/truth.json --timings <工程>/timings.json [--tol 0.1]

真值来自 compose_fixture.py：每句在源里的起止就是拼接时放下去的位置（TTS 句子去过首尾静音，起止即发声起止）。
每条真值找时间重叠最多的输出句，比较起点、终点误差（都要在 --tol 秒以内），以及稿子句号、半句、稿外标记。
"""
import argparse, json, sys

EXPECT = {"u01": 1, "u02a": 2, "u02": 2, "u03": 3, "x01": None, "u04": 4, "u05": 5, "u06": 6,
          "u07": 7, "u08": 8, "u09": 9, "u10": 10, "u06s": 6}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", required=True)
    ap.add_argument("--timings", required=True)
    ap.add_argument("--tol", type=float, default=0.1)
    # A.mov 的音频比画面晚 39.33ms 开始（P1 check 实测，MOV 按毫秒取整），归一后的时间线以画面为零点，真值要加上它
    ap.add_argument("--offset", default="A=0.03933,B=0,C=0")
    a = ap.parse_args()
    truth = json.load(open(a.truth))
    tim = json.load(open(a.timings))
    ok, errs = True, []
    off = {k: float(v) for k, v in (x.split("=") for x in a.offset.split(","))}
    srcs = [k for k in ("A", "B", "C") if k in truth and k in tim]
    for src in srcs:
        for t in truth[src]:
            t["start"] += off.get(src, 0); t["end"] += off.get(src, 0)
    for src in srcs:
        out = tim[src]["sentences"]
        used = set()
        print(f"== {src}: 真值 {len(truth[src])} 句，输出 {len(out)} 句")
        for t in truth[src]:
            best, bov = None, 0.0
            for s in out:
                ov = min(t["end"], s["end"]) - max(t["start"], s["start"])
                if ov > bov:
                    best, bov = s, ov
            if best is None:
                print(f"   {t['key']:5s} 没有对上的输出句  FAIL"); ok = False; continue
            used.add(best["id"])
            ds, de = best["start"] - t["start"], best["end"] - t["end"]
            want = EXPECT[t["key"]]
            lab_ok = best.get("line") == want
            flag_ok = (want is None and best.get("offscript")) or (want is not None and bool(best.get("partial")) == (t["key"] == "u02a"))
            if t["key"] == "u06s":  # 句内重说要被标成 dup，长度约等于「分镜先给人看，」那截
                dups = best.get("dups") or []
                flag_ok = flag_ok and len(dups) == 1 and 0.8 <= dups[0][1] - dups[0][0] <= 2.0
                print(f"   u06s 句内重说 dups={dups}")
            good = abs(ds) <= a.tol and abs(de) <= a.tol and lab_ok and flag_ok
            ok &= good
            errs += [abs(ds), abs(de)]
            tag = "稿外" if best.get("offscript") else ("半句" if best.get("partial") else "完整")
            print(f"   {t['key']:5s} 真值 {t['start']:6.3f}-{t['end']:6.3f}  输出#{best['id']:<2d} {best['start']:6.3f}-{best['end']:6.3f}  "
                  f"误差 {ds:+.3f}/{de:+.3f}  稿子第 {best.get('line')} 句（期望 {want}）{tag}  {'PASS' if good else 'FAIL'}")
        extra = [s for s in out if s["id"] not in used]
        for s in extra:
            print(f"   多出的输出句 #{s['id']} {s['start']:.3f}-{s['end']:.3f} 「{s['heard']}」  FAIL")
            ok = False
    if errs:
        errs.sort()
        print(f"句界误差：平均 {sum(errs) / len(errs) * 1000:.0f}ms，最大 {errs[-1] * 1000:.0f}ms，容差 {a.tol * 1000:.0f}ms")
    print("P2 ALL PASS" if ok else "P2 FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
