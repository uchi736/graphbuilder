"""枠付きの様式の検出（罫線の線分から）。

社内規格の様式は、ページ全体を囲む枠の中に、本文の大きな欄と、題名・番号・配布先・承認・来歴などの小さな欄がある。
ここでは pdfpage.line_segments が読んだ水平線・垂直線だけを使い、次の 3 つに分ける。

    body  本文の欄が 1 つに決まるページ。本文の欄 = 4 辺が線で覆われ、内側を端から端まで横切る線が無い、最大の矩形
          （幅・高さがページの 3 割以上、枠の面積の 4 割以上）
    grid  枠はあるが本文の欄が無いページ（協議・通知票、発行目録などの格子の様式）
    none  枠が無いページ（ページ全体を囲む矩形がページの 6 割未満）

短い線分に分けて引かれた枠の線は、同じ高さ（位置）の線分をつないで長さを測る。
欄（セル）は、点を囲む最も近い線で決める（cell_key）。本文以外の欄の文字は本文に入れず、上の帯の欄から題名の候補を取る。
試作 tools/kg/parser/survey_20261109/page-compare/scripts/pdfium_lines.py を下敷きにした（PyMuPDF の表の検出と 1pt 以内で一致）。
"""
from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .textlayer import TextLayer, area, split_by_gap

LONG_SHARE = 0.3            # 枠・本文の欄の辺とみなす線の長さ（ページの幅・高さに対する割合）
FRAMED_MIN_SHARE = 0.6      # 枠とみなす矩形の面積（ページに対する割合）
BODY_MIN_SHARE = 0.4        # 本文の欄の面積（枠に対する割合）
COVER_TOL = 3.0             # 辺が線で覆われているかを見るときの許容（pt）
TITLE_BAND = 0.12           # 題名の欄を探す上の帯（枠の高さに対する割合）
MAX_LONG_PAIRS = 1500       # 長い線の位置の数（縦×横）がこれを超えるページ（方眼の用紙など）は、欄の総当たりをしない

# 管理の欄の読み取り
_NUMBER = r"(?:HS-?)?([A-Z]{2,4})-([A-Z]?\d{2,4})([A-Z])?"    # 文書番号（TS-A104 の形）
CELL_NUMBER = re.compile(r"^%s$" % _NUMBER)
DATE = re.compile(r"('?\d{2,4}[./]\d{1,2}[./]\d{1,2}|[HSR]\d{1,2}[./]\d{1,2}[./]\d{1,2})")
LABEL_WORD = re.compile(r"改正|来歴|配布|承認|審査|作成|同意|合計|番号")
HISTORY_VERB = re.compile(r"(する|した|追加|変更|改訂|見直し|制定|移行|明記)[。.]?\s*(回|品|過|技|総|設)?")
# 格子のページの見出しに使う語。先に挙げた語を優先する
CONTROL_LABELS = ["発行目録", "基準来歴", "改正来歴", "改訂来歴", "来歴", "協議・通知票", "協議通知票", "通知票", "見直し確認欄",
                  "見直し確認", "配布先一覧"]
COMPANY = re.compile(r"株式会社|有限会社|Co\.?,?\s*Ltd|Corporation|Mass-Produced", re.I)
STAMP_LINE = re.compile(r"^\s*(改正番号\s*\d*|改\s*正)\s*$")


@dataclass
class Frame:
    kind: str                       # body | grid | none
    outer: Optional[tuple] = None   # 枠（左上原点の pt）
    body: Optional[tuple] = None    # 本文の欄
    hs: list = field(default_factory=list)
    vs: list = field(default_factory=list)
    virtual: bool = False           # 枠の線が無く、枠の語の位置から本文の範囲を決めた（virtual_frame）


