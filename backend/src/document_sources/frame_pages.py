"""枠付きの様式（社内規格）の PDF を、枠を表として読んでページごとに本文と管理情報に分ける。

ページ全体の 7 割以上を覆う罫線の表を「枠」とみなす。枠の中でいちばん大きなセルが本文、残りのセルが
番号・名称・改訂記号・配布先・承認日・改正来歴である。本文のセルの中だけで文字を読み、同じ範囲で改めて
罫線の表を探して Markdown の表にする。枠の無いページは触らず、呼び出し側（pymupdf4llm の経路）に任せる。

このモジュールは PyMuPDF だけを使い、pymupdf4llm を読み込んではならない。pymupdf4llm は読み込み時に
レイアウト解析を有効にし、PyMuPDF の表の検出の挙動を変える（枠の表が見つからなくなる）。そのため
呼び出し側は、このモジュールを別プロセスで動かす（python -m ... <pdf>）。標準出力に JSON を書く。

ページの区分:
    body     本文のセルが 1 つに決まるページ（面積比 0.4 以上）。枠の線が画像で描かれていて表として見つからないページでも、
             左の帯・上の帯・下の帯に枠の語（配布先・控・合計・作成・審査・承認・来歴・番・号・HS）が並んでいれば、
             その語の位置から本文の範囲を決める（virtual=true）
    grid     セルが多く、大きなセルが無いページ。発行目録や協議・通知票。表全体を内容として Markdown の表にする
    none     枠が見つからないページ。呼び出し側の経路に任せる
    notext   文字層の無いページ
"""
import json
import re
import sys
import unicodedata

import pymupdf

FRAME_MIN_SHARE = 0.7      # ページの面積に対する枠の表の面積
BODY_MIN_SHARE = 0.4       # 枠の面積に対する本文のセルの面積
GRID_MIN_CELLS = 12
# 枠の線が無いページで、枠の語を探す帯（ページの幅・高さに対する割合）と、枠とみなすのに必要な語の数
STRIP_LEFT, STRIP_TOP, STRIP_BOTTOM = 0.15, 0.10, 0.86
VIRTUAL_MIN_LABELS = 4
BOTTOM_WORD = re.compile(r"改正回数|年月日|来歴|石川島|IHI|回転機械")
FRAME_LABEL = re.compile(r"^(配布先|控|合計|作成|審査|承認|来歴|番|号|HS|改正回数|年月日|原|計)$|^[回過技総][^\d]{1,5}\d?$|^[0-9]{1,2}$")
META_WORD = re.compile(r"(配布先|承\s*認|審\s*査|作\s*成|改正回数|改正番号|来\s*歴|年\s*月\s*日|控|合\s*計|番\s*号)")
# 格子のページの見出しに使う語。先に挙げた語を優先する（発行目録のページには見直し確認欄も載るので、発行目録を先に見る）
CONTROL_LABELS = ["発行目録", "基準来歴", "改正来歴", "改訂来歴", "来歴", "協議・通知票", "協議通知票", "通知票", "見直し確認欄", "見直し確認", "配布先一覧"]
CONTENT_LABELS = {"発行目録"}
# 管理のセルの読み取り。番号は「番 号」の次のセル（RU-002F、VTR-T001 E）、種類は「HS」のセル、題名は「来 歴」の次のセル
CELL_NUMBER = re.compile(r"^(?:HS-?)?([A-Z]{2,4})-([A-Z]?\d{2,4})([A-Z])?$")
CELL_REVISION = re.compile(r"^([A-Z]|[-－‐0])$")
LABEL_WORD = re.compile(r"改正|来歴|配布|承認|審査|作成|同意|合計|番号")
HISTORY_VERB = re.compile(r"(する|した|追加|変更|改訂|見直し|制定|移行|明記)[。.]?\s*(回|品|過|技|総|設)?")
DATE = re.compile(r"('?\d{2,4}[./]\d{1,2}[./]\d{1,2}|[HSR]\d{1,2}[./]\d{1,2}[./]\d{1,2})")


def _norm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _nfkc(text):
    """全角の英数字・記号・半角カナを揃える。文末の ．！？ は長い段落を文で分けるときの目印、丸数字 ①〜⑳ は図中の記号なので変えない。"""
    return "".join(c if (c in "．！？" or "\u2460" <= c <= "\u2473") else unicodedata.normalize("NFKC", c) for c in (text or ""))


