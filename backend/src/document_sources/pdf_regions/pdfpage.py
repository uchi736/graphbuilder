"""pypdfium2（BSD-3-Clause / Apache-2.0）でページを読む。pdfium に触るのはこのモジュールだけである。

pdfium はスレッド安全ではない（pypdfium2 も錠を持たない）。同じプロセスで 2 本の変換が別々のスレッドから pdfium を呼ぶと
プロセスごと落ちる（SIGTRAP）。そのため pdfium を呼ぶ処理は、プロセスに 1 つの PDFIUM_LOCK を取ってから行う。
ここの関数は自分でも PDFIUM_LOCK を取る（再入できる錠）。文書を開く・ページを取る・閉じる処理は呼び出し側
（converter.py）が同じ錠の中で行う。返すものは Python のデータ（文字層 TextLayer、線分の並び、PNG のバイト列）で、
錠の外・別スレッドで使ってよい。

座標はすべて「表示の座標」: CropBox の左上を原点にした pt で、ページの回転（/Rotate）を掛けた後の向き。page.render の
画像と同じ向き・同じ範囲になる。文字の矩形と罫線は PDF の座標（左下原点、MediaBox 基準、回転前）で読めるので、
_Geometry で表示の座標に直す。

extra（時計回りの度、0/90/180/270）を渡すと、/Rotate にさらに回転を足した向きで読む（文字層・罫線・画像とも）。
/Rotate は 0 のまま、字だけを倒して横向きの付表を置いたページ（HS-TP-023 の 5 ページ目）を、字が正立する向きで読むために使う
（converter._prepare。どの向きにするかは TextLayer.dominant_rotation が字の向き rot から決める）。
字の向き rot は字の行列（FPDFText_GetMatrix）の字の並ぶ向きを表示の座標に直したもの（時計回りの度。0 右・90 下・180 左・270 上）。
"""
from __future__ import annotations

import ctypes
import io
import logging
import math
import threading

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from .textlayer import Char, TextLayer

LONG_SIDE_PX = 1755          # ページ画像の長辺（Nemotron Parse の調査と同じ大きさ）
SEGMENT_MIN_PT = 3.0         # これより短い線分は罫線として数えない
THIN_FILL_PT = 3.0           # 塗りだけの図形は、幅か高さがこれ以下のときだけ線とみなす（灰色の帯などの面を線にしない）
AXIS_TOL_PT = 1.5            # 水平・垂直とみなす傾きの許容

PDFIUM_LOCK = threading.RLock()


def open_document(path):
    with PDFIUM_LOCK:
        return pdfium.PdfDocument(str(path))


class _Geometry:
    """PDF の座標（左下原点・回転前）と表示の座標（CropBox の左上原点・回転後）の変換"""

    def __init__(self, page, extra: int = 0):
        try:
            left, bottom, right, top = page.get_cropbox()
        except Exception:
            left, bottom, right, top = 0.0, 0.0, *page.get_size()
        self.left, self.bottom, self.right, self.top = float(left), float(bottom), float(right), float(top)
        self.w0, self.h0 = self.right - self.left, self.top - self.bottom
        try:
            self.rotation = int(page.get_rotation() or 0) % 360
        except Exception:
            self.rotation = 0
        self.rotation = (self.rotation + int(extra or 0)) % 360
        if self.rotation in (90, 270):
            self.width, self.height = self.h0, self.w0
        else:
            self.width, self.height = self.w0, self.h0

    def point(self, x: float, y: float) -> tuple:
        u, v = x - self.left, self.top - y
        r = self.rotation
        if r == 90:
            return self.h0 - v, u
        if r == 180:
            return self.w0 - u, self.h0 - v
        if r == 270:
            return v, self.w0 - u
        return u, v

    def rect(self, left: float, bottom: float, right: float, top: float) -> tuple:
        """PDF の矩形 → 表示の (x0, y0, x1, y1)"""
        xa, ya = self.point(left, bottom)
        xb, yb = self.point(right, top)
        return min(xa, xb), min(ya, yb), max(xa, xb), max(ya, yb)

    def to_pdf(self, X: float, Y: float) -> tuple:
        r = self.rotation
        if r == 90:
            u, v = Y, self.h0 - X
        elif r == 180:
            u, v = self.w0 - X, self.h0 - Y
        elif r == 270:
            u, v = self.w0 - Y, X
        else:
            u, v = X, Y
        return u + self.left, self.top - v


