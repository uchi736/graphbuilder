"""pypdfium2 でページ画像(長辺 1755px)と文字層(ページ全文+文字の矩形)を作る。"""
import json, sys
from pathlib import Path
import pypdfium2 as pdfium

LONG = 1755
src, img_root, txt_root = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
names = sys.argv[4:]
for name in names:
    pdf = pdfium.PdfDocument(str(src / name))
    stem = Path(name).stem
    out = img_root / stem
    out.mkdir(parents=True, exist_ok=True)
    pages = []
    for i in range(len(pdf)):
        page = pdf[i]
        w, h = page.get_size()
        scale = LONG / max(w, h)
        img = page.render(scale=scale).to_pil()
        img.save(out / f"{stem}_p{i+1:03d}.png")
        tp = page.get_textpage()
        text = tp.get_text_bounded()
        # 文字ごとの矩形(PDF 座標、左下原点)を、行単位でまとめる代わりに文字と矩形の並びで保存
        n = tp.count_chars()
        chars = []
        for k in range(n):
            try:
                box = tp.get_charbox(k, loose=False)
            except Exception:
                box = None
            chars.append(box)
        pages.append({"page": i + 1, "width": w, "height": h, "rotation": page.get_rotation(),
                      "img_size": list(img.size), "text": text, "n_chars": n,
                      "charboxes": chars})
        tp.close(); page.close()
    json.dump({"pdf": name, "pages": pages}, open(txt_root / f"{stem}.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(name, len(pages), "pages", [p["img_size"] for p in pages[:2]], [p["rotation"] for p in pages])
