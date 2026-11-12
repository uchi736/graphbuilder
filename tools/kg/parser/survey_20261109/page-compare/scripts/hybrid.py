"""Nemotron Parse の領域 + pdfium の文字層 で組み立てた場合を試算する(置き換え後の経路の近似)。

各ページで:
  領域ごとに、Nemotron の文字の字数と、領域内の文字層の字数を比べる(cov = Nemotron / 文字層。小さいと黙った取りこぼし)
  どの領域にも中心が入らない文字層の文字(orphan)を行にまとめる
  組み立て: 本文系の領域は文字層の文字、表は Nemotron の HTML、図・グラフは除く(VLM に回す前提)、柱・ノンブルは除く、
            orphan の行は y 位置で差し込む
variant:
  full   ページ全体を Nemotron にかけた結果(single)
  crop   枠付きページだけ、本文セルを切り出して Nemotron にかけた結果(座標を本文セルからページへ戻す)
"""
import glob, json, re, sys
from pathlib import Path
import pypdfium2 as pdfium

pdf_dir, W = Path(sys.argv[1]), Path(sys.argv[2])
DOCS = ["VTR-T004C", "VTR-T005B", "HS-VTR-T002F"]
TEXT = {"Text", "Section-header", "Title", "List-item", "Footnote", "Caption", "Formula"}
FURN = {"Page-header", "Page-footer"}
bodycell = json.load(open(W / "bodycell.json", encoding="utf-8"))


def load_blocks(stem, n, variant):
    if variant == "crop":
        key = f"{stem} p{n}"
        if key not in bodycell:
            return None
        f = glob.glob(str(W / "nemotron_modes" / "bodycrop" / f"*{stem}_p{n:03d}_body.json"))[0]
        d = json.load(open(f, encoding="utf-8"))
        Wp, Hp = bodycell[key]["page_pt"]
        x0, y0, x1, y1 = bodycell[key]["body_mupdf"]
        out = []
        for b in d["blocks"]:
            bx0, by0, bx1, by1 = b["bbox"]
            out.append(dict(b, bbox=[(x0 + bx0 * (x1 - x0)) / Wp, (y0 + by0 * (y1 - y0)) / Hp,
                                      (x0 + bx1 * (x1 - x0)) / Wp, (y0 + by1 * (y1 - y0)) / Hp]))
        return out
    f = glob.glob(str(W / "nemotron" / stem / f"*_p{n:03d}.json"))[0]
    return json.load(open(f, encoding="utf-8"))["blocks"]


res = {}
for stem in DOCS:
    pdf = pdfium.PdfDocument(str(pdf_dir / f"{stem}.pdf"))
    for i in range(len(pdf)):
        n = i + 1
        page = pdf[i]
        Wp, Hp = page.get_size()
        tp = page.get_textpage()
        nchar = tp.count_chars()
        chars = []
        for k in range(nchar):
            c = tp.get_text_range(k, 1)
            if not c.strip():
                continue
            l, b, r, t = tp.get_charbox(k)
            chars.append((c, (l + r) / 2 / Wp, (Hp - (b + t) / 2) / Hp))
        for variant in ("full", "crop"):
            blocks = load_blocks(stem, n, variant)
            if blocks is None:
                continue
            regions, parts = [], []
            inside = [False] * len(chars)
            for bi, blk in enumerate(blocks):
                x0, y0, x1, y1 = blk["bbox"]
                tl = tp.get_text_bounded(left=x0 * Wp, bottom=Hp - y1 * Hp, right=x1 * Wp, top=Hp - y0 * Hp)
                for ci, (c, cx, cy) in enumerate(chars):
                    if x0 <= cx <= x1 and y0 <= cy <= y1:
                        inside[ci] = True
                ntext = blk.get("content") or blk.get("text") or ""
                regions.append({"class": blk["class"], "bbox": [round(v, 3) for v in blk["bbox"]],
                                "nem_len": len(re.sub(r"<[^>]+>|\s|\*\*", "", ntext)), "tl_len": len(re.sub(r"\s", "", tl)),
                                "nem": ntext[:300], "tl": tl[:300]})
                cls = blk["class"]
                if cls in FURN:
                    continue
                if cls in TEXT:
                    parts.append((y0, cls, tl.strip() or ntext.strip()))
                elif cls == "Table":
                    parts.append((y0, cls, ntext))
                # Picture / Chart は VLM に回す前提で、ここでは入れない
            # orphan: どの領域にも入らない文字。y でまとめて行にする
            orphan = [(cy, cx, c) for (c, cx, cy), ins in zip(chars, inside) if not ins]
            orphan.sort()
            lines, cur, last = [], [], None
            for cy, cx, c in orphan:
                if last is not None and cy - last > 0.006:
                    lines.append(cur); cur = []
                cur.append((cy, cx, c)); last = cy
            if cur:
                lines.append(cur)
            orphan_lines = [(min(x[0] for x in ln), "".join(x[2] for x in sorted(ln, key=lambda z: z[1]))) for ln in lines]
            doc_wo = "\n\n".join(p[2] for p in parts)
            parts2 = sorted(parts + [(y, "Orphan", t) for y, t in orphan_lines], key=lambda p: p[0])
            doc_with = "\n\n".join(p[2] for p in parts2)
            res[f"{stem} p{n} {variant}"] = {"n_chars": len(chars), "orphan_chars": len(orphan),
                                             "orphan_lines": [t for _, t in orphan_lines], "regions": regions,
                                             "doc_without_orphans": doc_wo, "doc_with_orphans": doc_with,
                                             "page_text": tp.get_text_bounded()}
        tp.close()
json.dump(res, open(W / "hybrid.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok", len(res))
