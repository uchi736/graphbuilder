"""文字層に戻した表の組み立て。

Nemotron Parse の表を捨てて文字層に戻した表（assemble._table の字数・読み誤り・行の欠け・行ごとの整合の規則）は、
文字層の見た目の行のままだと、行の中の並びが崩れ（書き順のかけらが入り組む）、値と列名の結び付きが分からなくなる
（材料試験成績表 TPS48BH19abura-3 の 3 表）。そこで、表の領域に罫線があれば欄を求めて文字を欄に割り当て、GFM の表の格子にする。

欄の求め方: pdfpage.line_segments の線分（frame.py と同じ読み方。短い線分に分けて引かれた線・細い矩形の塗りも線になる）のうち、
表の領域に掛かり、表の外枠とつながるもの（_frame_component。欄の中の図の線は外す）を使い、字ごとに、その字を囲む最も近い線で欄を決める（frame.Cells。結合した欄は、間に線が無いので 1 つの欄になる）。
欄の上下・左右の位置（近い位置はまとめる）を格子の行・列の境にし、欄の文字を、欄が掛かる格子の行ごとに、欄の左端の列に置く
（横に結合した欄が 1 行で、大きな字間のかけらが格子の別々の列に入るなら、かけらをその列に分ける。_spread）
（縦に結合した欄は行ごとに繰り返し、行の見出し「部位 ①」と値の結び付きを残す。横に結合した欄は左端の列だけに置き、残りは空にする。
様式の表は区画ごとに縦線の位置が違い、格子の列が 20 前後になるので、横にも繰り返すと表が数倍に膨らむ）。
罫線の 1 行に値が見た目の行で何段も並ぶときは、段ごとに表の行を分ける（_bands）。
どの行でも両方に中身のある所が無い隣り合う列は 1 つにまとめ、すべての列で同じ中身の隣り合う行は 1 つにまとめ、空の行・列は落とす。罫線の範囲の外の字（表の題・備考の行）は、範囲の上なら表の前、下なら表の後ろに行として置く。

罫線の表とみなすのは、領域に掛かる横線・縦線の位置がそれぞれ 2 つ以上あり、まとめた後の格子が 2 行 2 列以上で、2 字以上の
中身のある欄が MIN_FILLED 以上のときだけ（空の様式の台帳は表にしない）。
違えば None を返し、呼び出し側が行のかけらを「 | 」で区切った行にする。
"""
from __future__ import annotations

from typing import Optional

from .frame import Cells
from .textlayer import TextLayer, split_by_gap

TOL = 3.0           # 表の領域に掛かる線を選ぶときの余白（pt）
MERGE = 2.5         # 同じ境とみなす位置の差（pt。細い矩形で描いた線の両辺、二重線）
MIN_SEGMENT = 3.0   # 表の領域に掛かる長さがこれ以下の線は使わない（pt）
MAX_SEGMENTS = 1500 # 表の領域に掛かる線分がこれより多ければ格子にしない（方眼の用紙など。まとめる処理は線分の数の 2 乗）
MIN_FILLED = 0.25   # 2 字以上の中身のある欄がこの割合に満たない格子（空の様式）は表にしない


def _cluster(values, tol: float = MERGE) -> list:
    """近い値をまとめ、まとまりの平均の並び（小さい順）にする"""
    groups = []
    for v in sorted(values):
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]