class _Lines:
    """同じ位置の線分を引けるようにした索引"""

    def __init__(self, lines):
        self.by_pos = defaultdict(list)
        for s, e, p in lines:
            self.by_pos[round(p)].append((s, e, p))
        self.positions = sorted(self.by_pos)

    def near(self, pos, tol):
        out = []
        for key in range(int(round(pos - tol)) - 1, int(round(pos + tol)) + 2):
            out.extend(seg for seg in self.by_pos.get(key, ()) if abs(seg[2] - pos) <= tol)
        return out

    def covered(self, a, b, pos, tol=COVER_TOL) -> bool:
        """位置 pos で、a〜b を線がつながって覆うか"""
        cur = a
        for s, e, _ in sorted(self.near(pos, tol)):
            if s <= cur + tol:
                cur = max(cur, e)
            if cur >= b - tol:
                return True
        return cur >= b - tol

    def long_positions(self, limit):
        """つないだ長さが limit を超える位置"""
        out = []
        for key in self.positions:
            segs = sorted(self.near(key, 1.5))
            best, cs, ce = 0.0, None, None
            for s, e, _ in segs:
                if cs is None or s > ce + COVER_TOL:
                    cs, ce = s, e
                else:
                    ce = max(ce, e)
                best = max(best, ce - cs)
            if best > limit:
                pos = sum(p for _, _, p in segs) / len(segs)
                if not (out and pos - out[-1] <= 1.5):
                    out.append(pos)
        return out


def detect_frame(hs, vs, width: float, height: float) -> Frame:
    H, V = _Lines(hs), _Lines(vs)
    ys = H.long_positions(LONG_SHARE * width)
    xs = V.long_positions(LONG_SHARE * height)
    if len(ys) < 2 or len(xs) < 2:
        return Frame("none", hs=hs, vs=vs)
    outer, body = None, None
    # 欄の総当たりは線の本数の 4 乗で増える（100×100 本で約 1 分）。線の多すぎるページは外周だけで枠の有無を決め、本文の欄は探さない
    pairs = [] if len(ys) * len(xs) > MAX_LONG_PAIRS else ys
    for i, ya in enumerate(pairs):
        for yb in ys[i + 1:]:
            if yb - ya < LONG_SHARE * height:
                continue
            for j, xa in enumerate(xs):
                for xb in xs[j + 1:]:
                    if xb - xa < LONG_SHARE * width:
                        continue
                    if not (H.covered(xa, xb, ya) and H.covered(xa, xb, yb) and V.covered(ya, yb, xa) and V.covered(ya, yb, xb)):
                        continue
                    a = (xb - xa) * (yb - ya)
                    if outer is None or a > outer[0]:
                        outer = (a, (xa, ya, xb, yb))
                    # 内側を端から端まで横切る線が無いこと（欄である）
                    if any(ya + COVER_TOL < p < yb - COVER_TOL and H.covered(xa, xb, p) for p in ys):
                        continue
                    if any(xa + COVER_TOL < p < xb - COVER_TOL and V.covered(ya, yb, p) for p in xs):
                        continue
                    if body is None or a > body[0]:
                        body = (a, (xa, ya, xb, yb))
    page_area = width * height
    if outer is not None and outer[0] >= FRAMED_MIN_SHARE * page_area:
        outer_rect = outer[1]
    else:
        # 格子の様式は外周が二重線や段違いの線で描かれ、1 つの矩形として覆われないことがある（協議・通知票）。
        # 長い線が縦横に多く並び、その範囲がページの 6 割以上なら枠とみなす
        bbox = (min(xs), min(ys), max(xs), max(ys))
        if area(bbox) >= FRAMED_MIN_SHARE * page_area and len(ys) >= 4 and len(xs) >= 3:
            outer_rect = bbox
        else:
            return Frame("none", hs=hs, vs=vs)
    if body is not None and body[0] >= BODY_MIN_SHARE * area(outer_rect):
        return Frame("body", outer=outer_rect, body=body[1], hs=hs, vs=vs)
    return Frame("grid", outer=outer_rect, hs=hs, vs=vs)


