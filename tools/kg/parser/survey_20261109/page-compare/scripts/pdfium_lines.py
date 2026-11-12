"""pypdfium2 の raw API でパスを線分単位に読み、長い横線・縦線から本文セル(最大の空きセル)を求める。PyMuPDF の結果と比べる。"""
import ctypes, json, sys
from pathlib import Path
import pypdfium2 as pdfium
import pypdfium2.raw as R

src = Path(sys.argv[1]); ref = json.load(open(sys.argv[2]))
def segments(page):
    H = page.get_size()[1]
    hs, vs = [], []
    for obj in page.get_objects(filter=(R.FPDF_PAGEOBJ_PATH,), max_depth=8):
        m = obj.get_matrix()   # パスの座標 → ページ座標
        a, b, c, d, e, f = m.get()
        n = R.FPDFPath_CountSegments(obj.raw)
        pts = []
        for k in range(n):
            seg = R.FPDFPath_GetPathSegment(obj.raw, k)
            x, y = ctypes.c_float(), ctypes.c_float()
            R.FPDFPathSegment_GetPoint(seg, x, y)
            t = R.FPDFPathSegment_GetType(seg)
            X, Y = a * x.value + c * y.value + e, b * x.value + d * y.value + f
            if t == R.FPDF_SEGMENT_MOVETO:
                pts.append([(X, Y)])
            else:
                if not pts: pts.append([])
                pts[-1].append((X, Y))
                if R.FPDFPathSegment_GetClose(seg) and pts[-1]:
                    pts[-1].append(pts[-1][0])
        for poly in pts:
            for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
                y0t, y1t = H - y0, H - y1
                if abs(y0t - y1t) < 1.5 and abs(x1 - x0) > 20:
                    hs.append((min(x0, x1), max(x0, x1), (y0t + y1t) / 2))
                elif abs(x0 - x1) < 1.5 and abs(y1t - y0t) > 20:
                    vs.append((min(y0t, y1t), max(y0t, y1t), (x0 + x1) / 2))
    return hs, vs

def covered(lines, a, b, pos, tol=3):
    """pos の位置で a〜b を線がつながって覆うか"""
    segs = sorted((s, e) for s, e, p in lines if abs(p - pos) <= tol)
    cur = a
    for s, e in segs:
        if s <= cur + tol: cur = max(cur, e)
    return cur >= b - tol

def body_cell(hs, vs, W, H):
    def merged(lines, limit):
        """同じ位置の線分をつないだ長さが limit を超える位置(短い線分に分けて引かれた枠の線のため)"""
        out = []
        for pos in sorted({round(p, 0) for _, _, p in lines}):
            segs = sorted((s, e) for s, e, p in lines if abs(p - pos) <= 1.5)
            best, cs, ce = 0, None, None
            for s, e in segs:
                if cs is None or s > ce + 3: cs, ce = s, e
                else: ce = max(ce, e)
                best = max(best, ce - cs)
            if best > limit and not (out and pos - out[-1] <= 1.5): out.append(pos)
        return out
    ys = merged(hs, 0.3 * W)
    xs = merged(vs, 0.3 * H)
    best = None
    for i, ya in enumerate(ys):
        for yb in ys[i + 1:]:
            if yb - ya < 0.3 * H: continue
            for j, xa in enumerate(xs):
                for xb in xs[j + 1:]:
                    if xb - xa < 0.3 * W: continue
                    if not (covered(hs, xa, xb, ya) and covered(hs, xa, xb, yb) and covered(vs, ya, yb, xa) and covered(vs, ya, yb, xb)):
                        continue
                    # 内側を横切る線が無いこと(セルである)
                    if any(ya + 3 < p < yb - 3 and covered(hs, xa, xb, p) for p in ys): continue
                    if any(xa + 3 < p < xb - 3 and covered(vs, ya, yb, p) for p in xs): continue
                    area = (xb - xa) * (yb - ya)
                    if not best or area > best[0]: best = (area, (xa, ya, xb, yb))
    return best[1] if best else None

for key, v in ref.items():
    stem, p = key.split(" p")
    doc = pdfium.PdfDocument(str(src / f"{stem}.pdf"))
    page = doc[int(p) - 1]
    W, H = page.get_size()
    hs, vs = segments(page)
    cell = body_cell(hs, vs, W, H)
    print(key, "segments h/v", len(hs), len(vs), "pdfium", [round(x) for x in cell] if cell else None, "mupdf", [round(x) for x in v["body_mupdf"]])

if len(sys.argv) > 3:
    doc = pdfium.PdfDocument(str(src / "HS-VTR-T002F.pdf")); page = doc[0]
    hs, vs = segments(page)
    near = sorted((round(s), round(e), round(p, 1)) for s, e, p in hs if 700 < p < 725)
    print("h near 713:", near)
    # 細い塗りの矩形として描かれた線も見る
    for obj in page.get_objects(filter=(R.FPDF_PAGEOBJ_PATH,), max_depth=8):
        l, b, r, t = obj.get_bounds(); H = page.get_size()[1]
        if 700 < H - t < 725 and (r - l) > 100: print("obj bounds", round(l), round(H - t), round(r), round(H - b))