def _frame_component(H: list, V: list) -> tuple:
    """線分を、触れ合うもの（横線と縦線が交わる・端が届く、同じ位置の線が続く）どうしでまとめ、長さの合計が最も大きいまとまり
    （表の外枠と罫線）だけを返す。欄の中の図（HS-RU-006J の「場所」の欄の羽根の図）の線は、罫線に触れなければ外れる"""
    segs = [("h", s) for s in H] + [("v", s) for s in V]
    parent = list(range(len(segs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def touch(a, b):
        (ka, (s1, e1, p1)), (kb, (s2, e2, p2)) = a, b
        if ka != kb:
            return s2 - TOL <= p1 <= e2 + TOL and s1 - TOL <= p2 <= e1 + TOL
        return abs(p1 - p2) <= MERGE and s1 <= e2 + TOL and s2 <= e1 + TOL

    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if touch(segs[i], segs[j]):
                a, b = find(i), find(j)
                if a != b:
                    parent[a] = b
    size = {}
    for i, (_k, (s0, e0, _p)) in enumerate(segs):
        size[find(i)] = size.get(find(i), 0.0) + (e0 - s0)
    if not size:
        return H, V
    best = max(size, key=size.get)
    keep = [find(i) == best for i in range(len(segs))]
    return ([s for (k, s), ok in zip(segs, keep) if ok and k == "h"], [s for (k, s), ok in zip(segs, keep) if ok and k == "v"])


def _nearest(centers: list, v: float) -> int:
    return min(range(len(centers)), key=lambda i: abs(centers[i] - v))


def ruled_grid(layer: TextLayer, indices, rect, hs, vs) -> Optional[tuple]:
    """罫線から表の格子を作る。返り値は (表の前の行の並び, 格子（行ごとのセルの文字列の並び）, 表の後ろの行の並び)。
    罫線の表でなければ None"""
    if not indices:
        return None
    x0, y0, x1, y1 = rect
    H = [(max(s, x0 - TOL), min(e, x1 + TOL), p) for s, e, p in hs
         if y0 - TOL <= p <= y1 + TOL and min(e, x1 + TOL) - max(s, x0 - TOL) > MIN_SEGMENT]
    V = [(max(s, y0 - TOL), min(e, y1 + TOL), p) for s, e, p in vs
         if x0 - TOL <= p <= x1 + TOL and min(e, y1 + TOL) - max(s, y0 - TOL) > MIN_SEGMENT]
    if len(H) + len(V) > MAX_SEGMENTS:
        return None     # 方眼の用紙など、線の多すぎる領域は表の格子にしない
    H, V = _frame_component(H, V)
    ys = _cluster([p for _, _, p in H])
    xs = _cluster([p for _, _, p in V])
    if len(ys) < 2 or len(xs) < 2:
        return None
    gx0, gx1, gy0, gy1 = min(xs), max(xs), min(ys), max(ys)
    cells = Cells(H, V, (gx0, gy0, gx1, gy1))
    groups, before, after = {}, [], []
    for k in indices:
        ch = layer.chars[k]
        if ch.cy < gy0 - 1.0:
            before.append(k)
        elif ch.cy > gy1 + 1.0:
            after.append(k)
        elif not (gx0 - 1.0 <= ch.cx <= gx1 + 1.0):
            (before if ch.cy < (gy0 + gy1) / 2 else after).append(k)
        else:
            groups.setdefault(cells.key(ch.cx, ch.cy), []).append(k)
    if not groups:
        return None
    rows_at = _cluster([v for key in groups for v in (key[1], key[3])])
    cols_at = _cluster([v for key in groups for v in (key[0], key[2])])
    nr, nc = len(rows_at) - 1, len(cols_at) - 1
    if nr < 1 or nc < 1:
        return None
    cells_at = []       # (行の始め, 行の終わり, 列の始め, 列の終わり, 見た目の行の並び, 欄の矩形)
    for key, ks in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        r0, r1 = _nearest(rows_at, key[1]), _nearest(rows_at, key[3])
        c0, c1 = _nearest(cols_at, key[0]), _nearest(cols_at, key[2])
        r1, c1 = min(max(r1, r0 + 1), nr), min(max(c1, c0 + 1), nc)
        if r0 >= nr or c0 >= nc:
            continue
        cells_at.append((r0, r1, c0, c1, layer.lines(sorted(ks)), key))
    bands = {r: _bands(r, cells_at) for r in range(nr)}
    start, total = [], 0         # 格子の行ごとの、出力の行の始めの位置（段に分けた行は段の数だけ）
    for r in range(nr):
        start.append(total)
        total += len(bands[r][0]) if bands[r] else 1
    grid = [[""] * nc for _ in range(total)]

    def put(row, col, text):
        old = grid[row][col]
        grid[row][col] = (old + " " + text).strip() if old and old != text else text

    for r0, r1, c0, c1, lines, key in cells_at:
        texts = _spread(layer, lines, cols_at, c0, c1) or {c0: " ".join(ln.text for ln in lines)}
        if r1 == r0 + 1 and bands[r0]:
            # 段に分けた行: 見た目の行をその段に置く。1 行だけの欄で、その行が欄の縦の中ほどにあるもの（行の見出しの「①」「④」）や
            # どの段にも入らないものは、欄全体に掛かる値なので、すべての段に繰り返す
            centers, tol = bands[r0]
            b = _band_of(lines[0], centers, tol) if len(lines) == 1 else None
            if len(lines) == 1 and (b is None or (b > 0 and abs(lines[0].cy - (key[1] + key[3]) / 2) <= 2 * tol)):
                for b in range(len(centers)):
                    for c, text in texts.items():
                        put(start[r0] + b, c, text)
                continue
            for ln in lines:
                b = _band_of(ln, centers, tol)
                b = 0 if b is None else b
                spread = _spread(layer, [ln], cols_at, c0, c1) or {c0: ln.text}
                for c, text in spread.items():
                    put(start[r0] + b, c, text)
            continue
        for r in range(r0, r1):
            for row in range(start[r], start[r] + (len(bands[r][0]) if bands[r] else 1)):
                for c, text in texts.items():
                    put(row, c, text)
    grid = _tidy(grid)
    if len(grid) < 2 or len(grid[0]) < 2:
        return None
    cells_all = [c for row in grid for c in row]
    if sum(1 for c in cells_all if len(c.replace(" ", "")) >= 2) < MIN_FILLED * len(cells_all):
        return None     # 中身のある欄の少ない格子（空の様式の台帳「A B / C D E」の繰り返し）は表にしない
    before_lines = [ln.text for ln in layer.lines(sorted(before))]
    after_lines = [ln.text for ln in layer.lines(sorted(after))]
    return before_lines, grid, after_lines


def _bands(r: int, cells_at: list) -> Optional[tuple]:
    """格子の行 r を、見た目の行の段に分けるか。分けるなら (段の中心の y の並び, 段とみなす許容) を返す。

    罫線の 1 行に、値が見た目の行で何段も並ぶ表（材料試験成績表の試験片 Z-1〜Z-8 と値、HS-RU-004E の部位①の欠陥ごとの値）は、
    段ごとに表の行を分けると、値どうしの結び付き（Z-1 の降伏点・引張強さ）が残る。分けるのは、その行だけに掛かる欄のうち、
    2 段以上の欄で段の数が最も多いものが 2 つ以上あり、段の中心が段の数だけにまとまり、どの欄の見た目の行も別々の段に入るときだけ
    （折り返した長い文の欄が 1 つだけの行は分けない）。さらに、2 行以上の欄が 1 行の欄より多いこと（HS-RU-002F の材質名
    「SACM / 645 / 相当材」と硬さ「HB / 241～ / 302」だけが 3 行で、降伏点などの値は 1 行の行は分けない）と、数字を含む段が
    2 つ以上ある欄があること（見出しの欄を折り返した「Gauge / Length / 標点距離 / mm」の行は分けない）"""
    own = [lines for r0, r1, _c0, _c1, lines, _key in cells_at if r0 == r and r1 == r + 1]
    multi = [lines for lines in own if len(lines) >= 2]
    if len(multi) < 2 or len(multi) <= len(own) - len(multi):
        return None     # 1 行の欄の方が多い行（材質名「SACM / 645 / 相当材」を折り返し、値は 1 行で縦の中ほど）は分けない
    most = max(len(lines) for lines in multi)
    if sum(1 for lines in multi if len(lines) == most) < 2:
        return None
    if not any(sum(1 for ln in lines if any(c.isdigit() for c in ln.text)) >= 2 for lines in multi):
        return None     # 数字を含む段が 2 つ以上ある欄が無い行（見出しの欄の「Gauge / Length / 標点距離 / mm」）は分けない
    sizes = sorted(ln.size for lines in multi for ln in lines)
    tol = 0.5 * max(sizes[len(sizes) // 2], 1.0)
    centers = _cluster([ln.cy for lines in multi for ln in lines], tol)
    if len(centers) != most:
        return None
    for lines in multi:
        got = [_band_of(ln, centers, tol) for ln in lines]
        if None in got or len(set(got)) != len(got):
            return None
    return centers, tol


def _band_of(ln, centers: list, tol: float) -> Optional[int]:
    b = _nearest(centers, ln.cy)
    return b if abs(centers[b] - ln.cy) <= tol else None


def _spread(layer: TextLayer, lines: list, cols_at: list, c0: int, c1: int) -> Optional[dict]:
    """横に結合した欄の文字が 1 行で、大きな字間（textlayer.split_by_gap）で切ったかけらが格子の 2 つ以上の列に分かれるなら、
    {列: その列のかけらの文字列} を返す（線の無い見出しの欄「許容欠陥大きさ(mm)   許容欠陥数」を下の列に分ける）。違えば None"""
    if c1 - c0 < 2 or len(lines) != 1:
        return None
    chars = sorted((layer.reading_char(k) for k in lines[0].indices), key=lambda c: c.x0)
    pieces = split_by_gap(chars)
    if len(pieces) < 2 or any(c.rot for c in chars):
        return None
    out = {}
    for piece in pieces:
        cx = (piece[0].x0 + piece[-1].x1) / 2
        col = next((c for c in range(c0, c1) if cols_at[c] <= cx < cols_at[c + 1]), None)
        if col is None:
            return None
        k = [ch.i for ch in piece]
        out[col] = (out.get(col, "") + " " + layer.text(k, reflow=False)).strip()
    return out if len(out) >= 2 else None


def _tidy(grid: list) -> list:
    """空の行・列を落とし、どの行でも両方に中身のある所が無い隣り合う列を 1 つにまとめ（区画ごとに縦線の位置が違う様式の表で、
    横に結合した欄の残りの空の列や、別の区画だけの列を詰める。どの行でも中身はぶつからないので、値と列名の上下の並びは変わらない）、
    すべての列で同じ中身の隣り合う行を 1 つにまとめる"""
    grid = [row for row in grid if any(c.strip() for c in row)]
    if not grid:
        return []
    cols = [[row[j] for row in grid] for j in range(len(grid[0])) if any(row[j].strip() for row in grid)]
    merged = []
    for col in cols:
        if merged and all(not a.strip() or not b.strip() for a, b in zip(merged[-1], col)):
            merged[-1] = [a if a.strip() else b for a, b in zip(merged[-1], col)]
        else:
            merged.append(col)
    grid = [list(row) for row in zip(*merged)]
    out = []
    for row in grid:
        if out and row == out[-1]:
            continue
        out.append(row)
    return out