class Cells:
    """点を囲む最も近い線で欄を決める"""

    def __init__(self, hs, vs, bounds):
        self.bounds = bounds
        self.v = sorted(vs, key=lambda s: s[2])
        self.vx = [s[2] for s in self.v]
        self.h = sorted(hs, key=lambda s: s[2])
        self.hy = [s[2] for s in self.h]

    def key(self, x, y):
        x0, y0, x1, y1 = self.bounds
        left = max((p for s, e, p in self.v[:bisect_right(self.vx, x)] if s - 1 <= y <= e + 1), default=x0)
        right = min((p for s, e, p in self.v[bisect_left(self.vx, x):] if s - 1 <= y <= e + 1), default=x1)
        top = max((p for s, e, p in self.h[:bisect_right(self.hy, y)] if s - 1 <= x <= e + 1), default=y0)
        bottom = min((p for s, e, p in self.h[bisect_left(self.hy, y):] if s - 1 <= x <= e + 1), default=y1)
        return (round(left), round(top), round(right), round(bottom))


def nfkc(text: str) -> str:
    """全角の英数字・記号・半角カナを揃える（文末の ．！？ と丸数字は残す）。管理の欄の判定にだけ使う"""
    return "".join(c if (c in "．！？" or "①" <= c <= "⑳") else unicodedata.normalize("NFKC", c) for c in (text or ""))


def flat_nfkc(text: str) -> str:
    """nfkc にして空白を詰めた文字列"""
    return re.sub(r"\s+", "", nfkc(text))


def cell_texts(layer: TextLayer, frame: Frame, indices) -> list:
    """文字を欄ごとに分け、(欄の矩形, 文字列) を上から下、左から右に並べて返す。欄の中の行は空白でつなぐ。

    見た目の行ごとに、縦の罫線（その高さを通るもの）か大きな字間で行を切ってかけらにし、かけらの中心で欄を決める。
    字ごとに欄を決めると、行の一部だけを囲む短い線（チェック欄の飾りなど）で「■ 新制 □ 改訂 □ 廃止」が別々の欄に割れる。
    """
    if frame.outer is None:
        return []
    cells = Cells(frame.hs, frame.vs, frame.outer)
    groups = defaultdict(list)
    for ln in layer.lines(indices):
        chars = sorted((layer.chars[k] for k in ln.indices), key=lambda c: c.x0)
        crossing = sorted(p for s, e, p in frame.vs if s - 1 <= ln.cy <= e + 1)
        pieces, cur = [], [chars[0]]
        for a, b in zip(chars, chars[1:]):
            line_between = any(a.cx < p < b.cx for p in crossing)
            if line_between or b.x0 - a.x1 > 3.0 * max(a.size, 1.0):
                pieces.append(cur)
                cur = []
            cur.append(b)
        pieces.append(cur)
        for piece in pieces:
            text = piece[0].c
            for a, b in zip(piece, piece[1:]):
                text += (" " if b.x0 - a.x1 > 0.5 * max(a.size, 1.0) else "") + b.c
            text = text.strip()
            if not text or STAMP_LINE.match(text):
                continue
            cx = (piece[0].x0 + piece[-1].x1) / 2
            groups[cells.key(cx, ln.cy)].append((ln.y0, piece[0].x0, text))
    out = []
    for key, items in groups.items():
        text = " ".join(t for _, _, t in sorted(items))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append((key, text))
    out.sort(key=lambda kv: (kv[0][1], kv[0][0]))
    return out


