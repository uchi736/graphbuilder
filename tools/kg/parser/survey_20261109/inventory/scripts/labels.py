"""別表・付表・添付資料・別紙 のラベルの位置(ページ上端からの割合)と字の高さを取る。指定ページを PNG にする。"""
import json, re, sys
from pathlib import Path
import pypdfium2 as pdfium
PAT = re.compile(r"(?<!識)(別表|付表|附表|添付資料|別紙|別図|付図)\s*[-－‐]?\s*[0-9０-９]*")
out = []
for path in sorted(Path("/in/full56").glob("*.pdf")):
    pdf = pdfium.PdfDocument(str(path))
    for i in range(len(pdf)):
        page = pdf[i]; w, h = page.get_size(); tp = page.get_textpage()
        n = tp.count_chars(); text = tp.get_text_range(0, n) if n else ""
        # get_text_range の文字列の位置と文字の添字は一致する前提(改行は \r\n で生成されるため一致しないことがある)ので、search で取り直す
        for m in PAT.finditer(text):
            label = m.group(0).strip()
            sr = tp.search(m.group(1), match_case=True)
            boxes = []
            while True:
                r = sr.get_next()
                if not r: break
                idx, cnt = r
                bx = [tp.get_charbox(idx + k) for k in range(cnt)]
                l = min(b[0] for b in bx); b_ = min(b[1] for b in bx); rr = max(b[2] for b in bx); t = max(b[3] for b in bx)
                after = tp.get_text_range(idx, min(cnt + 30, n - idx)).replace("\r\n", "⏎")
                boxes.append({"x0": round(l / w, 3), "y_top": round(1 - t / h, 3), "height_pt": round(t - b_, 1), "text_after": after})
            sr.close()
            out.append({"file": path.name, "page": i + 1, "label": label, "occurrences": boxes})
            break  # ページごとに語の種類ごと 1 回 search すれば足りるので、1 ページ 1 件(以下で語ごとにまとめる)
        tp.close()
json.dump(out, open("/out/labels.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
# 画像
targets = json.loads(sys.argv[1])
for fn, pg in targets:
    pdf = pdfium.PdfDocument(f"/in/full56/{fn}")
    img = pdf[pg - 1].render(scale=1.4).to_pil()
    img.save(f"/out/png/{Path(fn).stem}_p{pg}.png")
