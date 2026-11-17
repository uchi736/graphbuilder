"""ページの文字層。文字ごとの矩形（左上原点の pt 座標）を持ち、領域ごとの文字・行・段落を組み立てる。

pdfium を触るのは pdfpage.py だけで、このモジュールのデータは Python のリストに写したものである。
そのため別スレッドで使ってよい（pdfium はスレッド安全ではない）。

文字の割り当て: 文字の中心が入る領域に 1 文字ずつ割り当てる（重なった領域では面積の小さい方）。
同じ文字が 2 つの領域に入って本文に 2 回出ることは無い。
行: 割り当てた文字を、文字の並び（PDF の書き順）で連続する部分ごとに切り、縦の位置が重なるものを 1 行にまとめ、
行の中は左から右、行は上から下に並べる。書き順が見た目の順と違う PDF でも、行の単位では見た目の順になる。
行の中のかけら（書き順で続く字）が横に入り組むとき（表の行で「Z-2 428 19」と「340」が別々に書かれ、340 が 428 より左にある）は、
かけらを大きな字間で切り直して左から並べる（入り組まない行はかけらのまま）。
字の向き: 字ごとに表示の向きでの字の並ぶ向き rot（0 右・90 下・180 左・270 上。pdfpage が字の行列から読む）を持つ。
向きの違う字（横向きの付表の上に正立した柱、グラフの縦の軸の名前）は、その字の向きの座標（reading_char）で行にまとめる。
段落: 行の右端が領域の右端近くまで達していれば折り返しとみなし、次の行をつなぐ（見た目の折り返しを改行にしない）。
次の行が番号・箇条の記号で始まるとき、文書番号（HS-VTR-T004 …）で始まり前の行が文の途中で終わっていないとき、
行の間が広いときはつながない。前の行が番号の見出しの形
（「2-5 外部振動計測 (…)」。その行だけで題になるもの。headings.heading_number_line）のときもつながず、見出しの判定に回す。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .headings import heading_number_line

Rect = tuple  # (x0, y0, x1, y1)、左上原点の pt


@dataclass(frozen=True)
class Char:
    i: int          # 文字層の中の通し番号（PDF の書き順）
    c: str
    x0: float
    y0: float
    x1: float
    y1: float
    gen: bool       # pdfium が補った空白・改行
    size: float     # 文字の高さの目安（pt。字の向きでの高さ）
    rot: int = 0    # 表示の向きでの字の並ぶ向き（時計回りの度。0 右・90 下・180 左・270 上）

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def ink(self) -> bool:
        """字として数える文字（補った文字・空白でない）"""
        return not self.gen and bool(self.c.strip())


@dataclass
class Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    indices: list
    rot: int = 0    # 行の字の向き（0 以外は、表示の座標の矩形が縦長になる）

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


# 行頭がこの形の行は、前の行の折り返しの続きとみなさない（番号・箇条の印）
_MARKER = re.compile(
    r"^\s*(?:\d{1,2}(?:[-.．)）]|\s)|[（(][0-9０-９a-zA-Z]{1,2}[)）]|[a-zA-Z][.)）]|[0-9０-９]{1,2}[)）]"
    r"|[・☆★※●○◆◇■□▲△▼▽►\-–—*•]|第\s*\d|図\s*[-‐]?\s*\d|表\s*[-‐]?\s*\d"
    r"|別\s*表|付\s*表|別\s*紙|添\s*付\s*資\s*料|[①-⑳]"
    # 来歴の 1 件ずつの記録（「A改正:96. 1.20…」「H06.10.12 一部見直し」「’04.03.26」）は前の記録の続きとみなさない
    r"|[0-9A-Z]\s*改\s*[正訂]|[HSR]\s*\d{1,2}\s*[./．]\s*\d|[’'`]\s*\d{2}\s*[./．])")
# 文書番号で始まる行（関連基準の「HS-VTR-T004(最新版) …」「QC-KAI-001 …」。HS で始まるか、ダッシュで 3 つに区切った番号。
# 全角の英数字・ダッシュも許す。「SL-1081B」のような品番は含めない）は、前の行が文の途中（ひらがな・読点・開き括弧・点で終わる
# 「…付いては」「[QC-KAI-005.」、括弧が閉じていない）でなければ、前の行の続きとみなさない（関連基準の 1 件ずつの行）
_D = r"[\-‐−－]"
_DOC_START = re.compile(r"^\s*(?:(?:HS|ＨＳ)[\s\-‐−－]?[A-ZＡ-Ｚ]{2,4}%s[A-ZＡ-Ｚ]?[0-9０-９]{2,4}"
                        r"|[A-ZＡ-Ｚ]{2,4}%s[A-ZＡ-Ｚ]{1,4}%s[A-ZＡ-Ｚ]?[0-9０-９]{2,4})" % (_D, _D, _D))
_CONTINUES = re.compile(r"[ぁ-ゖ、，,（(［\[「・･.．]\s*$")


_ASCII_ALNUM = re.compile(r"[0-9A-Za-z]")
PIECE_GAP = 2.0         # 字の並びをかけら（表のセル・横に並んだ別の欄・改正の印）に切る字間（字の大きさに対する倍率）


def split_by_gap(chars: Sequence["Char"], gap: float = PIECE_GAP, pair_size: bool = True) -> list:
    """字の並び（並べた順）を、字間が字の大きさの gap 倍を超える所で切った、字の並びの並びにする。
    字の大きさは、pair_size が真なら隣り合う 2 字の小さい方、偽なら左の字（1.0 pt を下限とする）"""
    if not chars:
        return []
    out, cur = [], [chars[0]]
    for a, b in zip(chars, chars[1:]):
        size = min(a.size, b.size) if pair_size else a.size
        if b.x0 - a.x1 > gap * max(size, 1.0):
            out.append(cur)
            cur = []
        cur.append(b)
    out.append(cur)
    return out


def _open_bracket(text: str) -> bool:
    """括弧が閉じていない行か（文書番号の並び「[QC-KAI-005.」の途中で折り返した行）"""
    return sum(text.count(c) for c in "(（[［「") > sum(text.count(c) for c in ")）]］」")


def inside(rect: Rect, x: float, y: float, pad: float = 0.0) -> bool:
    x0, y0, x1, y1 = rect
    return x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad


def area(rect: Rect) -> float:
    x0, y0, x1, y1 = rect
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def joiner(left: str, right: str) -> str:
    """2 つの文字列をつなぐときの間。英数字どうしは空白、和文は詰める"""
    if left and right and _ASCII_ALNUM.match(left[-1]) and _ASCII_ALNUM.match(right[0]):
        return " "
    return ""


class TextLayer:
    """1 ページの文字層。width・height はページの大きさ（pt）。"""

    def __init__(self, width: float, height: float, chars: Sequence[Char]):
        self.width = float(width)
        self.height = float(height)
        self.chars = list(chars)
        self._reading = None    # 字の向きの座標に直した字（向きの違う字があるときだけ作る）

    # ---- 字の向き ----
    def reading_char(self, k: int) -> Char:
        """字を、その字の向きで正立させた座標（字の並ぶ向きが右、行の進む向きが下）にした字。向きが 0 の字はそのまま"""
        ch = self.chars[k]
        if not ch.rot:
            return ch
        if self._reading is None:
            self._reading = {}
        out = self._reading.get(k)
        if out is None:
            W, H = self.width, self.height
            x0, y0, x1, y1 = ch.x0, ch.y0, ch.x1, ch.y1
            if ch.rot == 90:        # 下へ並ぶ字（行は左へ進む）
                box = (y0, W - x1, y1, W - x0)
            elif ch.rot == 180:
                box = (W - x1, H - y1, W - x0, H - y0)
            else:                   # 270: 上へ並ぶ字（行は右へ進む）
                box = (H - y1, x0, H - y0, x1)
            out = Char(ch.i, ch.c, *box, ch.gen, ch.size, ch.rot)
            self._reading[k] = out
        return out

    def dominant_rotation(self, min_share: float = 0.6, min_chars: int = 20) -> int:
        """字の多くが同じ向きに倒れているページ（横向きの付表を縦のページに置いたもの）を正立させる回転（時計回りの度、0/90/180/270）。
        字として数える字の min_share 以上、かつ min_chars 字以上が同じ 0 以外の向きなら、その向きを右に戻す回転を返す。違えば 0"""
        from collections import Counter
        count = Counter(ch.rot for ch in self.chars if ch.ink)
        total = sum(count.values())
        if not total:
            return 0
        rot, n = max(((r, c) for r, c in count.items() if r), key=lambda rc: rc[1], default=(0, 0))
        if rot and n >= min_chars and n >= min_share * total:
            return (360 - rot) % 360
        return 0

    # ---- 文字の選び出し ----
    def ink_indices(self, rect: Optional[Rect] = None, pad: float = 0.0) -> list:
        return [ch.i for ch in self.chars if ch.ink and (rect is None or inside(rect, ch.cx, ch.cy, pad))]

    def count(self, rect: Optional[Rect] = None, pad: float = 0.0) -> int:
        return len(self.ink_indices(rect, pad))

    def assign(self, rects: Sequence[Optional[Rect]], pool: Optional[Iterable[int]] = None, pad: float = 1.5):
        """文字を領域に 1 つずつ割り当てる。返り値は (領域ごとの通し番号の並び, どこにも入らない通し番号の並び)。

        pool を渡すとその文字だけを対象にする（本文の枠の中の文字など）。重なった領域では面積の小さい方に入れる。
        """
        order = sorted((i for i, r in enumerate(rects) if r is not None), key=lambda i: area(rects[i]))
        out = [[] for _ in rects]
        rest = []
        indices = self.ink_indices() if pool is None else sorted(pool)
        for k in indices:
            ch = self.chars[k]
            for i in order:
                if inside(rects[i], ch.cx, ch.cy, pad):
                    out[i].append(k)
                    break
            else:
                rest.append(k)
        return out, rest

    # ---- 行と段落 ----
    def segments(self, indices: Sequence[int]) -> list:
        """書き順で連続し、同じ高さにある文字の通し番号の並び（行のかけら）の並び。向きの違う字は別のかけらにし、
        高さはその字の向きの座標で比べる"""
        segs, cur = [], []
        prev = None
        for k in sorted(indices):
            ch = self.reading_char(k)
            if prev is not None:
                p = self.reading_char(prev)
                between = self.chars[prev + 1:k]
                same_line = (ch.rot == p.rot
                             and abs(ch.cy - p.cy) <= 0.5 * max(min(ch.size, p.size), 1.0)
                             and ch.x0 >= p.x0 - 0.5 * max(p.size, 1.0)
                             and not any(b.c in "\r\n" for b in between)
                             and all(not b.ink for b in between))
                if not same_line:
                    segs.append(cur)
                    cur = []
            cur.append(k)
            prev = k
        if cur:
            segs.append(cur)
        return segs

    def _segment_text(self, seg: Sequence[int]) -> str:
        out = []
        for a, b in zip(seg, list(seg[1:]) + [None]):
            out.append(self.chars[a].c)
            if b is not None and b > a + 1 and any(not c.c.strip() for c in self.chars[a + 1:b]):
                out.append(" ")
        return "".join(out)

    def _seg_info(self, seg: Sequence[int]) -> dict:
        cs = [self.reading_char(k) for k in seg]
        sizes = sorted(c.size for c in cs)
        return {"idx": list(seg), "x0": min(c.x0 for c in cs), "x1": max(c.x1 for c in cs),
                "y0": min(c.y0 for c in cs), "y1": max(c.y1 for c in cs),
                "size": sizes[len(sizes) // 2], "text": self._segment_text(seg)}

    def _split_wide(self, s: dict) -> list:
        """かけらを大きな字間（split_by_gap）で切り直す"""
        out = split_by_gap([self.reading_char(k) for k in s["idx"]])
        return [self._seg_info([c.i for c in part]) for part in out] if len(out) > 1 else [s]

    def lines(self, indices: Sequence[int]) -> list:
        """文字の通し番号の並びを、見た目の行（上から下、行の中は左から右）にする。

        向きの違う字は、字の向きごとに、その向きの座標で行にまとめる（行の文字列と通し番号の並びは字の向きの順。
        矩形は表示の座標）。行の中のかけらが横に入り組むときは、かけらを大きな字間で切り直してから左から並べる。
        """
        if not indices:
            return []
        by_rot = {}
        for seg in self.segments(indices):
            by_rot.setdefault(self.chars[seg[0]].rot, []).append(self._seg_info(seg))
        out = []
        for rot, segs in by_rot.items():
            out.extend(self._rows(segs, rot))
        out.sort(key=lambda ln: (ln.y0, ln.x0))
        return [ln for ln in out if ln.text]

    def _rows(self, segs: list, rot: int) -> list:
        segs.sort(key=lambda s: ((s["y0"] + s["y1"]) / 2, s["x0"]))
        rows = []
        for s in segs:
            placed = False
            for row in reversed(rows[-3:]):
                # 行の最初のかけらの縦の範囲と半分以上重なれば同じ行。行の範囲を広げながら比べると、2 行にまたがる背の高い字
                # （枠の「HS」など）を介して上下の行が 1 行につながる
                top, bottom = max(row["ry0"], s["y0"]), min(row["ry1"], s["y1"])
                if bottom > top and bottom - top >= 0.5 * min(row["ry1"] - row["ry0"], s["y1"] - s["y0"]):
                    row["segs"].append(s)
                    row["y0"], row["y1"] = min(row["y0"], s["y0"]), max(row["y1"], s["y1"])
                    placed = True
                    break
            if not placed:
                rows.append({"y0": s["y0"], "y1": s["y1"], "ry0": s["y0"], "ry1": s["y1"], "segs": [s]})
        out = []
        for row in rows:
            parts = sorted(row["segs"], key=lambda s: s["x0"])
            if any(b["x0"] < a["x1"] - 0.5 * max(min(a["size"], b["size"]), 1.0) for a, b in zip(parts, parts[1:])):
                # かけらが横に入り組む（書き順のかけらの範囲に別のかけらが入る）。大きな字間で切り直して左から並べる
                parts = sorted((p for s in parts for p in self._split_wide(s)), key=lambda s: s["x0"])
            text = parts[0]["text"]
            for a, b in zip(parts, parts[1:]):
                gap = b["x0"] - a["x1"]
                size = max(min(a["size"], b["size"]), 1.0)
                if gap > 0.5 * max(a["size"], 1.0):
                    sep = " "
                elif -0.3 * size <= gap <= 0.15 * size:
                    sep = ""      # 字間がほぼ 0 のかけら（上付き・下付きの字。m3/s の 3、ρV2 の 2）は詰める
                                  # （横に大きく重なるかけらは分数の分子と分母なので詰めない）
                else:
                    sep = joiner(text, b["text"])
                text += sep + b["text"]
            sizes = sorted(p["size"] for p in parts)
            indices = [k for p in parts for k in p["idx"]]
            if rot:
                cs = [self.chars[k] for k in indices]
                x0, y0, x1, y1 = min(c.x0 for c in cs), min(c.y0 for c in cs), max(c.x1 for c in cs), max(c.y1 for c in cs)
            else:
                x0, y0, x1, y1 = min(p["x0"] for p in parts), row["y0"], max(p["x1"] for p in parts), row["y1"]
            out.append(Line(text=re.sub(r"[ \t]+", " ", text).strip(), x0=x0, y0=y0, x1=x1, y1=y1,
                            size=sizes[len(sizes) // 2], indices=indices, rot=rot))
        return out

    def text(self, indices: Sequence[int], reflow: bool = True) -> str:
        """文字の通し番号の並びを本文にする。reflow が真なら見た目の折り返しをつなぐ"""
        return paragraph_text(self.lines(indices), reflow=reflow)


_LEADER = re.compile(r"[・･.．…‥]{3,}")
WRAP_MIN_CHARS = 16     # これより短い行は折り返しとみなさない（凡例・箇条の短い行をつながない）


def paragraph_text(lines: Sequence[Line], reflow: bool = True) -> str:
    """行を段落にする。行の右端の最大を領域の右端とする（領域の枠は文字より広いことがあるので、枠の右端は使わない）"""
    if not lines:
        return ""
    edge = max(ln.x1 for ln in lines)
    out = [lines[0].text]
    for prev, cur in zip(lines, lines[1:]):
        size = max(prev.size, 1.0)
        wrapped = (reflow and not prev.rot and not cur.rot and prev.x1 >= edge - 1.6 * size and len(prev.text.replace(" ", "")) >= WRAP_MIN_CHARS
                   and not _LEADER.search(prev.text) and not _MARKER.match(cur.text)
                   and cur.y0 - prev.y1 < 1.2 * size and cur.x0 <= prev.x0 + 2.5 * size
                   and not (_DOC_START.match(cur.text) and not _CONTINUES.search(prev.text) and not _open_bracket(prev.text))
                   and not heading_number_line(prev.text))   # 番号の見出しの行には次の行をつなげない（見出しの判定に回す）
        if wrapped:
            out[-1] += joiner(out[-1], cur.text) + cur.text
        else:
            out.append(cur.text)
    return "\n".join(out)