_NUMBER_GAP = re.compile(r"\b([A-Z]{2,4})\s*-\s*([A-Z]?\d{2,4})\b")
_HS_GAP = re.compile(r"\bHS\s*-\s*(?=[A-Z])")


def _tidy(text):
    """文字を揃え、文書番号の中の空白（HS-RU -003、RU－ ００２）を詰める。"""
    text = _nfkc(text)
    return _HS_GAP.sub("HS-", _NUMBER_GAP.sub(r"\1-\2", text))


STAMP_LINE = re.compile(r"^\s*(改正番号\s*\d*|改\s*正)\s*$")


def _cell_text(page, rect):
    return _norm(_tidy(page.get_text("text", clip=rect)))


def _table_markdown(table, compact=False):
    rows = table.extract()
    rows = [[_norm(_tidy(c)) if c else "" for c in row] for row in rows if row and any(c and c.strip() for c in row)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    if compact:
        # 格子のページは空のセルが大半なので、どの行でも空の列を落とす。それでも半分以上が空なら（協議・通知票のような様式）、
        # 表の形をやめて、行ごとに埋まっているセルだけを並べる
        keep = [j for j in range(width) if any(r[j] for r in rows)]
        rows = [[r[j] for j in keep] for r in rows]
        width = len(keep)
        if width == 0:
            return ""
        filled = sum(1 for r in rows for c in r if c)
        if filled * 2 < len(rows) * width:
            lines = []
            for r in rows:
                cells = [c for c in r if c]
                if cells and any(len(c) > 1 for c in cells):  # 改訂記号だけの行（A B C …）は落とす
                    lines.append(" / ".join(cells))
            return "\n".join(lines)
    esc = lambda c: c.replace("|", "／")
    out = ["| " + " | ".join(esc(c) for c in rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows[1:]]
    return "\n".join(out)


def _aligned_tables(page, body):
    """罫線の無い表。同じ高さに短い項目が 3 つ以上並ぶ行が 2 行以上続く範囲を表とみなし、x 位置で列を揃えて Markdown の表にする。

    返り値は (範囲, Markdown) の並び。罫線が画像で描かれたページ（枠も表も線として検出できない）のために使う。
    """
    blocks = [(pymupdf.Rect(b[:4]), _norm(_tidy(b[4]))) for b in page.get_text("blocks", clip=body) if b[6] == 0 and b[4].strip()]
    blocks.sort(key=lambda t: (t[0].y0, t[0].x0))
    rows = []
    for rect, text in blocks:
        if rows and abs(rect.y0 - rows[-1]["y0"]) <= 3:
            rows[-1]["items"].append((rect, text))
            rows[-1]["y1"] = max(rows[-1]["y1"], rect.y1)
        else:
            rows.append({"y0": rect.y0, "y1": rect.y1, "items": [(rect, text)]})

    def is_table_row(row):
        return len(row["items"]) >= 3 and all(len(t) <= 24 for _, t in row["items"])

    runs, run = [], []

    def flush():
        if len(run) >= 2:
            runs.append(list(run))
        run.clear()

    for row in rows:
        if is_table_row(row):
            if run and row["y0"] - run[-1]["y1"] > max(45.0, 3.5 * (run[-1]["y1"] - run[-1]["y0"] + 1)):
                flush()
            run.append(row)
        else:
            flush()
    flush()
    out = []
    for run_rows in runs:
        xs = sorted(rect.x0 for row in run_rows for rect, _ in row["items"])
        cols = []
        for x in xs:
            if cols and x - cols[-1][-1] <= 12:
                cols[-1].append(x)
            else:
                cols.append([x])
        centers = [sum(c) / len(c) for c in cols]
        if len(centers) < 3:
            continue
        grid = []
        for row in run_rows:
            cells = [""] * len(centers)
            for rect, text in row["items"]:
                j = min(range(len(centers)), key=lambda k: abs(centers[k] - rect.x0))
                cells[j] = (cells[j] + " " + text).strip()
            grid.append(cells)
        esc = lambda c: c.replace("|", "／")
        md = ["| " + " | ".join(esc(c) for c in grid[0]) + " |", "|" + "---|" * len(centers)]
        md += ["| " + " | ".join(esc(c) for c in r) + " |" for r in grid[1:]]
        bbox = pymupdf.Rect(min(rect.x0 for row in run_rows for rect, _ in row["items"]), run_rows[0]["y0"],
                            max(rect.x1 for row in run_rows for rect, _ in row["items"]), run_rows[-1]["y1"])
        out.append((bbox, "\n".join(md)))
    return out


def _body_markdown(page, body, lined=True):
    """本文のセルの中の文字を読み順に並べ、同じ範囲の表は Markdown の表に置き換える。lined が偽なら罫線の無い表を項目の並びから組む。"""
    tables = []
    if lined:
        for t in page.find_tables(clip=body).tables:
            r = pymupdf.Rect(t.bbox)
            if r.get_area() < 400:
                continue
            md = _table_markdown(t)
            if md:
                tables.append((r, md))
    else:
        tables = _aligned_tables(page, body)
    blocks = []
    for b in page.get_text("blocks", clip=body):
        x0, y0, x1, y1, text, _, kind = b[:7]
        if kind != 0:
            continue
        rect = pymupdf.Rect(x0, y0, x1, y1)
        if any((rect & tr).get_area() > 0.5 * rect.get_area() for tr, _ in tables):
            continue
        lines = [_tidy(ln).rstrip() for ln in text.splitlines() if ln.strip() and not STAMP_LINE.match(ln)]
        if lines:
            blocks.append((y0, x0, "\n".join(lines)))
    parts = [(y0, x0, txt) for y0, x0, txt in blocks] + [(tr.y0, tr.x0, md) for tr, md in tables]
    parts.sort(key=lambda p: (round(p[0]), p[1]))
    return "\n\n".join(p[2] for p in parts)


def _meta_from_cells(page, cells, body, frame=None):
    """本文以外のセルから、文書の管理情報を読む。

    number    文書番号。「HS」のセルがあれば HS- を頭に付け、末尾の改訂記号は revision に分ける（RU-002F → HS-RU-002, F）
    title     題名。枠の上の帯にある、番号でも日付でも管理の語でもない最も長いセル。無ければ全セルから同じ条件で選ぶ
    history   改正の来歴。(改訂記号, 日付, 内容) の並びのセルを組にする
    dates     セルに現れた日付
    cells     セルの文字（先頭 200 字）
    """
    others = [c for c in cells if c != body]
    rows = []  # 同じ高さのセルを行にまとめ、上から下、左から右に並べる（来歴の 3 つ組を順に読むため）
    for c in sorted(others, key=lambda r: (r.y0, r.x0)):
        if rows and abs(c.y0 - rows[-1][0]) <= 3:
            rows[-1][1].append(c)
        else:
            rows.append((c.y0, [c]))
    ordered = [c for _, row in rows for c in sorted(row, key=lambda r: r.x0)]
    texts, rects = [], []
    for c in ordered:
        text = _cell_text(page, c)
        if text:
            texts.append(text)
            rects.append(c)
    flat = [re.sub(r"\s+", "", t) for t in texts]
    meta = {"cells": [t[:200] for t in texts], "number": None, "revision": None, "title": None, "history": [], "dates": []}
    family = "HS" if "HS" in flat else ""
    for f in flat:
        m = CELL_NUMBER.match(f)
        if m and not DATE.match(f):
            meta["number"] = "-".join(x for x in (family, m.group(1), m.group(2)) if x)
            meta["revision"] = m.group(3)
            break
    # 題名は枠の上の帯（番号・HS と同じ高さ）にある、番号でも管理の語でもない最も長いセル
    head_limit = frame.y0 + 0.12 * frame.height if frame is not None else page.rect.height * 0.15
    head = [t for t, f, r in zip(texts, flat, rects) if r.y0 <= head_limit and len(f) >= 6 and not CELL_NUMBER.match(f)
            and not DATE.search(f) and not LABEL_WORD.search(f) and f != "HS"]
    if head:
        meta["title"] = max(head, key=len)
    if not meta["title"]:
        candidates = [t for t, f in zip(texts, flat) if len(f) >= 8 and not DATE.search(f) and not LABEL_WORD.search(f)
                      and not HISTORY_VERB.search(f) and sum(ch.isdigit() for ch in f) < 0.3 * len(f)]
        if candidates:
            meta["title"] = max(candidates, key=len)
    # 来歴は 2 つの並び方がある。横並び（改正回数の行に記号、年月日の行に日付、来歴の行に内容）と、縦並び（記号・日付・内容が 1 行ずつ）
    def run_after(label_pattern, accept):
        for i, f in enumerate(flat):
            if re.fullmatch(label_pattern, f):
                out = []
                for g, t in zip(flat[i + 1:], texts[i + 1:]):
                    if LABEL_WORD.search(g) and not accept(g):
                        break
                    if accept(g):
                        out.append(t)
                    else:
                        break
                return out
        return []

    revisions = run_after(r"(改正回数)+", lambda g: bool(CELL_REVISION.match(g)))
    history_dates = run_after(r"(年月日)+", lambda g: bool(DATE.search(g)) and len(g) <= 12)
    notes = run_after(r"(来歴)+", lambda g: len(g) >= 2 and not DATE.search(g) and not CELL_REVISION.match(g))
    if revisions and len(revisions) == len(history_dates) and len(notes) >= len(revisions) - 1:
        for rev, date, note in zip(revisions, history_dates, notes + [""]):
            meta["history"].append({"revision": rev.replace("－", "-").replace("‐", "-"), "date": date, "note": note})
    else:
        for i in range(len(flat) - 2):
            if CELL_REVISION.match(flat[i]) and DATE.search(flat[i + 1]) and len(flat[i + 2]) >= 2 and not DATE.search(flat[i + 2]):
                meta["history"].append({"revision": flat[i].replace("－", "-").replace("‐", "-"), "date": texts[i + 1], "note": texts[i + 2]})
    for t in texts:
        meta["dates"] += [m.group(0) for m in DATE.finditer(t)]
    return meta


def _virtual_frame(page):
    """枠の線が無いページで、枠の語の位置から本文の範囲を決める。枠の語が足りなければ None。

    左の帯の枠の語（配布先・控・合計・承認…）の右端を本文の左端にする。上の帯の枠の語（番・号・HS・頁）と、それに
    縦に重なるブロック（題名）の下端を本文の上端にする。下の帯の枠の語（来歴・改正回数・年月日・社名）と、それに
    縦に重なるブロック（来歴の日付と内容）の上端を本文の下端にする。
    """
    W, H = page.rect.width, page.rect.height
    blocks = [(pymupdf.Rect(b[:4]), re.sub(r"\s+", "", _nfkc(b[4]))) for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    left_labels = [r for r, flat in blocks if r.x1 <= STRIP_LEFT * W and len(flat) <= 8 and FRAME_LABEL.match(flat)]
    if not left_labels:
        return None
    left = max(r.x1 for r in left_labels) + 2
    top_labels = [r for r, flat in blocks if r.x1 > left and r.y1 <= STRIP_TOP * H and len(flat) <= 8 and FRAME_LABEL.match(flat)]
    bottom_labels = [r for r, flat in blocks if r.x1 > left and r.y0 >= 0.80 * H
                     and ((len(flat) <= 20 and (FRAME_LABEL.match(flat) or BOTTOM_WORD.search(flat))) or len(DATE.findall(flat)) >= 2)]
    if len(left_labels) + len(top_labels) + len(bottom_labels) < VIRTUAL_MIN_LABELS:
        return None
    top = max((r.y1 for r in top_labels), default=STRIP_TOP * H)
    for _ in range(5):  # 題名のブロックは枠の語と縦に重なる
        grown = max([top] + [r.y1 for r, _ in blocks if r.x1 > left and r.y0 < top and r.y1 <= 0.2 * H])
        if grown == top:
            break
        top = grown
    bottom = min((r.y0 for r in bottom_labels), default=STRIP_BOTTOM * H)
    for _ in range(5):  # 来歴の日付と内容のブロックは枠の語と縦に重なる
        grown = min([bottom] + [r.y0 for r, _ in blocks if r.x1 > left and r.y1 > bottom and r.y0 >= 0.76 * H])
        if grown == bottom:
            break
        bottom = grown
    top, bottom, right = top + 2, bottom - 2, W - 0.03 * W
    if bottom - top < 0.4 * H or right - left < 0.5 * W:
        return None
    return pymupdf.Rect(left, top, right, bottom)


_TEXT_NUMBER = re.compile(r"\b(?:HS-?)?([A-Z]{2,4})-([A-Z]?\d{2,4})([A-Z])?\b")


def _virtual_meta(page, body):
    """仮想の枠のページで、本文より上のブロックから番号と題名を読む（HS TP-008C、RU・RH形舶用過給機 扇車素材 品質要求基準 RU-003H）。
    本文より下と左のブロック（来歴・承認・配布先）も管理のセルとして記録する。"""
    meta = {"cells": [], "number": None, "revision": None, "title": None, "history": [], "dates": []}
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    above = [_norm(_tidy(b[4])) for b in blocks if b[3] <= body.y0 + 2]
    below = [_norm(_tidy(b[4])) for b in blocks if b[1] >= body.y1 - 2 or b[2] <= body.x0 + 2]
    meta["cells"] = [t[:200] for t in above + below]
    joined = " ".join(above)
    m = _TEXT_NUMBER.search(joined)
    if m:
        family = "HS" if re.search(r"\bHS\b", joined) else ""
        meta["number"] = "-".join(x for x in (family, m.group(1), m.group(2)) if x)
        meta["revision"] = m.group(3)
    titles = []
    for t in above:
        rest = _norm(re.sub(r"\bHS\b|番|号|\b\d{1,2}\b", "", _TEXT_NUMBER.sub("", t)))
        if len(rest) >= 6 and not LABEL_WORD.search(rest):
            titles.append(rest)
    if titles:
        meta["title"] = max(titles, key=len)
    return meta


def analyze(path):
    pages = []
    with pymupdf.open(path) as doc:
        for number, page in enumerate(doc, start=1):
            text = page.get_text()
            if len(text.strip()) < 20:
                pages.append({"page": number, "kind": "notext"})
                continue
            area = page.rect.get_area() or 1.0
            frames = [t for t in page.find_tables().tables if pymupdf.Rect(t.bbox).get_area() / area >= FRAME_MIN_SHARE]
            if not frames:
                body = _virtual_frame(page)
                if body is None:
                    pages.append({"page": number, "kind": "none"})
                    continue
                lined = bool(page.get_drawings())
                pages.append({"page": number, "kind": "body", "virtual": True, "body_share": round(body.get_area() / area, 2),
                              "cells": 0, "markdown": _body_markdown(page, body, lined=lined), "meta": _virtual_meta(page, body)})
                continue
            frame = frames[0]
            cells = [pymupdf.Rect(c) for c in frame.cells if c]
            frame_area = pymupdf.Rect(frame.bbox).get_area() or 1.0
            body = max(cells, key=lambda r: r.get_area()) if cells else None
            share = body.get_area() / frame_area if body else 0.0
            if body is not None and share >= BODY_MIN_SHARE:
                pages.append({"page": number, "kind": "body", "body_share": round(share, 2), "cells": len(cells),
                              "markdown": _body_markdown(page, body), "meta": _meta_from_cells(page, cells, body, pymupdf.Rect(frame.bbox))})
            elif len(cells) >= GRID_MIN_CELLS or share < BODY_MIN_SHARE:
                md = _table_markdown(frame, compact=True)
                flat = re.sub(r"\s+", "", _nfkc(text)).replace("･", "・")
                label = next((w for w in CONTROL_LABELS if w in flat), "")
                control = (bool(label) and label not in CONTENT_LABELS) or (not label and bool(re.search(r"協議|通知票|見直し確認", flat)))
                pages.append({"page": number, "kind": "grid", "cells": len(cells), "markdown": md, "label": label,
                              "control": control, "meta": {"cells": [], "number": None, "revision": None, "title": None, "history": [], "dates": []}})
            else:
                pages.append({"page": number, "kind": "none"})
    return {"path": path, "pages": pages}


if __name__ == "__main__":
    result = analyze(sys.argv[1])
    json.dump(result, sys.stdout, ensure_ascii=False)
