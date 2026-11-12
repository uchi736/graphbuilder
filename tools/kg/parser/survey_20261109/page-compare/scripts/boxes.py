"""pymupdf4llm のページごとのブロック区分と、PyMuPDF の文字層を出す(今の変換の内部の確認用)。"""
import json, sys
from pathlib import Path
import pymupdf, pymupdf4llm
src, out = Path(sys.argv[1]), Path(sys.argv[2])
for name in sys.argv[3:]:
    stem = Path(name).stem
    pages = pymupdf4llm.to_markdown(str(src / name), page_chunks=True, use_ocr=False, show_progress=False)
    res = []
    with pymupdf.open(str(src / name)) as doc:
        for i, page in enumerate(pages):
            text = page.get("text") or ""
            boxes = [{"class": b.get("class"), "bbox": b.get("bbox"), "text": text[b["pos"][0]:b["pos"][1]] if b.get("pos") else ""} for b in page.get("page_boxes") or []]
            res.append({"page": i + 1, "boxes": boxes, "mupdf_text": doc[i].get_text()})
    json.dump(res, open(out / f"{stem}.boxes.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(stem, "ok")