def page_size(page, extra: int = 0) -> tuple:
    """表示の大きさ（CropBox の幅・高さに回転を掛けたもの。page.render の画像と同じ向き）"""
    with PDFIUM_LOCK:
        g = _Geometry(page, extra)
        return g.width, g.height


# 時計回りの回転 → PIL の向きの変え方（PIL の ROTATE_90 は反時計回り）
_TRANSPOSE = {90: "ROTATE_270", 180: "ROTATE_180", 270: "ROTATE_90"}


def render_image(page, long_side: int = LONG_SIDE_PX, extra: int = 0) -> tuple:
    """ページを画像にして (PIL の画像, 縮尺 px/pt) を返す。extra は /Rotate に足す時計回りの回転（画像を回す）"""
    with PDFIUM_LOCK:
        w, h = page_size(page)
        scale = long_side / max(w, h)
        bitmap = page.render(scale=scale)
        try:
            image = bitmap.to_pil().convert("RGB")   # convert で写しを作り、pdfium の画像の領域を手放せるようにする
        finally:
            try:
                bitmap.close()
            except Exception:
                pass
    extra = int(extra or 0) % 360
    if extra:
        from PIL import Image
        method = getattr(Image, "Transpose", Image)
        image = image.transpose(getattr(method, _TRANSPOSE[extra]))
    return image, scale


def render_png(page, long_side: int = LONG_SIDE_PX, extra: int = 0) -> tuple:
    """ページを画像にして (PNG のバイト列, 縮尺 px/pt) を返す"""
    image, scale = render_image(page, long_side, extra)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue(), scale


def _char_rotation(tp, k: int, g: _Geometry) -> int:
    """字の並ぶ向き（字の行列の 1 列目）を表示の座標に直し、時計回りの度（0/90/180/270）にする。読めなければ 0"""
    m = pdfium_c.FS_MATRIX()
    try:
        if not pdfium_c.FPDFText_GetMatrix(tp.raw, k, m):
            return 0
    except Exception:
        return 0
    if abs(m.a) < 1e-9 and abs(m.b) < 1e-9:
        return 0
    xa, ya = g.point(0.0, 0.0)
    xb, yb = g.point(m.a, m.b)
    angle = math.degrees(math.atan2(yb - ya, xb - xa))
    return int(round(angle / 90.0)) % 4 * 90


