#!/usr/bin/env python3
"""把分镜脚本表（markdown 表格：镜号 | 口播 | 素材 | 剪辑动作 ...）拆成 稿子.txt 和镜号表。只用标准库。

  python3 script_table.py <脚本.md> --out <工程目录> [--col 口播]

- 口播列按表头里含 --col（默认口播）的那一列取；镜号取第一列。
- 清理：删掉 ~~删除线~~ 里的字、**粗体** 标记、（括号里的拍摄或剪辑说明）、【[链接](...)】 整段；
  其余 【文字】 留下文字（提示语里的占位词口播会念）；[文字](链接) 只留文字；<br> 当换行。
- 输出 稿子.txt：按镜依次写，单元格里的换行保留（换行处也算句界），给 transcribe.mjs 用；镜号表.json：[{line, shot, part, text}]，
  line 和 transcribe.mjs 的 _script 序号一一对应（同样按 。！？ 和换行分句），part 是表格所在的章节标题；
  shots 按镜存：assets（素材列里的本地文件，相对脚本目录、已解码 %20，带 @来源）、bold（口播里加粗的词，叠层关键词候选）、
  material / action / idea（素材列、剪辑动作列、想法列清理后的文字），分镜卡按镜号查。
"""
import argparse, json, os, re, sys
from urllib.parse import unquote

SPLIT = re.compile(r"[^。！？!?\n]+[。！？!?]?")
SPEAKABLE = re.compile(r"[一-鿿A-Za-z0-9]")


def cells(row):
    row = row.strip()
    if row.startswith("|"): row = row[1:]
    if row.endswith("|"): row = row[:-1]
    out, cur, esc = [], "", False
    for ch in row:
        if esc: cur += ch; esc = False; continue
        if ch == "\\": cur += ch; esc = True; continue
        if ch == "|": out.append(cur); cur = ""; continue
        cur += ch
    out.append(cur)
    return [c.strip() for c in out]


def unescape(s):
    return re.sub(r"\\([\\.\-_*\[\]()#+!|~`>])", r"\1", s)


def clean(s):
    s = re.sub(r"~~.*?~~", "", s)
    s = s.replace("<br>", "\n")
    s = re.sub(r"【\s*\[[^\]]*\]\([^)]*\)\s*】", "", s)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = s.replace("**", "")
    s = re.sub(r"（[^（）]*）", "", s)
    s = re.sub(r"【([^】]*)】", r"\1", s)
    s = unescape(s)
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in s.split("\n")]
    return "\n".join(l for l in lines if l)


def assets(cell):
    """素材列里的本地文件，按出现顺序；每条配上它后面最近的 x.com 账号（@来源角标用），抖音链接记作 douyin。"""
    out = []
    for m in re.finditer(r"\]\(([^)]+)\)", cell):
        url = m.group(1)
        if not url.startswith("http"):
            out.append({"path": unquote(unescape(url)), "src": None})
            continue
        h = re.match(r"https?://(?:x|twitter)\.com/([^/]+)/status", url)
        src = "@" + h.group(1) if h else ("douyin" if "douyin.com" in url else None)
        if out and out[-1]["src"] is None and src:
            out[-1]["src"] = src
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("md"); ap.add_argument("--out", required=True); ap.add_argument("--col", default="口播")
    a = ap.parse_args()
    part, col, shots = "", None, []
    for raw in open(a.md, encoding="utf-8"):
        if raw.startswith("#"):
            part = raw.lstrip("#").strip(); col = None; continue
        if not raw.lstrip().startswith("|"):
            continue
        c = cells(raw)
        if col is None:
            hit = [i for i, h in enumerate(c) if a.col in h]
            if hit: col = hit[0]
            continue
        if all(re.fullmatch(r":?-+:?", x) for x in c if x):
            continue
        shot = re.sub(r"<br>.*", "", unescape(c[0])).strip()
        text = clean(c[col]) if col < len(c) else ""
        if text:
            col_text = lambda i: clean(c[i]) if i < len(c) else ""
            bold = [unescape(b).strip("，。、 ") for b in re.findall(r"\*\*(.+?)\*\*", re.sub(r"~~.*?~~", "", c[col]))]
            shots.append({"shot": shot, "part": part, "text": text, "assets": assets(c[col + 1]) if col + 1 < len(c) else [],
                          "bold": [x for x in (re.sub(r"（[^（）]*）", "", b).strip("，。、 ") for b in bold) if x],
                          "material": col_text(col + 1), "action": col_text(col + 2), "idea": col_text(col + 3)})
    if not shots:
        sys.exit(f"没找到表头含 {a.col} 的表格")
    # 素材后面没紧跟原帖链接的，按同一个 M 编号在别的镜里找来源
    known = {re.match(r"(M\d+)", os.path.basename(x["path"])).group(1): x["src"]
             for sh in shots for x in sh["assets"] if x["src"] and re.match(r"M\d+", os.path.basename(x["path"]))}
    for sh in shots:
        for x in sh["assets"]:
            m = re.match(r"(M\d+)", os.path.basename(x["path"]))
            if x["src"] is None and m: x["src"] = known.get(m.group(1))
    os.makedirs(a.out, exist_ok=True)
    rows, n = [], 0
    for s in shots:
        for seg in SPLIT.findall(s["text"]):
            seg = seg.strip()
            if SPEAKABLE.search(seg):
                n += 1; rows.append({"line": n, "shot": s["shot"], "part": s["part"], "text": seg})
    with open(os.path.join(a.out, "稿子.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(s["text"] for s in shots) + "\n")
    with open(os.path.join(a.out, "镜号表.json"), "w", encoding="utf-8") as f:
        json.dump({"source": os.path.abspath(a.md), "lines": rows,
                   "shots": [{k: s[k] for k in ("shot", "part", "assets", "bold", "material", "action", "idea")} for s in shots]},
                  f, ensure_ascii=False, indent=1)
    print(f"{len(shots)} 镜，{n} 句 → {a.out}/稿子.txt、镜号表.json")


if __name__ == "__main__":
    main()