def frame_title(layer: TextLayer, frame: Frame) -> Optional[str]:
    """枠の上の帯の欄から題名を選ぶ（番号・日付・管理の語でない、6 字以上の最も長い欄）。無ければ他の欄から同じ条件で選ぶ"""
    if frame.kind != "body" or frame.body is None:
        return None
    if frame.virtual:
        return _virtual_title(layer, frame.body)
    if frame.outer is None:
        return None
    bx0, by0, bx1, by1 = frame.body
    pool = [ch.i for ch in layer.chars if ch.ink and not (bx0 <= ch.cx <= bx1 and by0 <= ch.cy <= by1)]
    cells = cell_texts(layer, frame, pool)
    ox0, oy0, ox1, oy1 = frame.outer
    limit = oy0 + TITLE_BAND * (oy1 - oy0)
    texts = [(key, t, flat_nfkc(t)) for key, t in cells if not COMPANY.search(nfkc(t))]
    texts = [(key, t, f) for key, t, f in texts if not _not_title(f)]
    head = [t for key, t, f in texts if key[1] <= limit and len(f) >= 6 and not CELL_NUMBER.match(f)
            and not DATE.search(f) and not LABEL_WORD.search(f) and f != "HS"]
    if head:
        return _strip_title_label(max(head, key=len))
    rest = [t for key, t, f in texts if len(f) >= 8 and not DATE.search(f) and not LABEL_WORD.search(f)
            and not HISTORY_VERB.search(f) and sum(c.isdigit() for c in f) < 0.3 * len(f)]
    return _strip_title_label(max(rest, key=len)) if rest else None


TITLE_LABEL = re.compile(r"^\s*(件\s*名|題\s*名|表\s*題|標\s*題|名\s*称)\s*[:：]?\s*")


def _strip_title_label(text: str) -> str:
    """「件名 気密不良による油もれ対策」のように項目の名前が付いた欄は、名前を外す"""
    rest = TITLE_LABEL.sub("", text)
    return rest if len(rest.strip()) >= 4 else text


# 題名でない欄: 項目の名前で始まる欄（型式・製番・図番などの記入欄）と、英数字が半分以上の欄（型式の値・番号）
NOT_TITLE_LABEL = re.compile(r"^(型\s*式|形\s*式|製\s*番|製造番号|図\s*番|品\s*番|品\s*名|部品番号|部品名|機\s*番|客\s*先|工\s*番|"
                             r"注文番号|作業番号|報告番号|文書番号|記号|数量|材質|日付|年月日|提出先|宛先|担当|部署|所属)")


def _not_title(flat: str) -> bool:
    if NOT_TITLE_LABEL.match(flat):
        return True
    alnum = sum(1 for c in flat if c.isascii() and c.isalnum())
    return bool(flat) and alnum >= 0.5 * len(flat)


GRID_TITLE_BAND = 0.2        # 格子のページの種類の語を探す上の帯（枠の上端から、枠の高さに対する割合。枠の上の題も含める）


def grid_label(layer: TextLayer, frame: Optional[Frame] = None) -> str:
    """格子のページの種類（協議・通知票・発行目録など）を、ページの題の位置の語で決める。無ければ ""。

    題 = 上の帯（枠の上端から枠の高さの 2 割まで。枠の上も含む）にある行のかけらで、種類の語で終わり、文（。で終わる）でないもの。
    候補が複数なら字の大きいかけら。小欄の名前（台帳の下の「見直し確認欄(3年毎)」）や本文の文（協議・通知票の理由欄の
    「3．基準来歴が存在しないため。」）では決めない。ページ全体の字から探すと、別紙の台帳が「見直し確認欄」の様式になり、
    枠の外の題名と柱（TS-A103C 別紙-1）が消えた（TS-A103D）。種類が無い格子のページは Nemotron Parse に送る
    """
    if frame is not None and frame.outer is not None:
        top, height = frame.outer[1], frame.outer[3] - frame.outer[1]
    else:
        top, height = 0.0, layer.height
    limit = top + GRID_TITLE_BAND * height
    best = None
    for rect, flat, _text in _pieces(layer):
        flat = flat.replace("･", "・")
        if rect[1] > limit or not flat or len(flat) > 40 or flat.endswith(("。", "．")):
            continue
        word = next((w for w in CONTROL_LABELS if flat.endswith(w)), None)
        if word and (best is None or rect[3] - rect[1] > best[0]):
            best = (rect[3] - rect[1], word)
    return best[1] if best else ""


