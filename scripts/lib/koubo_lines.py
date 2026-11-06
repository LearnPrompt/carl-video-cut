"""口播成片里定位稿子句和关键词：在 cutmap 的成片逐字时间里按稿子顺序找每句开头。koubo_card.py 和 rough_preview.py 共用。

逐字时间里的字已按稿子纠错，但读音形式会把数字写成汉字、英文转小写，所以只拿汉字（不含数字字）当钥匙。
自由口播时一个转写句常常跨好几句稿子，按句号对应镜号会差好几秒，按字找才准。
"""
NUM = set("零一二两三四五六七八九十百千万亿点")


def keys(s):
    return "".join(c for c in s.lower() if "一" <= c <= "鿿" and c not in NUM)


def stream(sentences, ordered=False):
    """成片逐字流：[(字, 起)]，只留汉字钥匙字，按时间排；ordered=True 时保持传入的句序（几个源的源时间不能混排）。"""
    out = []
    for s in sentences:
        for c, t0, _ in s.get("chars") or []:
            if keys(c):
                out.append((c, t0))
    if not ordered: out.sort(key=lambda x: x[1])
    return out


def find(st, text, pos=0, n=5, window=160):
    """从 pos 起在 window 个字以内找 text：先找开头 n 个钥匙字（退到 4、3 个），再找句中任意 4 个连续钥匙字
    （临场换了开头的说法时兜底，返回的时刻会比真正开口晚一点）。返回 (下标, 时刻) 或 None。"""
    k = keys(text)
    s = "".join(c for c, _ in st)[:pos + window]
    for m in (n, 4, 3):
        if len(k) >= m:
            i = s.find(k[:m], pos)
            if i >= 0: return i, st[i][1]
    hits = [i for q in range(1, max(1, len(k) - 3)) for i in [s.find(k[q:q + 4], pos)] if i >= 0]
    if hits:
        i = min(hits); return i, st[i][1]
    return None


def line_starts(sentences, script, ordered=False):
    """{稿子句号: 成片起点秒}，按稿子顺序单调往后找；没说或没找到的句不给。"""
    st, pos, out = stream(sentences, ordered), 0, {}
    for k, line in enumerate(script, 1):
        text = line["text"] if isinstance(line, dict) else line
        hit = find(st, text, pos)
        if hit:
            out[k] = hit[1]; pos = hit[0] + 1
    return out
