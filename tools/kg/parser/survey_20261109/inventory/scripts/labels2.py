"""ラベル(別表・付表・添付資料・別紙・図・表)の出現を、字間の空白を許して全件取る。位置(上端からの割合)・字の高さ・直後の文字も取る。"""
import json, re, unicodedata
from pathlib import Path
import pypdfium2 as pdfium
SP = r"[\s　]*"
KINDS = ["別表", "付表", "附表", "添付資料", "別紙", "別図", "付図", "図", "表"]
NUM = r"[0-9０-９ⅠⅡⅢⅣⅤ一二三四五六七八九十]+(?:[-－‐ー][0-9０-９]+)?"
def kpat(k):
    return SP.join(map(re.escape, k))
PAT = re.compile(r"(?<![識別付附一覧覧系統線])(" + "|".join(kpat(k) for k in KINDS) + r")" + SP + r"[-－‐]?" + SP + "(" + NUM + ")?")
out = []
for path in sorted(Path("/in/full56").glob("*.pdf")):
    pdf = pdfium.PdfDocument(str(path))
    for i in range(len(pdf)):
        page = pdf[i]; w, h = page.get_size(); tp = page.get_textpage()
        n = tp.count_chars()
        chars = [tp.get_text_range(k, 1) for k in range(n)]
        text = "".join(chars)
        for m in PAT.finditer(text):
            kind = re.sub(r"\s", "", m.group(1)); num = m.group(2)
            if kind in ("図", "表") and not num:
                continue
            if kind == "表" and text[max(0, m.start()-2):m.start()] and re.search(r"[別付附覧]", text[m.start()-1:m.start()] or ""):
                continue
            s, e = m.start(), m.end()
            bx = [tp.get_charbox(k) for k in range(s, e) if chars[k].strip()]
            if not bx:
                continue
            l = min(b[0] for b in bx); b_ = min(b[1] for b in bx); r = max(b[2] for b in bx); t = max(b[3] for b in bx)
            hs = sorted(b[3] - b[1] for b in bx)
            line_after = re.split(r"[\r\n]", text[e:e + 60])[0]
            line_before = re.split(r"[\r\n]", text[max(0, s - 40):s])[-1]
            out.append({"file": path.name, "page": i + 1, "kind": kind,
                        "num": unicodedata.normalize("NFKC", num) if num else None,
                        "y_top": round(1 - t / h, 3), "x0": round(l / w, 3), "char_h_pt": round(hs[len(hs)//2], 1),
                        "before": line_before.strip()[-20:], "after": line_after.strip()[:40]})
        tp.close()
json.dump(out, open("/out/labels_all.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(out))