def grid_rows(layer: TextLayer, frame: Frame) -> list:
    """格子のページを、行ごとに欄の文字を「 / 」でつないだ並びにする。

    改訂記号だけの行（A B C …）は落とす。
    """
    cells = cell_texts(layer, frame, layer.ink_indices(line_bounds(frame), pad=2.0))
    rows = []
    for key, text in cells:
        if rows and abs(key[1] - rows[-1][0]) <= 3:
            rows[-1][1].append((key[0], text))
        else:
            rows.append((key[1], [(key[0], text)]))
    out = []
    for _, row in rows:
        texts = [t for _, t in sorted(row)]
        if not texts or not any(len(t) > 1 for t in texts):
            continue
        line = texts[0]
        for t in texts[1:]:
            # 文書番号の欄に続く改訂記号の欄（TS-A105 | A | B）は空白でつなぐ。協議・通知票の「■ 改訂 … TS-A105 A B」を
            # 文書層の規則（parse_notice）が読む形。発行目録の「日付 / A / 日付」の並びは「 / 」のまま
            if REV_CELL.fullmatch(nfkc(t).strip()) and NUMBER_THEN_REVS.search(nfkc(line)):
                line += " " + t
            else:
                line += " / " + t
        out.append(line)
    return out


REV_CELL = re.compile(r"[A-Z0-9\-](?:\s+[A-Z0-9\-])*")
NUMBER_THEN_REVS = re.compile(r"[A-Z][A-Z0-9-]{2,}\d{3}(?:\s+[A-Z0-9\-])*$")


def line_bounds(frame: Frame) -> tuple:
    """すべての罫線の範囲（枠の上の、短い線で囲んだ日付・番号の欄も含める。枠の外の社名などは除く）"""
    xs = [x for s, e, _ in frame.hs for x in (s, e)] + [p for _, _, p in frame.vs]
    ys = [p for _, _, p in frame.hs] + [y for s, e, _ in frame.vs for y in (s, e)]
    if not xs or not ys:
        return frame.outer
    return (min(xs), min(ys), max(xs), max(ys))


# ---- 枠の線が画像で描かれた様式（線の代わりに枠の語の位置を使う）----
STRIP_LEFT, STRIP_TOP, STRIP_BOTTOM = 0.15, 0.10, 0.86
VIRTUAL_MIN_LABELS = 4
VIRTUAL_MAX_SEGMENTS = 8
BOTTOM_WORD = re.compile(r"改正回数|年月日|来歴|石川島|IHI|回転機械")
FRAME_LABEL = re.compile(r"^(配布先|控|合計|作成|審査|承認|来歴|番|号|HS|改正回数|年月日|原|計)$|^[回過技総][^\d]{1,5}\d?$|^[0-9]{1,2}$")
_TEXT_NUMBER = re.compile(r"\b%s\b" % _NUMBER)


def _pieces(layer: TextLayer) -> list:
    """文字層を、行のかけら（大きな字間で切ったもの）ごとの (矩形, 空白を詰めた文字列, 文字列) にする"""
    out = []
    for seg in layer.segments(layer.ink_indices()):
        for cs in split_by_gap([layer.chars[k] for k in seg], pair_size=False):
            text = nfkc("".join(c.c for c in cs))
            out.append(((min(c.x0 for c in cs), min(c.y0 for c in cs), max(c.x1 for c in cs), max(c.y1 for c in cs)),
                        re.sub(r"\s+", "", text), text))
    return out