def text_layer(page, extra: int = 0) -> TextLayer:
    """文字ごとの矩形を読み、表示の座標に直した文字層を返す。extra は /Rotate に足す時計回りの回転。

    文字層の並びの位置と Char.i は一致させる（矩形が読めない字も補った字として残す）。
    太字に見せるために同じ字を少しずらして重ね書きした字（同じ字が近い位置で 2 回読める）は、2 つめ以降を補った字として扱う。
    ただし直前の字と矩形がまったく同じ字は重ね書きとみなさない（合字 ﬀ → ff のように、1 つの字形が 2 字に当たる）。
    """
    with PDFIUM_LOCK:
        g = _Geometry(page, extra)
        tp = page.get_textpage()
        chars = []
        seen = {}   # (字, x の升, y の升) → [(中心 x, 中心 y, 通し番号, 矩形)]（重ね書きの検出）
        try:
            n = tp.count_chars()
            for k in range(n):
                c = (tp.get_text_range(k, 1) or "")[:1]
                gen = bool(pdfium_c.FPDFText_IsGenerated(tp.raw, k))
                try:
                    # 字送りと字の高さ（フォントの上端・下端）で決まる矩形。句読点も行の高さを持つので、行の判定と領域への割り当てに向く
                    box = tp.get_charbox(k, loose=True)
                    if max(box[3] - box[1], box[2] - box[0]) > 40:
                        # フォントの上端・下端の値が壊れている字は、字の形の矩形に戻す
                        box = tp.get_charbox(k, loose=False)
                except Exception:
                    chars.append(Char(k, c or " ", 0.0, 0.0, 0.0, 0.0, True, 1.0))
                    continue
                x0, y0, x1, y1 = g.rect(*box)
                if not c:
                    chars.append(Char(k, " ", x0, y0, x1, y1, True, 1.0))
                    continue
                # 字の高さ: 字の向きでの高さ（正立する字は表示の座標での縦の幅、横に倒れた字は横の幅）
                rot = _char_rotation(tp, k, g) if not gen and c.strip() else 0
                size = max((x1 - x0) if rot in (90, 270) else (y1 - y0), 1.0)
                if not gen and c.strip():
                    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                    tol_x, tol_y = 0.3 * max(min(x1 - x0, size), 1.0), 0.3 * size
                    gx, gy = int(cx // 2), int(cy // 2)
                    dup = False
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            for ox, oy, ok, obox in seen.get((c, gx + dx, gy + dy), ()):
                                if abs(ox - cx) <= tol_x and abs(oy - cy) <= tol_y:
                                    if ok == k - 1 and obox == box:
                                        continue   # 合字（同じ字形の 2 字めは同じ矩形で続く）
                                    dup = True
                    seen.setdefault((c, gx, gy), []).append((cx, cy, k, box))
                    if dup:
                        gen = True
                chars.append(Char(k, c, x0, y0, x1, y1, gen, size, rot))
        finally:
            tp.close()
        return TextLayer(g.width, g.height, chars)


def bounded_text(page, rect) -> str:
    """領域の中の文字（pdfium の get_text_bounded）。評価の参照に使う。rect は表示の座標"""
    with PDFIUM_LOCK:
        g = _Geometry(page)
        x0, y0, x1, y1 = rect
        xa, ya = g.to_pdf(x0, y0)
        xb, yb = g.to_pdf(x1, y1)
        tp = page.get_textpage()
        try:
            return tp.get_text_bounded(left=min(xa, xb), bottom=min(ya, yb), right=max(xa, xb), top=max(ya, yb))
        finally:
            tp.close()


def _mul(m, n):
    """行列の積（m を先に掛ける）。行列は PDF の (a, b, c, d, e, f)"""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def _matrix(obj_raw):
    m = pdfium_c.FS_MATRIX()
    if not pdfium_c.FPDFPageObj_GetMatrix(obj_raw, m):
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    return (m.a, m.b, m.c, m.d, m.e, m.f)


def _invisible(obj_raw, getter) -> bool:
    """白か透明の色（白い紙の上では見えない）"""
    r, g, b, a = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
    if not getter(obj_raw, r, g, b, a):
        return False
    return a.value == 0 or (r.value >= 250 and g.value >= 250 and b.value >= 250)


def _path_segments(obj_raw, matrix, g: _Geometry, hs, vs):
    fill = ctypes.c_int(0)
    stroke = ctypes.c_int(0)
    if pdfium_c.FPDFPath_GetDrawMode(obj_raw, fill, stroke):
        filled = bool(fill.value) and not _invisible(obj_raw, pdfium_c.FPDFPageObj_GetFillColor)
        stroked = bool(stroke.value) and not _invisible(obj_raw, pdfium_c.FPDFPageObj_GetStrokeColor)
        if not filled and not stroked:
            return  # 描かれない図形（切り抜きの範囲、白い下地、白い線のページの縁など）
        filled_only = filled and not stroked
    else:
        filled_only = False
    n = pdfium_c.FPDFPath_CountSegments(obj_raw)
    if n <= 0:
        return
    a, b, c, d, e, f = matrix
    polys = []
    x = ctypes.c_float()
    y = ctypes.c_float()
    for k in range(n):
        seg = pdfium_c.FPDFPath_GetPathSegment(obj_raw, k)
        if not seg or not pdfium_c.FPDFPathSegment_GetPoint(seg, x, y):
            continue
        kind = pdfium_c.FPDFPathSegment_GetType(seg)
        X, Y = a * x.value + c * y.value + e, b * x.value + d * y.value + f
        pt = g.point(X, Y)
        if kind == pdfium_c.FPDF_SEGMENT_MOVETO or not polys:
            polys.append([pt])
        else:
            polys[-1].append(pt)
        if pdfium_c.FPDFPathSegment_GetClose(seg) and polys[-1]:
            polys[-1].append(polys[-1][0])
    for poly in polys:
        if filled_only:
            # 塗りだけの図形は、細い矩形（線の代わりに塗った帯）だけを線とみなす。枠の線を 1 つのパスに細い矩形を
            # 何十も並べて描く様式があるので、パス全体ではなく部分の図形ごとに見る
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            if not xs or (max(xs) - min(xs) > THIN_FILL_PT and max(ys) - min(ys) > THIN_FILL_PT):
                continue
        for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
            if abs(y0 - y1) < AXIS_TOL_PT and abs(x1 - x0) >= SEGMENT_MIN_PT:
                hs.append((min(x0, x1), max(x0, x1), (y0 + y1) / 2))
            elif abs(x0 - x1) < AXIS_TOL_PT and abs(y1 - y0) >= SEGMENT_MIN_PT:
                vs.append((min(y0, y1), max(y0, y1), (x0 + x1) / 2))


def _image_bounds(obj_raw, parent, g: _Geometry):
    """画像の置かれた範囲（表示の座標）。フォームの中の画像は親の行列を掛ける"""
    left, bottom, right, top = ctypes.c_float(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
    if not pdfium_c.FPDFPageObj_GetBounds(obj_raw, left, bottom, right, top):
        return None
    corners = [(left.value, bottom.value), (right.value, bottom.value), (left.value, top.value), (right.value, top.value)]
    if parent is not None:
        a, b, c, d, e, f = parent
        corners = [(a * x + c * y + e, b * x + d * y + f) for x, y in corners]
    pts = [g.point(x, y) for x, y in corners]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


def _walk(obj_raw, parent, g: _Geometry, hs, vs, depth, survey=None):
    kind = pdfium_c.FPDFPageObj_GetType(obj_raw)
    matrix = _mul(_matrix(obj_raw), parent) if parent is not None else _matrix(obj_raw)
    if kind == pdfium_c.FPDF_PAGEOBJ_PATH:
        if survey is not None:
            survey["paths"] += 1
        else:
            _path_segments(obj_raw, matrix, g, hs, vs)
    elif kind == pdfium_c.FPDF_PAGEOBJ_IMAGE and survey is not None:
        box = _image_bounds(obj_raw, parent, g)
        if box:
            x0, y0, x1, y1 = max(0.0, box[0]), max(0.0, box[1]), min(g.width, box[2]), min(g.height, box[3])
            survey["image_area"] += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    elif kind == pdfium_c.FPDF_PAGEOBJ_FORM and depth < 8:
        for k in range(pdfium_c.FPDFFormObj_CountObjects(obj_raw)):
            child = pdfium_c.FPDFFormObj_GetObject(obj_raw, k)
            if child:
                _walk(child, matrix, g, hs, vs, depth + 1, survey)


def _walk_page(page, hs, vs, survey=None, extra: int = 0):
    g = _Geometry(page, extra)
    for k in range(pdfium_c.FPDFPage_CountObjects(page.raw)):
        obj = pdfium_c.FPDFPage_GetObject(page.raw, k)
        if obj:
            try:
                _walk(obj, None, g, hs, vs, 0, survey)
            except Exception as error:  # 壊れた図形 1 つでページ全体を諦めない
                logging.debug("pdf_regions: page object skipped (%s)", error)
    return g


def line_segments(page, extra: int = 0) -> tuple:
    """罫線の線分。水平線 (x0, x1, y) と垂直線 (y0, y1, x) の並びを返す（表示の座標）。extra は /Rotate に足す時計回りの回転。

    フォーム XObject の中の図形は、親の行列を掛けてページの座標に直す。
    """
    with PDFIUM_LOCK:
        hs, vs = [], []
        _walk_page(page, hs, vs, extra=extra)
        return hs, vs


def survey_page(page) -> dict:
    """文字層の有無を見るための軽い集計（pdf_markdown._survey と同じ考え方）。

    chars: 字として数える文字の数。image_share: 画像の置かれた面積の合計 / ページの面積。
    text_share: 文字の行の矩形の面積の合計 / ページの面積（画像が 5 割以上のページだけ数える。他は None）。
    paths: 図形の数（文字が 0 で図形だけのページは、輪郭にした文字とみなす）。
    """
    with PDFIUM_LOCK:
        survey = {"paths": 0, "image_area": 0.0}
        g = _walk_page(page, [], [], survey)
        area = max(g.width * g.height, 1.0)
        tp = page.get_textpage()
        try:
            # pdfium が補う字は空白と改行なので、空白以外の字を数える
            chars = sum(1 for c in (tp.get_text_range() or "") if not c.isspace())
        finally:
            tp.close()
        image_share = survey["image_area"] / area
        text_share = None
        if image_share >= 0.5 and chars:
            layer = text_layer(page)
            lines = layer.lines(layer.ink_indices())
            text_share = sum(max(0.0, ln.x1 - ln.x0) * max(0.0, ln.y1 - ln.y0) for ln in lines) / area
        return {"chars": chars, "image_share": image_share, "text_share": text_share, "paths": survey["paths"]}
