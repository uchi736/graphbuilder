"""枠付きページの本文セル: PyMuPDF の find_tables(今の枠の処理と同じ規則、評価用の参照)と、pypdfium2 の線のオブジェクトから求めた範囲を比べ、
参照の範囲でページ画像を切り出す。pdfium の文字層を本文セルの範囲で取り、今の枠の処理の本文と比べられるようにする。"""
import json, sys
from pathlib import Path
import pymupdf
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

src, img_root, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
targets = {"VTR-T004C": [1, 2], "VTR-T005B": [1, 2], "HS-VTR-T002F": [1, 2, 3]}
res = {}
for stem, pages in targets.items():
    mdoc = pymupdf.open(str(src / f"{stem}.pdf"))
    pdoc = pdfium.PdfDocument(str(src / f"{stem}.pdf"))
    for n in pages:
        mp = mdoc[n - 1]
        W, H = mp.rect.width, mp.rect.height
        frames = [t for t in mp.find_tables().tables if pymupdf.Rect(t.bbox).get_area() / (W * H) >= 0.7]
        cells = [pymupdf.Rect(c) for c in frames[0].cells if c]
        body = max(cells, key=lambda r: r.get_area())
        # pypdfium2: 線のオブジェクト(細い横線・縦線と、矩形)を集め、本文セルを「長い横線と縦線で囲まれた最大の矩形」として求める
        pp = pdoc[n - 1]
        hs, vs = [], []
        for obj in pp.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_PATH,), max_depth=5):
            l, b, r, t = obj.get_bounds()
            y0, y1 = H - t, H - b   # 上原点に直す
            if (r - l) > 0.25 * W and (y1 - y0) < 3:
                hs.append(((l, r), (y0 + y1) / 2))
            elif (y1 - y0) > 0.25 * H and (r - l) < 3:
                vs.append(((y0, y1), (l + r) / 2))
            elif (r - l) > 0.25 * W and (y1 - y0) > 0.25 * H:
                # 矩形のパス: 4 辺を線として扱う
                hs += [((l, r), y0), ((l, r), y1)]
                vs += [((y0, y1), l), ((y0, y1), r)]
        best = None
        for (hx, ya) in hs:
            for (hx2, yb) in hs:
                if yb <= ya + 0.3 * H:
                    continue
                for (vy, xa) in vs:
                    for (vy2, xb) in vs:
                        if xb <= xa + 0.3 * W:
                            continue
                        # 4 辺がそれぞれ線で覆われているか(端の余裕 4pt)
                        ok = (hx[0] <= xa + 4 and hx[1] >= xb - 4 and hx2[0] <= xa + 4 and hx2[1] >= xb - 4
                              and vy[0] <= ya + 4 and vy[1] >= yb - 4 and vy2[0] <= ya + 4 and vy2[1] >= yb - 4)
                        if not ok:
                            continue
                        # 内側を横切る長い横線があれば、それはセルではない
                        inside = any(ya + 4 < y < yb - 4 and h[0] <= xa + 4 and h[1] >= xb - 4 for h, y in hs)
                        if inside:
                            continue
                        area = (xb - xa) * (yb - ya)
                        if best is None or area > best[0]:
                            best = (area, (xa, ya, xb, yb))
        img = Image.open(img_root / stem / f"{stem}_p{n:03d}.png")
        sx, sy = img.size[0] / W, img.size[1] / H
        crop = img.crop((int(body.x0 * sx), int(body.y0 * sy), int(body.x1 * sx), int(body.y1 * sy)))
        crop.save(out / "body_crops" / f"{stem}_p{n:03d}_body.png")
        # pdfium の文字層を本文セルの範囲で(PDF 座標は下原点)
        tp = pp.get_textpage()
        body_text = tp.get_text_bounded(left=body.x0, bottom=H - body.y1, right=body.x1, top=H - body.y0)
        res[f"{stem} p{n}"] = {"page_pt": [W, H], "body_mupdf": [round(v, 1) for v in body],
                               "body_pdfium_lines": [round(v, 1) for v in best[1]] if best else None,
                               "n_hlines": len(hs), "n_vlines": len(vs), "crop_px": list(crop.size),
                               "pdfium_body_text": body_text}
        print(stem, n, "mupdf", [round(v) for v in body], "pdfium", [round(v) for v in best[1]] if best else None, len(hs), len(vs))
json.dump(res, open(out / "bodycell.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
