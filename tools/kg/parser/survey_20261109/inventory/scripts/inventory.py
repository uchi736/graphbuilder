"""PDF 62 本の性質の棚卸し(pypdfium2)。表の数の見当だけ PyMuPDF の find_tables を使う(pymupdf4llm は読み込まない)。

入力: /in/full56/*.pdf, /in/prod6/*.pdf  出力: /out/inventory_raw.json, /out/pagetext.jsonl
"""
import hashlib, json, re, sys, unicodedata
from pathlib import Path
import pypdfium2 as pdfium
import pypdfium2.raw as raw

KEYWORDS = ["別表", "付表", "添付資料"]
EXTRA_KEYWORDS = ["附表", "別紙", "別図", "付図", "附図", "付属書", "附属書", "付録", "添付", "参考資料"]
PAPER = {"A4": (210, 297), "A3": (297, 420), "B4": (257, 364), "B5": (182, 257), "A5": (148, 210), "Letter": (216, 279), "Legal": (216, 356), "B4(ISO)": (250, 353)}
PT2MM = 25.4 / 72


def paper(wmm, hmm):
    a, b = sorted((wmm, hmm))
    for name, (x, y) in PAPER.items():
        if abs(a - x) <= 6 and abs(b - y) <= 6:
            return name
    return f"{round(a)}x{round(b)}mm"


def weird(text):
    n = 0
    for ch in text:
        o = ord(ch)
        if ch == "�" or 0xE000 <= o <= 0xF8FF or (unicodedata.category(ch) == "Cc" and ch not in "\r\n\t"):
            n += 1
    return n


def cjk(text):
    return sum(1 for ch in text if 0x3040 <= ord(ch) <= 0x30FF or 0x4E00 <= ord(ch) <= 0x9FFF)


def tables_by_page(path):
    try:
        import pymupdf
    except Exception:
        return {}
    out = {}
    with pymupdf.open(str(path)) as doc:
        for i, page in enumerate(doc, start=1):
            try:
                tabs = page.find_tables()
                area = page.rect.get_area() or 1
                out[i] = [round((pymupdf.Rect(t.bbox) & page.rect).get_area() / area, 3) for t in tabs.tables]
            except Exception as e:
                out[i] = None
    return out


def main():
    docs = []
    seen = {}
    outtext = open("/out/pagetext.jsonl", "w", encoding="utf-8")
    files = [("full56", p) for p in sorted(Path("/in/full56").glob("*.pdf"))] + [("prod6", p) for p in sorted(Path("/in/prod6").glob("*.pdf"))]
    for group, path in files:
        data = path.read_bytes()
        md5 = hashlib.md5(data).hexdigest()
        entry = {"group": group, "file": path.name, "bytes": len(data), "md5": md5}
        if md5 in seen:
            entry["same_as"] = seen[md5]
            docs.append(entry)
            print(group, path.name, "duplicate of", seen[md5], file=sys.stderr)
            continue
        seen[md5] = f"{group}/{path.name}"
        pdf = pdfium.PdfDocument(data)
        tabs = tables_by_page(path)
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            w, h = page.get_size()
            rot = page.get_rotation()
            tp = page.get_textpage()
            text = tp.get_text_range() or ""
            tp.close()
            nows = re.sub(r"\s+", "", text)
            area = (w * h) or 1
            imgs, n_text, n_path, n_form = [], 0, 0, 0
            for obj in page.get_objects(max_depth=15):
                t = obj.type
                if t == raw.FPDF_PAGEOBJ_IMAGE:
                    try:
                        l, b, r, tt = obj.get_bounds()
                        l, b, r, tt = max(l, 0), max(b, 0), min(r, w), min(tt, h)
                        frac = max(0.0, (r - l)) * max(0.0, (tt - b)) / area
                    except Exception:
                        frac = None
                    try:
                        px = list(obj.get_px_size())
                    except Exception:
                        px = None
                    imgs.append({"area": round(frac, 4) if frac is not None else None, "px": px})
                elif t == raw.FPDF_PAGEOBJ_TEXT:
                    n_text += 1
                elif t == raw.FPDF_PAGEOBJ_PATH:
                    n_path += 1
                elif t == raw.FPDF_PAGEOBJ_FORM:
                    n_form += 1
            wmm, hmm = w * PT2MM, h * PT2MM
            vw, vh = (hmm, wmm) if rot in (90, 270) else (wmm, hmm)
            kw = {k: text.count(k) for k in KEYWORDS if k in text}
            kx = {k: text.count(k) for k in EXTRA_KEYWORDS if k in text}
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            pg = {
                "page": i + 1, "w_mm": round(wmm, 1), "h_mm": round(hmm, 1), "rotation": rot,
                "paper": paper(wmm, hmm), "orientation": "landscape" if vw > vh else "portrait",
                "chars": len(nows), "cjk": cjk(nows), "weird_chars": weird(text),
                "text_objs": n_text, "path_objs": n_path, "form_objs": n_form,
                "image_objs": len(imgs),
                "image_area_max": max([x["area"] or 0 for x in imgs], default=0),
                "image_area_sum": round(min(1.0, sum(x["area"] or 0 for x in imgs)), 4),
                "images": imgs[:40],
                "tables_pymupdf": tabs.get(i + 1),
                "keywords": kw, "keywords_extra": kx,
                "head_lines": lines[:6],
            }
            pages.append(pg)
            outtext.write(json.dumps({"file": path.name, "page": i + 1, "text": text}, ensure_ascii=False) + "\n")
            page.close()
        meta = {}
        try:
            meta = {k: v for k, v in pdf.get_metadata_dict().items() if v}
        except Exception:
            pass
        entry.update({"pages": len(pdf), "pdf_version": pdf.get_version(), "metadata": meta, "page_info": pages})
        pdf.close()
        docs.append(entry)
        print(group, path.name, len(pages), "pages", file=sys.stderr)
    outtext.close()
    json.dump({"docs": docs}, open("/out/inventory_raw.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
