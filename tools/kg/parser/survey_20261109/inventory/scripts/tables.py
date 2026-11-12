import json
from pathlib import Path
import pymupdf
out = []
for path in sorted(Path("/in/full56").glob("*.pdf")):
    with pymupdf.open(str(path)) as doc:
        for i, page in enumerate(doc, start=1):
            W, H = page.rect.width, page.rect.height
            for t in page.find_tables().tables:
                x0, y0, x1, y1 = t.bbox
                out.append({"file": path.name, "page": i, "bbox": [round(x0 / W, 3), round(y0 / H, 3), round(x1 / W, 3), round(y1 / H, 3)],
                            "rows": t.row_count, "cols": t.col_count})
json.dump(out, open("/out/tables_pymupdf.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