def virtual_frame(layer: TextLayer) -> Optional[tuple]:
    """枠の線が無いページで、枠の語の位置から本文の範囲を決める。枠の語が足りなければ None。

    左の帯の枠の語（配布先・控・合計・承認…）の右端を本文の左端に、上の帯の枠の語（番・号・HS）と、それに縦に重なる
    かけら（題名）の下端を本文の上端に、下の帯の枠の語（来歴・改正回数・年月日・社名）と、それに縦に重なるかけら
    （来歴の日付と内容）の上端を本文の下端にする。
    """
    W, H = layer.width, layer.height
    blocks = _pieces(layer)
    left_labels = [r for r, flat, _ in blocks if r[2] <= STRIP_LEFT * W and len(flat) <= 8 and FRAME_LABEL.match(flat)]
    if not left_labels:
        return None
    left = max(r[2] for r in left_labels) + 2
    top_labels = [r for r, flat, _ in blocks if r[2] > left and r[3] <= STRIP_TOP * H and len(flat) <= 8 and FRAME_LABEL.match(flat)]
    bottom_labels = [r for r, flat, _ in blocks if r[2] > left and r[1] >= 0.80 * H
                     and ((len(flat) <= 20 and (FRAME_LABEL.match(flat) or BOTTOM_WORD.search(flat))) or len(DATE.findall(flat)) >= 2)]
    if len(left_labels) + len(top_labels) + len(bottom_labels) < VIRTUAL_MIN_LABELS:
        return None
    top = max((r[3] for r in top_labels), default=STRIP_TOP * H)
    for _ in range(5):
        grown = max([top] + [r[3] for r, _, _ in blocks if r[2] > left and r[1] < top and r[3] <= 0.2 * H])
        if grown == top:
            break
        top = grown
    bottom = min((r[1] for r in bottom_labels), default=STRIP_BOTTOM * H)
    for _ in range(5):
        grown = min([bottom] + [r[1] for r, _, _ in blocks if r[2] > left and r[3] > bottom and r[1] >= 0.76 * H])
        if grown == bottom:
            break
        bottom = grown
    top, bottom, right = top + 2, bottom - 2, W - 0.03 * W
    if bottom - top < 0.4 * H or right - left < 0.5 * W:
        return None
    return (left, top, right, bottom)


def _virtual_title(layer: TextLayer, body) -> Optional[str]:
    """本文より上のかけらから題名を選ぶ。縦に続き横に重なるかけら（2 行に折った題名）は 1 つにつなぐ"""
    cands = []
    for r, flat, text in _pieces(layer):
        if r[3] <= body[1] + 2:
            rest = re.sub(r"\s+", " ", re.sub(r"\bHS\b|番|号|\b\d{1,2}\b", "", _TEXT_NUMBER.sub("", text))).strip()
            if len(rest.replace(" ", "")) >= 4 and not LABEL_WORD.search(rest) and not COMPANY.search(rest):
                cands.append((r, rest))
    cands.sort(key=lambda c: (c[0][1], c[0][0]))
    groups = []
    for r, text in cands:
        g = groups[-1] if groups else None
        if g and r[1] - g["r"][3] < 1.5 * (r[3] - r[1]) and r[0] < g["r"][2] and r[2] > g["r"][0]:
            g["text"] += " " + text
            g["r"] = (min(g["r"][0], r[0]), g["r"][1], max(g["r"][2], r[2]), r[3])
        else:
            groups.append({"r": r, "text": text})
    titles = [g["text"] for g in groups if len(g["text"].replace(" ", "")) >= 6]
    return max(titles, key=len) if titles else None


def detect_page_frame(layer: TextLayer, hs, vs) -> Frame:
    """罫線から枠を求め、線が見つからないページは枠の語の位置から本文の範囲を求める"""
    frame = detect_frame(hs, vs, layer.width, layer.height)
    if frame.kind == "none" and len(hs) + len(vs) <= VIRTUAL_MAX_SEGMENTS:
        # 罫線がほとんど無いページだけ（枠の線が画像で描かれた様式）。罫線の多い様式の用紙は枠の無いページとして扱う
        body = virtual_frame(layer)
        if body is not None:
            return Frame("body", outer=(0.0, 0.0, layer.width, layer.height), body=body, hs=hs, vs=vs, virtual=True)
    return frame
