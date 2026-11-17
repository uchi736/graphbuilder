"""1 ページの組み立て: Nemotron Parse の領域（構造と読み順）と、文字層（本文の文字）を合わせて、行の並びにする。

    本文・見出し系の領域  領域の中の文字層。文字層の無い領域だけ Nemotron Parse の文字
    表                    Nemotron Parse の表の構造（行・列）で GFM のパイプ表に。セルの文字は文字層に寄せる（align_cells）。
                          表として信用できないもの（取り違え・取りこぼし・作った値・読み誤り・行の欠け・行ごとの不整合）は
                          文字層に戻すか図として扱う（規則の一覧は _table）
    図・グラフ            図の範囲の文字層を「図中の文字: …」として残す（VLM は呼ばない）。題は Caption の領域として別に出る。
                          図の範囲の中か直下にある番号の見出し（3-1.サージ誤差計算）の下の、文・式・定義の行は、図中の文字に
                          入れず見出しの後ろの本文にする（_figure_zones）
    柱・ノンブル          捨てる。ただし番号の見出しの形の 1 行（本文の欄の上端の「5-13 コンプレッサケーシング…」を柱と判定した
                          もの）は見出しの領域として読む。ページ上端の付属のラベル（別表・付表・別紙・添付資料＋番号）は、
                          同じページの題の行と 1 行にまとめて ## の見出しにする。題は見出し・本文・図の題の領域の短い行か、
                          それより上にある柱の短い行。ラベルと同じ柱に入った括弧書きの注記（(V-00862による)）は見出しの直後に残す
    どの領域にも入らない文字層の行   y の位置で差し込む（枠のページは本文の欄の中だけ。枠の無いページは上下の端の帯を除く）
    Nemotron Parse の文字を使うのは、領域の範囲に文字層の字が 1 つも無いときだけ（範囲の字が他の領域に入ったときは何も出さない。
    同じ本文を 2 回出さない）
    改正の印（「改正番号 6」）は、大きな字間で切った行のかけら単位で、割り当ての前に外す（本文の行の末尾につながらないように）

見出しの段は、このあと文書全体を通して headings.render_lines が番号の規則で決める。ここでは見出しの領域の行に印（hint）を付ける。
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from .frame import Frame, grid_rows
from .frame import COMPANY, STAMP_LINE, flat_nfkc, nfkc
from .headings import (DOC_NUMBER, DOTTED, PAGE_NUMBER, FigureText, Line, escape_body_line, header_annex, header_notes,
                       heading_number_line, normalize_text)
from .nemotron import place, plain_text, table_rows
from .tablegrid import ruled_grid
from .textlayer import TextLayer, paragraph_text, split_by_gap

from ..number_headings import ITEM, NUMBERED, SECTION, SUBITEM, _is_heading_title

TEXT_CLASSES = {"Text", "Section-header", "Title", "List-item", "Footnote", "Caption", "Formula", "Code"}
HEADING_CLASSES = {"Section-header", "Title"}
FURNITURE_CLASSES = {"Page-header", "Page-footer"}
FIGURE_CLASSES = {"Picture", "Chart"}
MARGIN_BAND = 0.07          # 枠の無いページで、どの領域にも入らない文字を捨てる上下の帯（ページの高さに対する割合）
HEADER_BAND = 0.15          # 付属のラベルを探すページ上端の帯
TL_MIN_CHARS = 10           # 表の字数を比べるのに要る文字層の字数
TABLE_MISREAD_SHARE = 0.2   # 文字層に寄せた後も、表の字のうち文字層に無い字がこの割合を超えれば、表を文字層の行に戻す
REGION_PAD = 1.5            # 領域に文字を割り当てるときの余白（TextLayer.assign と同じ）
BIG_TABLE_AREA = 0.6        # 送った画像に対する面積がこれを超える表は、枠などの取り違えとみなし文字層の段落として読む
TABLE_MIN_COVERAGE = 0.7    # Nemotron Parse の表の字数が領域の文字層の字数のこの割合に満たなければ、取りこぼしとみなし文字層に戻す
NUMBERED_CAPTION = re.compile(r"^\s*\d{1,2}(?:[-.．]\d{1,2})+\s*[.．]?\s*\D|^\s*\d{1,2}[.．]\s*[^\d\s]")
MISSING_MIN_CHARS = 4       # 表に無いとみなすかけらの、セルと突き合わない字の最小の数（縦書きの 1 字・記号・1 字の読み違いでは数えない）
MISSING_SHARE = 0.5         # かけらの字のうち、セルと突き合わない字がこの割合以上なら、表に無いかけらとみなす
SPACED_GAP = 2.0            # 字間の空いた 1 字の語（「部 品 名」）を 1 語につなぐ字間の上限（字の大きさに対する倍率）
SPILL_ROWS = 2              # 行ごとの整合で、セルに無い語を上の表の行の結合した欄のはみ出しとみなす行の数


@dataclass
class PageInput:
    number: int
    layer: TextLayer
    frame: Frame
    png: Optional[bytes] = None      # Nemotron Parse に送った画像（枠のページは本文の欄の切り出し）
    view: Optional[tuple] = None     # 送った画像の範囲（ページの pt）
    title: Optional[str] = None      # 枠の題名の欄
    label: str = ""                  # 格子のページの種類（協議・通知票など）
    needs_parse: bool = True
    note: str = ""
    extra: int = 0                   # 字が正立する向きに回して読んだときの回転（時計回りの度。converter._prepare）
    annex_hint: Optional[str] = None # 回したページの、回す前の向きの柱にあった付属のラベル（「付表」）
    annex_notes: list = field(default_factory=list)   # その柱の括弧書きの注記
    drop: set = field(default_factory=set)            # 本文に入れない字（回す前の向きの柱の字）


@dataclass
class PageOutput:
    number: int
    lines: list
    framed: bool
    stats: dict = field(default_factory=dict)


# HTML の印（<sup>・</td> など。名前が英字のものだけ。「<初品>」のような本文の山括弧は残す）
HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")


def _chars(text: str) -> str:
    return re.sub(r"\s+", "", normalize_text(HTML_TAG.sub("", (text or "").replace("**", ""))))


def _bag_precision(nem: str, tl: str) -> float:
    """Nemotron Parse の字のうち、文字層にもある字の割合（字の多重集合で数える）"""
    a, b = Counter(nem), Counter(tl)
    if not a:
        return 1.0
    return sum(min(n, b[c]) for c, n in a.items()) / sum(a.values())


def to_gfm(grid) -> list:
    """セルの格子を GFM のパイプ表の行にする。セルの中の改行は空白、| は ／"""
    rows = [[re.sub(r"\s+", " ", normalize_text(c or "")).strip().replace("|", "／") for c in row] for row in grid]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return out


# 点線（リーダー）だけの行
_DOTS_LINE = re.compile(r"^[\s・･.．…‥\-‐－—―_]+$")


def _noise_line(text: str) -> bool:
    """本文に入れない行: 点線だけの行と、「改正番号 2」などの改正の印（frame.STAMP_LINE）"""
    return bool(_DOTS_LINE.match(text) or STAMP_LINE.match(text))


def _text_lines(text: str, hint: bool = False) -> list:
    return [Line(t, hint=hint) for t in normalize_text(text).split("\n") if t.strip() and not _noise_line(t)]


def _final(text: str, annex: bool = False, heading: bool = False, root: bool = False) -> Line:
    text = normalize_text(text)
    return Line(text if heading else escape_body_line(text), kind="final", annex=annex, root=root)


def _is_heading_number_line(text: str) -> bool:
    t = normalize_text(text).strip()
    return bool(SECTION.match(t) or DOTTED.match(t))


def grid_page(page: PageInput) -> PageOutput:
    """格子の様式のページ（協議・通知票・発行目録）。種類の見出しを立て、行ごとに欄を「 / 」でつなぐ"""
    lines = [_final(f"## {page.label}", heading=True, root=True), Line("", kind="blank")]
    lines += [_final(r) for r in grid_rows(page.layer, page.frame)]
    return PageOutput(page.number, lines, framed=True, stats={"kind": "grid", "label": page.label})


def _pool(page: PageInput) -> list:
    layer = page.layer
    if page.frame.kind == "body" and page.frame.body:
        indices = layer.ink_indices(page.frame.body, pad=0.5)
    else:
        indices = [ch.i for ch in layer.chars if ch.ink]
    if page.drop:
        indices = [k for k in indices if k not in page.drop]
    return _drop_stamps(layer, indices)


def _drop_stamps(layer: TextLayer, indices) -> list:
    """改正の印（「改正番号 6」「改 正」）の字を外す。書き順で続く同じ高さの字を、大きな字間で切ったかけらごとに見る
    （印は本文の欄の右端に置かれ、見た目の行では本文の行の末尾に並ぶ）"""
    drop = set()
    for seg in layer.segments(indices):
        for group in split_by_gap([layer.reading_char(k) for k in seg], pair_size=False):
            g = [c.i for c in group]
            flat = flat_nfkc("".join(layer.chars[k].c for k in g))
            if flat and STAMP_LINE.match(flat):
                drop.update(g)
    return [k for k in indices if k not in drop] if drop else list(indices)


def _layer_count(layer: TextLayer, rect) -> int:
    """領域の範囲にある文字層の字の数（割り当ての前の数。0 なら文字層の無い領域）"""
    return layer.count(rect, pad=REGION_PAD)


def _drop_margins(page: PageInput, indices) -> list:
    """枠の無いページで、上下の端の帯にある文字（柱・ノンブル・社名）を外す"""
    if page.frame.kind == "body":
        return list(indices)
    H = page.layer.height
    return [k for k in indices if MARGIN_BAND * H <= page.layer.chars[k].cy <= (1 - MARGIN_BAND) * H]


def _paragraphs(layer: TextLayer, indices) -> list:
    """文字層の行を、行の間の広さと行頭の番号で段落に分ける（領域の無いときの組み立て）"""
    lines = layer.lines(indices)
    groups, cur = [], []
    for ln in lines:
        if cur and (ln.y0 - cur[-1].y1 > 0.9 * max(cur[-1].size, 1.0)):
            groups.append(cur)
            cur = []
        cur.append(ln)
    if cur:
        groups.append(cur)
    return [paragraph_text(g, reflow=True) for g in groups]


def fallback_page(page: PageInput, reason: str = "") -> PageOutput:
    """Nemotron Parse の結果が無いページ。本文の欄（無ければページ）の文字層の行だけで組み立てる"""
    if page.frame.kind == "grid" and page.label:
        return grid_page(page)
    idx = _drop_margins(page, _pool(page))
    lines = []
    if page.annex_hint:
        lines += [_final(f"## {page.annex_hint}", annex=True, heading=True)] + [l for n in page.annex_notes for l in _text_lines(n)]
        lines.append(Line("", kind="blank"))
    for para in _paragraphs(page.layer, idx):
        if PAGE_NUMBER.match(para.strip()):
            continue
        lines += _text_lines(para)
        lines.append(Line("", kind="blank"))
    return PageOutput(page.number, lines, framed=page.frame.kind == "body",
                      stats={"kind": page.frame.kind, "fallback": True, "reason": reason})


def assemble_page(page: PageInput, blocks, own_keys=frozenset()) -> PageOutput:
    if page.frame.kind == "grid" and page.label:
        return grid_page(page)
    if blocks is None:
        return fallback_page(page, "no regions")
    layer = page.layer
    framed = page.frame.kind == "body"
    W, H = layer.width, layer.height
    vx0, vy0, vx1, vy1 = page.view or (0.0, 0.0, W, H)
    vw, vh = vx1 - vx0, vy1 - vy0
    stats = {"kind": page.frame.kind, "regions": len(blocks), "tables": 0, "tables_gfm": 0, "tables_to_text": 0,
             "tables_grid": 0, "tables_pieces": 0,
             "figures": 0, "orphan_chars": 0, "fallback": False}

    def to_page(b):
        return (vx0 + b[0] * vw, vy0 + b[1] * vh, vx0 + b[2] * vw, vy0 + b[3] * vh)

    rects = [to_page(b.bbox) for b in blocks]
    pool = _pool(page)
    assigned, orphans = layer.assign(rects, pool)
    _settle_line_pieces(layer, blocks, pool, assigned, orphans)
    top_limit = (page.frame.body[1] + 0.12 * (page.frame.body[3] - page.frame.body[1])) if framed else HEADER_BAND * H

    zones = _figure_zones(page, blocks, rects, assigned, orphans)

    parts = []          # {"y": 上端, "lines": [...], "cls": 区分, "i": 領域の番号}
    annex = page.annex_hint     # 回したページは、回す前の向きの柱から取った付属のラベル（converter._rotated_header）
    annex_notes = list(page.annex_notes)    # 付属のラベルの柱に入った括弧書きの注記（「(V-00862による)」）。本文に残す
    header_titles = []  # 上端の柱のうち、付属のラベルでも文書番号でもない短い行（付属のページの題の候補）
    for i, (b, rect) in enumerate(zip(blocks, rects)):
        cls = b.cls
        idx = assigned[i]
        if cls in FURNITURE_CLASSES:
            text = layer.text(idx, reflow=True) if idx else plain_text(b.text)
            if rect[1] <= top_limit:
                label = header_annex(text, own_keys) if annex is None else None
                if label:
                    annex = label
                    annex_notes = header_notes(text)
                    continue
            if idx and _furniture_heading(text):
                # 番号の見出しの形の柱（図のページの本文の欄の上端の「5-13 コンプレッサケーシングアウターパート」）は柱ではない。
                # 見出しの領域として読む（柱として捨てると節が欠ける。HS-TP-002C の 10・13 ページ目）
                cls = "Section-header"
            else:
                if rect[1] <= top_limit and _header_title(text, own_keys):
                    header_titles.append((rect[1], " ".join(normalize_text(text).split())))
                continue
        if cls == "Table":
            stats["tables"] += 1
            part = _table(page, b, rect, idx, stats)
            if part == "figure":
                part = _figure(page, idx, stats)
            if part:
                parts.append({"y": rect[1], "lines": part, "cls": cls, "i": i})
            continue
        if cls in FIGURE_CLASSES:
            zone = zones.get(i)
            part = _figure(page, idx, stats, zone if zone is not None and zone.internal else None)
            if part:
                parts.append({"y": rect[1], "lines": part, "cls": cls, "i": i})
            if zone is not None and not zone.internal:
                # 図の外（どの領域にも入らない文字・直下の題の領域）から取った見出しは、図の後ろに見出しと本文を置く
                parts.append({"y": zone.y, "lines": [Line(normalize_text(zone.head), hint=zone.hint)] + _text_lines("\n".join(zone.body)),
                              "cls": "Zone", "i": -1})
            continue
        # 本文・見出し系（未知の区分も本文として扱う）。Nemotron Parse の文字は、範囲に文字層の字が無い領域だけ
        if idx:
            text = layer.text(idx, reflow=True)
        elif _layer_count(layer, rect) == 0:
            text = plain_text(b.text)
        else:
            continue   # 範囲の字は他の領域に入って出力済み（または改正の印として外した）
        if not text.strip():
            continue
        if not framed and cls == "Text" and PAGE_NUMBER.match(text.strip()) and (rect[1] < 0.1 * H or rect[3] > 0.9 * H):
            continue  # ノンブル（「2/2」など）
        if cls in HEADING_CLASSES:
            # 見出しの領域は 1 行の題（折り返した題をつなぐ）
            lines = _text_lines(" ".join(t.strip() for t in text.split("\n")), hint=True)
        else:
            lines = _text_lines(text)
            if cls == "Caption" and lines and NUMBERED_CAPTION.match(lines[0].text):
                lines[0].hint = True   # 番号で始まる題（3-1.サージ誤差計算）は見出しに戻す
        parts.append({"y": rect[1], "lines": lines, "cls": cls, "i": i})

    if annex:
        # 付属のラベル + 同じページの題の行（上端の近くにある、番号で始まらない最初の見出しの領域。題が柱と判定された
        # ページ（HS-VTR-T002F の添付資料の「ABB方式 コンプレッサ性能」）は、それより上にある柱の短い行）
        title, title_part, title_y = None, None, None
        for p in parts:
            t = " ".join(ln.text for ln in p["lines"]).strip()
            if p["y"] > vy0 + 0.35 * vh:
                break
            short = len(t) <= 30 and not re.search(r"[。．]$", t) and len(p["lines"]) == 1
            if p["cls"] in HEADING_CLASSES or (p["cls"] in ("Text", "Caption") and short):
                # 見出しの領域か、上端近くの短い 1 行（題が本文・図の題と判定されたもの。別紙の台帳の題は Caption になる）
                if (t and not re.match(r"^\s*[\d(（]|^[a-z][.)]", t) and not header_annex(t)
                        and not re.match(r"^\s*[図表]\s*[-‐]?\s*\d", t)):
                    title, title_part, title_y = t, p, p["y"]
                break
            if len(t) >= 30:
                break   # 本文が始まった
        if header_titles and (title_y is None or header_titles[0][0] < title_y):
            title, title_part = header_titles[0][1], None
        if title_part is not None:
            parts.remove(title_part)
        head = f"## {annex} {title}".rstrip() if title else f"## {annex}"
        notes = [{"y": -0.5, "lines": _text_lines(n), "cls": "AnnexNote", "i": -1} for n in annex_notes]
        parts[:0] = [{"y": -1.0, "lines": [_final(head, annex=True, heading=True)], "cls": "Annex", "i": -1}] + notes

    # どの領域にも入らない文字層の行を y の位置で差し込む
    orphans = _drop_margins(page, orphans)
    stats["orphan_chars"] = len(orphans)
    if orphans:
        groups = {}
        for ln in layer.lines(orphans):
            pos = next((k for k, p in enumerate(parts) if p["y"] > ln.y0 + 0.5), len(parts))
            groups.setdefault(pos, []).append(ln)
        for pos in sorted(groups, reverse=True):
            text = paragraph_text(groups[pos], reflow=True)
            if PAGE_NUMBER.match(text.strip()):
                continue
            parts.insert(pos, {"y": groups[pos][0].y0, "lines": _text_lines(text), "cls": "Orphan", "i": -1})

    lines = []
    for p in parts:
        if not p["lines"]:
            continue
        lines.extend(p["lines"])
        lines.append(Line("", kind="blank"))
    return PageOutput(page.number, lines, framed, stats)


def _furniture_heading(text: str) -> bool:
    """柱と判定された領域が、番号の見出しの形の 1 行（章・節・点区切りの番号で始まり、その行だけで題になる）か"""
    lines = [t for t in normalize_text(text or "").split("\n") if t.strip()]
    return len(lines) == 1 and heading_number_line(lines[0])


def _header_title(text: str, own_keys=frozenset()) -> bool:
    """上端の柱が、付属のページの題になれる短い行か（文書番号・ページ番号・社名・付属のラベル・番号で始まる行・文は除く）"""
    t = " ".join(normalize_text(text or "").split())
    flat = t.replace(" ", "")
    if not 4 <= len(flat) <= 30 or not re.search(r"[^\W\d_A-Za-z]", flat):
        return False
    if DOC_NUMBER.search(t.upper()) or PAGE_NUMBER.match(t) or COMPANY.search(t) or header_annex(t, own_keys) is not None:
        return False
    return not re.match(r"^\s*[\d(（]", t) and not re.search(r"[。．]$", t)


def _settle_line_pieces(layer, blocks, pool, assigned, orphans):
    """領域の端に掛かった行のかけらを、同じ行の続きの側にそろえる。書き順で続く同じ行の文字（layer.segments）を単位に見る。

    1. 行の頭の文字が図の外にあれば、図の範囲に入った残りの文字も頭の側に移す（「3-1.サージ誤差計算」の後半だけが
       グラフの範囲に入った例）
    2. どの領域にも入らなかった文字は、同じ行の中で字間 3 字分以内に隣り合う文字の領域に入れる（行末の「、」だけが
       本文の領域の外に出て、段落の後ろに 1 字だけの行として差し込まれるのを防ぐ）
    """
    figures = {i for i, b in enumerate(blocks) if b.cls in FIGURE_CLASSES}
    owner = {}
    for i, idx in enumerate(assigned):
        for k in idx:
            owner[k] = i
    changed = False
    for seg in layer.segments(pool):
        first = owner.get(seg[0], -1)
        if figures and first not in figures:
            for k in seg[1:]:
                if owner.get(k, -1) in figures:
                    owner[k] = first
                    changed = True
        for pos, k in enumerate(seg):
            if owner.get(k, -1) != -1:
                continue
            ch = layer.chars[k]
            near = None
            for step in (-1, 1):
                j = pos + step
                while 0 <= j < len(seg):
                    other = layer.chars[seg[j]]
                    gap = (ch.x0 - other.x1) if step < 0 else (other.x0 - ch.x1)
                    if gap > 3.0 * max(ch.size, 1.0):
                        break
                    if owner.get(seg[j], -1) != -1:
                        near = owner[seg[j]]
                        break
                    j += step
                if near is not None:
                    break
            if near is not None:
                owner[k] = near
                changed = True
    if not changed:
        return
    for idx in assigned:
        idx.clear()
    orphans.clear()
    for k in pool:
        o = owner.get(k, -1)
        (orphans if o == -1 else assigned[o]).append(k)


def _table(page, b, rect, idx, stats):
    """表の領域を行の並びにする。図として扱うべき表なら "figure" を返す。

    セルの文字は文字層に寄せる（align_cells。1 字ずつ対応する読み違い「室化→窒化」を文字層の字に直す）。ただし
        ・領域の面積が送った画像の BIG_TABLE_AREA を超える表は、枠などの取り違えとみなし、文字層の段落として読む
        ・Nemotron Parse の字の半分以上が文字層に無い表（グラフを表に直して値を作った例）は、図として扱う
        ・Nemotron Parse の字数が領域の文字層の TABLE_MIN_COVERAGE 未満なら、取りこぼしとみなし、文字層に戻す
        ・寄せた後も表の字の TABLE_MISREAD_SHARE を超えて文字層に無い字が残る表は、読み誤りとみなし、文字層に戻す
        ・領域の文字層の行のかけらに、表のセルのどれにも突き合わないものがある表は、行の欠けとみなし、文字層に戻す
          （unmatched_pieces）
        ・文字層の行を表の行に順を保って対応させ、対応した表の行のセルに無い語がある表は、列の落ちや値の移った読み違いと
          みなし、文字層に戻す（row_mismatches）
    文字層に戻した表は _layer_table で組み立てる。字数と字の照合は、結合セルの中身を 1 回だけ数える。
    """
    layer = page.layer
    bx0, by0, bx1, by1 = b.bbox
    img_share = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    lines = layer.lines(idx) if idx else []
    tl_text = paragraph_text(lines, reflow=False)
    in_rect = _layer_count(layer, rect)
    if img_share > BIG_TABLE_AREA:
        # 枠やページ全体を 1 つの表にした取り違え。文字層の段落として読む
        stats["tables_to_text"] += 1
        if idx:
            return _text_lines(paragraph_text(lines, reflow=True))
        return _text_lines(plain_text(b.text)) if in_rect == 0 else []
    rows = table_rows(b.text)
    cells = [c for row in (rows or []) for c, _colspan, _rowspan in row]
    nem = _chars(" ".join(cells)) if rows else _chars(b.text)   # 結合セルの中身は 1 回だけ数える
    tl = _chars(tl_text)
    if len(tl) >= TL_MIN_CHARS:
        if nem and _bag_precision(nem, tl) < 0.5:
            return "figure"
        if not rows or len(nem) < TABLE_MIN_COVERAGE * len(tl):
            stats["tables_to_text"] += 1
            return _layer_table(page, rect, idx, lines, tl_text, stats)
        rows, absent = align_cells(rows, tl)
        if absent > TABLE_MISREAD_SHARE:
            stats["tables_to_text"] += 1
            stats["tables_misread"] = stats.get("tables_misread", 0) + 1
            return _layer_table(page, rect, idx, lines, tl_text, stats)
        cut = cut_lines(layer, lines)
        if unmatched_pieces(layer, cut, rows):
            # 表の行の欠け（来歴の表で「・2-3項の確認用を判定用に変更」の行が無い。字数は 7 割を超えるので上の規則では
            # 見つからない）。欠けた行を補うには行と列の位置を推し量る必要があるので、表を文字層に戻す
            stats["tables_to_text"] += 1
            stats["tables_missing_rows"] = stats.get("tables_missing_rows", 0) + 1
            return _layer_table(page, rect, idx, lines, tl_text, stats)
        if row_mismatches(lines, cut, rows):
            # 行ごとの整合が取れない（文字層の行の語が、その行に対応する表の行のセルに無い）。列を落として値が別の列に
            # 結び付いた表（材料試験成績表）や、別の行に同じ字の並びがある行の欠け（HS-RU-004E の部位①）。表を文字層に戻す
            stats["tables_to_text"] += 1
            stats["tables_row_mismatch"] = stats.get("tables_row_mismatch", 0) + 1
            return _layer_table(page, rect, idx, lines, tl_text, stats)
        if absent:
            # 寄せきれなかった字（縦書きの欄など、文字層の並びが表と違う所の読み違い）。評価で見られるように数える
            stats["table_cells_unverified"] = stats.get("table_cells_unverified", 0) + 1
    elif in_rect >= TL_MIN_CHARS:
        # 範囲の文字層の字は他の領域（重なった小さい領域）に入って出力済み。Nemotron Parse の文字で 2 回目を出さない
        return _text_lines(tl_text)
    if not rows:
        return _text_lines(plain_text(b.text)) if in_rect == 0 else _text_lines(tl_text)
    stats["tables_gfm"] += 1
    return [Line(row, kind="final") for row in to_gfm(place(rows))]


# 来歴の記録の行の頭（版の記号＋改正・改訂＋日付。document_layer_rules.parse_history が読む形）
HISTORY_RECORD = re.compile(r"(?m)^\s*[0-9A-Z]\s*改\s*[正訂]\s*[:：]?\s*(?:'?\d{2,4}\s*[./．]\s*\d{1,2}\s*[./．]\s*\d{1,2}"
                            r"|[HSR]\s*\d{1,2}\s*[./．]\s*\d{1,2}\s*[./．]\s*\d{1,2})")


def _layer_table(page, rect, idx, lines, plain, stats) -> list:
    """Nemotron Parse の表を捨てた表を、文字層から組み立てる。lines は領域の文字層の見た目の行、plain はその文字列。

    表の領域に罫線があれば、線分から欄を求めて文字を欄に割り当てた GFM の表にする（tablegrid.ruled_grid。罫線の範囲の外の行は
    表の前後に置く）。罫線が無ければ、見た目の行ごとに、大きな字間で切ったかけらの間を「 | 」で区切る（かけらが 1 つの行は
    今までどおりの本文の行。番号の見出しの判定に回す）。どちらも、行の中のかけらの並びは x の順（値と列名の結び付きを残す）。
    来歴の記録の表（HISTORY_RECORD）は、文書層の規則が読む形のまま本文の行にする。
    """
    layer = page.layer
    if HISTORY_RECORD.search(nfkc(plain)):
        # 来歴の記録の表（「C改正 02.08.19 一部見直し 担当 …」）は今までどおりの本文の行にする。文書層の規則（parse_history）が
        # 「版の記号＋改正＋日付」の続いた行から版と日付を読むので、「 | 」や表の欄で区切ると読めなくなる（VTR-T003D の 4. 基準来歴）
        stats["tables_history"] = stats.get("tables_history", 0) + 1
        return _text_lines(plain)
    built = ruled_grid(layer, idx, rect, page.frame.hs, page.frame.vs) if idx else None
    if built:
        before, grid, after = built
        stats["tables_grid"] = stats.get("tables_grid", 0) + 1
        out = [ln for t in before for ln in _text_lines(t)]
        out += [Line(row, kind="final") for row in to_gfm(grid)]
        return out + [ln for t in after for ln in _text_lines(t)]
    stats["tables_pieces"] = stats.get("tables_pieces", 0) + 1
    out = []
    for ln in lines:
        pieces = [t for t in (layer.text(p, reflow=False).strip() for p in line_pieces(layer, ln)) if t]
        if len(pieces) > 1:
            text = normalize_text(" | ".join(pieces))
            if text.strip() and not _noise_line(text):
                out.append(_final(text))
        else:
            out += _text_lines(" ".join(pieces))
    return out


def line_pieces(layer: TextLayer, ln) -> list:
    """見た目の行を、大きな字間（textlayer.split_by_gap）で切ったかけら（文字の通し番号の並び、左から右）にする"""
    chars = sorted((layer.reading_char(k) for k in ln.indices), key=lambda c: c.x0)
    return [[c.i for c in piece] for piece in split_by_gap(chars)]


def cut_lines(layer: TextLayer, lines) -> list:
    """見た目の行ごとに、かけら（line_pieces）とその語（piece_words）の組の並びにする。unmatched_pieces と row_mismatches が使う"""
    return [[(piece, piece_words(layer, piece)) for piece in line_pieces(layer, ln)] for ln in lines]


def _word_chars(text: str) -> str:
    """突き合わせに使う字（文字と数字だけ。書き分けをそろえ、合成の字は分ける: Ⅱ → II、㎜ → mm）。
    ○・―・％ などの記号は読み違い（○ → 0）や空欄の印が多いので見ない"""
    out = []
    for c in _chars(text):
        if c.isalnum():
            parts = unicodedata.normalize("NFKC", c)
            out.extend(_fold(p) for p in (parts if len(parts) > 1 else c) if p.isalnum())
    return "".join(out)


def piece_words(layer: TextLayer, piece) -> list:
    """かけら（文字の通し番号の並び、左から右）を語の文字列にする。字間が字の大きさの半分を超える所で切り、字間の空いた 1 字の語の
    並び（「部 品 名」「材 料 試 験 成 績 表」。字の大きさの SPACED_GAP 倍以内の字間で続くもの）は 1 つの語につなぐ
    （1 字ずつの語にすると突き合わせの語の長さに満たず、表の欠けが見つからない）"""
    words = []
    for k in piece:
        ch = layer.reading_char(k)
        if words and ch.x0 - words[-1][-1].x1 <= 0.5 * max(words[-1][-1].size, 1.0):
            words[-1].append(ch)
        else:
            words.append([ch])
    out = []        # [字の並び, 1 字の語（をつないだもの）か, 最後の字]
    for w in words:
        text = "".join(c.c for c in w)
        if (out and len(w) == 1 and out[-1][1]
                and w[0].x0 - out[-1][2].x1 <= SPACED_GAP * max(w[0].size, 1.0)):
            out[-1][0] += text
            out[-1][2] = w[0]
        else:
            out.append([text, len(w) == 1, w[-1]])
    return [t for t, _single, _last in out]


def unmatched_pieces(layer: TextLayer, cut, rows) -> list:
    """表の領域の文字層の行を大きな字間でかけら（セル）に切ったもの（cut_lines）のうち、表のセルのどれにも突き合わない
    かけらの文字列を返す。

    突き合わせは、かけらを語（piece_words。字間の空いた 1 字の並びは 1 語）ごとに、2 字の並び（文字と数字だけ。_word_chars）で見る。
    語の字のうち、セルをつないだ字の並びにある 2 字の並びに掛からない字を数え、かけら全体で MISSING_MIN_CHARS 字以上、かつかけらの字の
    MISSING_SHARE 以上なら、表に無いかけらとみなす。複数行のセルで行の順が違っても 2 字の並びは残り、1 字の読み違い（VTR0.1 → VTRO.1）では
    1〜2 字しか外れないので数えない。縦書きの欄の字が横の欄と 1 行に並んだもの（「ン Ｐ冷 冷」）は語が短いので数えない。
    """
    cells = "".join(_word_chars(cell) for row in rows for cell, _cs, _rs in row)
    pairs = {cells[k:k + 2] for k in range(len(cells) - 1)}
    out = []
    for line_cut in cut:
        for piece, piece_word_list in line_cut:
            total = missing = 0
            for word in piece_word_list:
                flat = _word_chars(word)
                total += len(flat)
                if len(flat) < MISSING_MIN_CHARS:
                    continue
                covered = [False] * len(flat)
                for k in range(len(flat) - 1):
                    if flat[k:k + 2] in pairs:
                        covered[k] = covered[k + 1] = True
                missing += covered.count(False)
            if missing >= MISSING_MIN_CHARS and missing >= MISSING_SHARE * total:
                out.append(layer.text(piece, reflow=False))
    return out


# 行ごとの整合で、読み違いの多い英数字をそろえる（O と 0、I・l と 1）。突き合わせにだけ使う
_LENIENT = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1"})


def _row_words(text: str) -> str:
    return _word_chars(text).translate(_LENIENT)


def _hits(word: str, pairs: set, singles: set) -> list:
    """語の字ごとに、表の行のセルの字の並びにあるか（2 字以上の語は 2 字の並びで、1 字の語はその字で見る）"""
    if len(word) == 1:
        return [word in singles]
    hit = [False] * len(word)
    for k in range(len(word) - 1):
        if word[k:k + 2] in pairs:
            hit[k] = hit[k + 1] = True
    return hit


def _covered(word: str, pairs: set, singles: set) -> int:
    return sum(_hits(word, pairs, singles))


def row_mismatches(lines, cut, rows) -> list:
    """表の行ごとの整合: 文字層の見た目の行が、表のどの行から来たかを決め、その行のセルに無い語を返す。

    表の行（展開前のセルの行。multirow のセルは下の行にも数える）ごとに、セルの字（_word_chars。O と 0、I・l と 1 はそろえる）の
    2 字の並びを作る。文字層の行を上から、表の行を上から、順を保って（上の文字層の行ほど上の表の行に）対応させ、対応した表の行の
    字の並びに掛かる字の数の合計が最も大きくなるように決める（動的計画法。どの表の行にも掛からない文字層の行は対応させない）。
    対応した表の行のセルに無い語（piece_words。語の字の MISSING_SHARE 以上が掛からないもの。表の行の字は一度ずつしか使えないとして、
    上の文字層の行から使った字を減らしていく。すぐ上の SPILL_ROWS 行のまだ使っていない字で読める語は、結合した欄が下へはみ出した
    ものとみなして数えない）の字が、文字層の 1 行で MISSING_MIN_CHARS 字以上あれば、その行を返す。
    表全体の字の並びで見る unmatched_pieces では、別の行に同じ字の並びがある欠け（HS-RU-004E の部位①の「≦１（合計３以下）」は
    部位④の行にもある）や、値が隣の行に移った読み違い（材料試験成績表の「161 Pass」）が見つからない。
    lines は文字層の見た目の行、cut はその行のかけらと語（cut_lines）。
    返り値は [(文字層の行の文字列, 対応させた表の行の番号, セルに無い語の並び)]。
    """
    n = len(rows or [])
    if not n:
        return []
    # 表の行ごとの字の並び: その行のセルの字（隣り合う 1 字のセルはつなぐ。字間の空いた改訂記号「A B C」は 1 語につなぐので。
    # 他のセルの境はつながない。「≧785」「≧930」から「59」の並びを作らない）と、上の行から続く multirow のセルの字
    texts = [[] for _ in range(n)]
    for r, row in enumerate(rows):
        own, run = [], ""
        for cell, _cs, _rs in row:
            w = _row_words(cell)
            if not w:
                continue
            if len(w) == 1:
                run += w
                continue
            if run:
                own.append(run)
                run = ""
            own.append(w)
        if run:
            own.append(run)
        texts[r][:0] = own
        for cell, _cs, rs in row:
            w = _row_words(cell)
            if w:
                for rr in range(r + 1, min(n, r + max(1, rs))):
                    texts[rr].append(w)
    pairs = [{t[k:k + 2] for t in ts for k in range(len(t) - 1)} for ts in texts]
    singles = [set("".join(ts)) for ts in texts]
    words, score = [], []
    for line_cut in cut:
        ws = [w for _piece, piece_word_list in line_cut for w in (_row_words(t) for t in piece_word_list) if w]
        words.append(ws)
        score.append([sum(_covered(w, pairs[r], singles[r]) for w in ws) for r in range(n)])
    # best[i][r]: 文字層の行 0..i-1 を、表の行 r 以下に順を保って対応させたときの合計の最大
    L = len(lines)
    best = [[0] * n for _ in range(L + 1)]
    take = [[False] * n for _ in range(L + 1)]
    for i in range(1, L + 1):
        for r in range(n):
            here = best[i - 1][r] + score[i - 1][r]
            left = best[i][r - 1] if r else 0
            # 同点なら上の表の行に対応させる（同じ字の並びのある隣り合う行「⑤ 表面欠陥 ≦5 全面」「⑥ 表面欠陥 ≦5」を 1 つの行に寄せない）
            if score[i - 1][r] > 0 and here > left and here >= best[i - 1][r]:
                best[i][r], take[i][r] = here, True
            else:
                best[i][r] = max(left, best[i - 1][r])
    assigned = {}
    i, r = L, n - 1
    while i > 0 and r >= 0:
        if take[i][r]:
            assigned[i - 1] = r
            i -= 1
        elif r and best[i][r] == best[i][r - 1]:
            r -= 1
        else:
            i -= 1
    # セルに無い語を数える。表の行の字は一度ずつしか使えない（上の文字層の行から順に、使った字を減らす）。同じ表の行に対応した
    # 2 つの文字層の行が、表の行に 1 つしか無い字の並びを両方で使うもの（欠けた行の字の並びが別の行にある）も欠けとみなす。
    # 欠けが残る行は、前の文字層の行の対応から、次の文字層の行の対応かすぐ下の表の行までの範囲で別の表の行も試し、最も欠けの
    # 少ない行に付け替える（同じ字の並びのある隣り合う行「SCM … HBW」「1.貫通欠陥無し」で、下の行の文字層の行が上の表の行に
    # 寄せられたもの。同点なら上の表の行に寄せるので、下へ 1 行は試す）
    left = [Counter("".join(ts)) for ts in texts]

    def above(w, r):
        """語が、すぐ上の表の行（SPILL_ROWS 行上まで）のまだ使っていない字で読めるか。結合した欄（備考の欄が 2 行に渡る）の中身を
        Nemotron Parse は上の行に置くが、文字層では欄の下の方の行が次の行の見た目の行に混じる（HS-RU-010C の「西暦末尾 メーカーマーク」）"""
        need = Counter(w)
        for rr in range(max(0, r - SPILL_ROWS), r):
            if all(_hits(w, pairs[rr], singles[rr])) and all(left[rr][c] >= k for c, k in need.items()):
                return True
        return False

    def missing_in(li, r, bag, spill=True):
        missing, lost, used = 0, [], Counter()
        for w in words[li]:
            hit = _hits(w, pairs[r], singles[r])
            for k, c in enumerate(w):
                if hit[k]:
                    if bag[c] - used[c] > 0:
                        used[c] += 1
                    else:
                        hit[k] = False
            miss = len(w) - sum(hit)
            if miss and miss >= MISSING_SHARE * len(w) and not (spill and above(w, r)):
                missing += miss
                lost.append(w)
        return missing, lost, used

    order = sorted(assigned)
    out = []
    for pos, li in enumerate(order):
        r = assigned[li]
        missing, lost, used = missing_in(li, r, left[r])
        if missing >= MISSING_MIN_CHARS:
            # 付け替え先は、上の行のはみ出しを欠けから除かずに比べて選ぶ（はみ出しで説明できる語ばかりの行に付け替えて、入れ替わった
            # 値を見逃さないように）
            best_raw = missing_in(li, r, left[r], spill=False)[0]
            lo = assigned[order[pos - 1]] if pos else 0
            hi = min(n - 1, max(assigned[order[pos + 1]] if pos + 1 < len(order) else n - 1, r + 1))
            for alt in range(lo, hi + 1):
                if alt == r:
                    continue
                raw = missing_in(li, alt, left[alt], spill=False)[0]
                if raw < best_raw:
                    r, best_raw = alt, raw
            missing, lost, used = missing_in(li, r, left[r])
            assigned[li] = r
        left[r] -= used
        if missing >= MISSING_MIN_CHARS:
            out.append((lines[li].text, r, lost))
    return out


ALIGN_MAX_RUN = 3                      # 文字層の字に置き換える読み違いの長さの上限（長い食い違いは並びの違いとみなす）
MARKUP_CHARS = set("_*^`\\${}~")
# 突き合わせで同じ字とみなす書き分け（ダッシュ・波形・≦≧・中黒・ピリオド・ローマ数字など）
_FOLD = {}
for _group in ("-‐‑‒–—―−➖－ー─━", "~～〜∼", "≤≦", "≥≧", "·・･•", ".．", ",，", ":：", ";；", "/／", "=＝", "×✕",
               "°˚゜º", "(（", ")）", "[［", "]］", "<＜", ">＞", "+＋", "%％", "Oo○〇", "*＊※", "φΦФфϕ"):
    for _c in _group:
        _FOLD[_c] = _group[0]


def _fold(ch: str) -> str:
    if ch in _FOLD:
        return _FOLD[ch]
    n = unicodedata.normalize("NFKC", ch)
    return n if len(n) == 1 else ch


def _ascii_alnum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _char_class(ch: str) -> str:
    """読み違いとみなす置き換えの相手を、同じ種類の字（漢字どうし・かなどうし・記号どうし）に限るための分類"""
    o = ord(ch)
    if ch.isdigit():
        return "digit"
    if ch.isascii() and ch.isalpha():
        return "latin"
    if 0x3041 <= o <= 0x30FA or 0xFF66 <= o <= 0xFF9D:
        return "kana"
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or ch in "々〆ヶ":
        return "kanji"
    return "other"


def _plain_segment(text: str) -> bool:
    """かな・漢字・数字を含まない部分（°C と ℃、IIS と ⅡS のような書き分け）"""
    return all(_char_class(c) in ("latin", "other") for c in text)


def _repair_from_context(chars: list, tl: str, present: set) -> None:
    """文字層に無い字を、前後の字が文字層の中で一致する箇所の字に直す（並びの違う部分に残った読み違い「·室化部」→「・窒化部」）。
    前後の字は文字層にある字に限り、候補の字が 1 つに決まり、同じ種類の字（漢字どうし・かなどうし・記号どうし）のときだけ直す
    （分数の「/」を文字層の別の字にしない）"""
    for _ in range(3):
        changed = False
        for i, ch in enumerate(chars):
            if not ch.strip() or _fold(ch) in present:
                continue
            for lw, rw in ((2, 2), (1, 2), (2, 1), (1, 1), (0, 3), (3, 0)):
                left, right = chars[max(0, i - lw):i], chars[i + 1:i + 1 + rw]
                if len(left) < lw or len(right) < rw or any(_fold(c) not in present for c in left + right):
                    continue
                pattern = "(?=%s(.)%s)" % (re.escape("".join(left)), re.escape("".join(right)))
                found = set(re.findall(pattern, tl))
                if len(found) == 1 and _char_class(next(iter(found))) == _char_class(ch) != "digit" and _char_class(ch) != "latin":
                    chars[i] = found.pop()
                    changed = True
                    break
        if not changed:
            return


def align_cells(rows, tl: str):
    """表のセルの文字を、領域の文字層の字の並び tl（空白を除いた正規化済みの文字列）に寄せる。

    セルを読み順につないだ字の並びと tl を、書き分けをそろえて（_FOLD）difflib で突き合わせ、
        一致した字は文字層の書き方に（≤ → ≦、. → ．、— → ―）、
        1 つのセルの中の 3 字以内の同じ字数の置き換えのうち、文字層のどこにも無い字（室化 → 窒化、プラスト → ブラスト）は
        1 字ずつ同じ種類の文字層の字に（文字層にある字どうしの食い違いは並びの違いなので直さない。英数字も直さない）、
        1 つのセルの中の 1 字違いの短い置き換え（°C → ℃）はその部分を文字層の字に、
        文字層に 1 つも無い記法の残りの字（_ * ^ など）と HTML の印（<sup>）は先に消す（= や / などの記号は分数の線で
        文字層に無いことがあるので残す）、
        1 つのセルの中で前後が一致し、Nemotron Parse が落とした 2 字以内の記号（範囲の「～」など）は文字層から補う。
    並びの違う部分（複数行のセル）は、前後の字で文字層の箇所が 1 つに決まる字だけを直す（_repair_from_context）。
    返り値は (寄せた行の並び, 寄せた後も文字層のどこにも無い字の割合)。ダッシュ（空の欄の印）は数えない。
    セルごとの割合では判定しない（1 字のセルの書き分け「一」と「―」や、縦書きの欄の 1 字の読み違いで、正しい表の構造まで
    捨てることになるため。56 本で確かめた）。
    """
    # HTML の印と、文字層に 1 つも無い記法の字（_italic_ の _ など。書き分けをそろえて見る）は先に外す
    tl_folded = {_fold(c) for c in tl}
    strip = {c for c in MARKUP_CHARS if _fold(c) not in tl_folded}
    texts = ["".join(ch for ch in normalize_text(HTML_TAG.sub("", c)) if ch not in strip) for row in rows for c, _cs, _rs in row]
    chars, owner = [], []
    for ci, text in enumerate(texts):
        for ch in text:
            if not ch.isspace():
                chars.append(ch)
                owner.append(ci)
    seq = "".join(chars)
    new = list(seq)
    # 文字層にある字（書き分けをそろえたもの。㎜・№・Ⅳ のような合成の字は NFKC で分けた字も数える）
    present = {_fold(c) for c in tl} | {_fold(c) for c in unicodedata.normalize("NFKC", tl)}
    if seq and tl:
        matcher = difflib.SequenceMatcher(None, [_fold(c) for c in seq], [_fold(c) for c in tl], autojunk=False)
        ops = matcher.get_opcodes()
        for n, (tag, i1, i2, j1, j2) in enumerate(ops):
            if (tag == "insert" and j2 - j1 <= 2 and 0 < i1 < len(seq) and owner[i1 - 1] == owner[i1]
                    and all(_char_class(c) == "other" for c in tl[j1:j2])
                    and ops[n - 1][0] == "equal" and n + 1 < len(ops) and ops[n + 1][0] == "equal"):
                new[i1 - 1] = new[i1 - 1] + tl[j1:j2]   # セルの中で落ちた記号（2.3 2.7 の間の「～」）を文字層から補う
                continue
            if tag == "equal":
                for k in range(i2 - i1):
                    if seq[i1 + k] != "ー":     # 長音の「ー」は文字層のダッシュ（縦書きの「｜」など）に替えない
                        new[i1 + k] = tl[j1 + k]
            elif tag == "replace" and len(set(owner[i1:i2])) == 1 and max(i2 - i1, j2 - j1) <= ALIGN_MAX_RUN:
                # 直すのは文字層のどこにも無い字だけ（文字層にある字どうしの食い違いは並びの違い。「備考」と「材質」の入れ替わり）
                if i2 - i1 == j2 - j1:
                    for k in range(i2 - i1):
                        a, b = seq[i1 + k], tl[j1 + k]
                        if (_fold(a) not in present and _char_class(a) == _char_class(b)
                                and _char_class(a) in ("kana", "kanji", "other")):
                            new[i1 + k] = b      # 同じ種類の字の読み違い（英数字は並びの違いと区別できないので直さない）
                        elif a == "0" and b in "○〇" and "0" not in present:
                            new[i1 + k] = b      # ○ を 0 と読んだもの（文字層に 0 が 1 つも無い領域だけ。HS-TP-023 の付表）
                elif (abs((i2 - i1) - (j2 - j1)) == 1 and _plain_segment(seq[i1:i2]) and _plain_segment(tl[j1:j2])
                        and any(_fold(c) not in present for c in seq[i1:i2])):
                    new[i1] = tl[j1:j2]          # °C → ℃ のような 1 字違いの書き分け
                    for k in range(i1 + 1, i2):
                        new[k] = ""
    # 突き合わせで対応しなかった字の書き分けも、文字層の書き方が 1 通りなら文字層に合わせる（· → ・、≤ → ≦）
    variants = {}
    for c in tl:
        variants.setdefault(_fold(c), set()).add(c)
    out_texts, pos, total, absent_total = [], 0, 0, 0
    for text in texts:
        slots = []                               # セルの字の並び（空白はそのまま）
        for ch in text:
            if ch.isspace():
                slots.append(ch)
            else:
                slots.append(new[pos])
                pos += 1
        flat = [c for slot in slots if not slot.isspace() for c in slot]
        _repair_from_context(flat, tl, present)
        for k, ch in enumerate(flat):
            options = variants.get(_fold(ch))
            if ch not in tl and options and len(options) == 1 and not _ascii_alnum(ch) and ch != "ー":
                flat[k] = next(iter(options))
        it = iter(flat)
        cell = "".join(slot if slot.isspace() else "".join(next(it) for _ in slot) for slot in slots)
        # 文字層に無い字を数える。ダッシュは空の欄の印（—）に使われるので数えない
        total += len(flat)
        absent_total += sum(1 for ch in flat if _fold(ch) not in present and _fold(ch) != "-")
        out_texts.append(cell)
    it = iter(out_texts)
    aligned = [[(next(it), cs, rs) for _c, cs, rs in row] for row in rows]
    return aligned, (absent_total / total if total else 0.0)


@dataclass
class Zone:
    """図の範囲の中か直下にある番号の見出しと、その下の文・式・定義の行（_figure_zones）"""
    head: str                   # 見出しの行（大きな字間で切った左端のかけら「3-1.サージ誤差計算」）
    y: float                    # 見出しの上端（ページの pt）
    body: list                  # 見出しの後ろに置く本文の行（y の順）
    internal: bool              # 見出しの行が図の範囲の文字か（図から戻す見出しの候補。章が文書にあるときだけ見出しにする）
    hint: bool = False          # 見出しの行が見出し・題の領域の行か


_HIRAGANA = re.compile(r"[ぁ-ゖ]")


def _statement_line(text: str) -> bool:
    """文・式・定義の行か（＝・≒ などを含む、句読点を含む、ひらがなを 3 字以上含む）。軸の目盛り（1.0 1.5 2.0）や凡例の短い語
    （エンジン特性曲線・試験実回転数・15%）は違う"""
    t = normalize_text(text)
    return bool(re.search(r"[=＝≒≠≦≧]|[。、]", t)) or len(_HIRAGANA.findall(t)) >= 3


def _numbered_line(text: str) -> bool:
    """番号の見出しの形の行か（章・節・点区切り・項「(1)」・号「1)」・目「a)」。その行だけで題になるもの）"""
    t = normalize_text(text).strip()
    if heading_number_line(t):
        return True
    for rx in (ITEM, NUMBERED, SUBITEM):
        m = rx.match(t)
        if m and m.group(2) and _is_heading_title(m.group(2), item=True):
            return True
    return False


def _zone_heading(text: str) -> bool:
    """図の近くの見出しとして扱う行か（節「3-1.」・点区切り「3.1」の番号で始まり、その行だけで題になる）"""
    return _is_heading_number_line(text) and heading_number_line(text)


def _figure_zones(page, blocks, rects, assigned, orphans) -> dict:
    """図の範囲の中か直下にある番号の見出し（グラフの範囲に呑まれた「3-1.サージ誤差計算」）の下の、文・式・定義の行を見出しの本文にする。

    見出しの行の出どころ: 図の範囲の文字（図から戻す見出しの候補）、どの領域にも入らない文字、図の直下の題・本文の領域の最初の行。
    いずれも行を大きな字間で切った左端のかけらで見る（右に並んだ「a=1.65」は見出しに入れない）。図ごとにいちばん上の見出しを使う。
    見出しの下（見出しの上端から、図と見出しの領域以外で次に始まる領域の上端まで）にある次の字を、見た目の行に組み直し
    （折り返しはつなぐ）、y の順に見出しの後ろの本文にする:
        図の範囲の字のうち、文・式・定義の行（_statement_line）と番号の見出しの形の行（_numbered_line「1)外観検査」）。
        軸の目盛りや凡例の短い語は図中の文字に残す
        見出しの領域の残りの字、見出しの行の右に並んだ字、どの領域にも入らない字（見出しと図の横の範囲にあるもの）
    VTR-T005B の添付資料では、サージ誤差の式と定義（a-b/b×100=X%、a=実測風量、b=マップ風量、計算例）がグラフの範囲に入り、
    見出しの前の図中の文字に出て 3-1 の節が空になっていた。返り値は {図の領域の番号: Zone}。移した字は assigned・orphans から外す。
    本文になる字が無ければ何もしない（見出しも元の場所のまま）。
    """
    layer = page.layer
    figures = [i for i, b in enumerate(blocks) if b.cls in FIGURE_CLASSES]
    if not figures:
        return {}
    margin_ok = set(_drop_margins(page, orphans))
    zones, used = {}, set()

    def first_piece(ln):
        pieces = line_pieces(layer, ln)
        return pieces[0] if pieces else []

    def box(ks):
        cs = [layer.chars[k] for k in ks]
        return (min(c.x0 for c in cs), min(c.y0 for c in cs), max(c.x1 for c in cs), max(c.y1 for c in cs),
                sorted(c.size for c in cs)[len(cs) // 2])

    shared = None   # どの領域にも入らない字の行と、本文系の領域の最初の行（字を移したときだけ作り直す）
    for fi in figures:
        fx0, fy0, fx1, fy1 = rects[fi]
        cands = []      # (上端, 見出しのかけら, 行の全体, 出どころ, 領域の番号, 文字列, 字の大きさ, (左端, 右端))
        if shared is None:
            shared = [("orphan", -1, layer.lines([k for k in orphans if k in margin_ok]))]
            shared += [("block", bj, layer.lines(assigned[bj])[:1]) for bj, bb in enumerate(blocks)
                       if bb.cls in TEXT_CLASSES and assigned[bj]]
        sources = [("internal", fi, layer.lines(assigned[fi]))] + shared
        for kind, owner, lines in sources:
            for ln in lines:
                piece = first_piece(ln)
                if not piece or used & set(piece):
                    continue
                text = layer.text(piece, reflow=False)
                if not _zone_heading(text):
                    continue
                x0, y0, x1, y1, size = box(piece)
                if fy0 - size <= y0 <= fy1 + 2.0 * size and x1 >= fx0 - 2.0 * size and x0 <= fx1:
                    cands.append((y0, piece, ln, kind, owner, text, size, (x0, x1)))
        if not cands:
            continue
        hy, hpiece, hline, kind, owner, htext, hsize, (hx0, hx1) = min(cands, key=lambda c: c[0])
        skip = {fi, owner} if kind == "block" else {fi}
        below = [rects[j][1] for j, bb in enumerate(blocks)
                 if j not in skip and bb.cls not in FIGURE_CLASSES and bb.cls not in FURNITURE_CLASSES
                 and rects[j][1] > hy + 0.5 * hsize]
        end = min(below, default=layer.height)
        hset = set(hpiece)
        moved = set()
        # 見出しの行の右に並んだ字（同じ行の残りのかけら）
        moved.update(k for k in hline.indices if k not in hset)
        # 見出しの領域の残りの字
        if kind == "block":
            moved.update(k for k in assigned[owner] if k not in hset)
        # 図の範囲の字のうち、見出しより下の文・式・定義の行（見出しの帯に掛かる図をすべて見る）
        for fj in figures:
            r = rects[fj]
            if r[3] < hy or r[1] > end:
                continue
            ks = [k for k in assigned[fj] if k not in hset and k not in moved and hy < layer.chars[k].cy < end]
            for ln in layer.lines(ks):
                if _statement_line(ln.text) or _numbered_line(ln.text):
                    moved.update(ln.indices)
        # どの領域にも入らない字（見出しと図の横の範囲にあるもの）
        lo, hi = min(fx0, hx0) - 2.0 * hsize, max(fx1, hx1) + 2.0 * hsize
        moved.update(k for k in orphans if k in margin_ok and k not in hset
                     and hy - 0.5 * hsize <= layer.chars[k].cy < end and lo <= layer.chars[k].cx <= hi)
        if not moved:
            continue
        body = paragraph_text(layer.lines(sorted(moved)), reflow=True).split("\n")   # 見た目の折り返しはつなぐ
        taken = moved | hset
        for idx in assigned:
            idx[:] = [k for k in idx if k not in taken]
        orphans[:] = [k for k in orphans if k not in taken]
        used |= taken
        shared = None
        hint = kind == "block" and blocks[owner].cls in HEADING_CLASSES | {"Caption"}
        zones[fi] = Zone(head=htext, y=hy, body=body, internal=(kind == "internal"), hint=hint)
    return zones


def _figure(page, idx, stats, zone: Optional[Zone] = None):
    """図・グラフの領域。範囲の文字層を図中の文字の行にする。見出しの番号で始まる行（グラフの範囲に呑まれた「3-1.サージ誤差計算」）は
    見出しの候補として先に出す。

    候補は strict の行にする（render_lines で、その章が文書にあるときだけ見出しになる）。見出しにならなかった候補は
    図中の文字の頭に戻す。zone（図の範囲の文字から取った見出しと、その下の文・式・定義の行。_figure_zones）があれば、
    その見出しの候補と本文の行をいちばん先に出す。
    """
    stats["figures"] += 1
    head, rest = [], []
    for ln in page.layer.lines(idx):
        (head if _is_heading_number_line(ln.text) else rest).append(ln)
    fig_text = FigureText(" ".join(normalize_text(ln.text) for ln in rest).strip())
    out = []
    if zone is not None:
        out = [Line(normalize_text(zone.head), hint=True, strict=True, figure=fig_text)] + _text_lines("\n".join(zone.body))
    out += [Line(normalize_text(ln.text), hint=True, strict=True, figure=fig_text) for ln in head]
    return out + [Line("", kind="figure", figure=fig_text)]
