"""項番の構造に沿って PDF をチャンクに分ける（社内規格・基準・規程向け）。

既定の分割（トークン数で機械的に切る）には、次の問題がある。
  - 文や項の途中で切れる。日本語は 1 文字を途中で割って文字化けすることもある。
  - 表題欄・配布先・改訂履歴などの枠の文字と、欄外の改訂番号が本文に混ざる。
  - どの文書のどの項かが、チャンクの本文から分からない。

ここでは PyMuPDF の行の座標を使い、
  1. ページごとに枠（表題欄・左の配布先欄・下の改訂履歴欄・欄外の改訂番号）を本文から外す
  2. 本文を「1.」「2-1」「3.2.1」などの項番の見出しで区切り、1 項 1 チャンクを基本にする
  3. 各チャンクの先頭に、文書名・表題・章と項の見出しを付ける
  4. 本文中の表は行ごとに「a | b | c」の形で残す
  5. 枠の文字は、文書ごとに 1 つの「文書情報」チャンクにまとめる
項番の見出しが 1 つも見つからない PDF では None を返し、呼び出し側は従来の分割に戻る。
"""
import logging
import re
import unicodedata
from pathlib import Path
from typing import List, Optional

from langchain_core.documents import Document

MAX_CHARS = 1000      # 1 チャンクの本文の上限（見出しの行を除く）
MIN_CHARS = 80        # これ未満の項は、同じ章の次の項とまとめる
CIRCLED = {chr(c) for c in range(0x2460, 0x2474)}   # ①〜⑳ は丸付きのまま残す

_FRAME_TOP = re.compile(r"^(番|号|番\s*号|HS|ＨＳ)$")
_FRAME_BOTTOM = re.compile(r"改正回数|来\s*歴|年\s*月\s*日|Ishikawajima|石\s*川\s*島")
_FRAME_LEFT = re.compile(r"^(配布先|控|合計|審査|承認|作成)$")
_REV_NOTE = re.compile(r"^改正番号\s*\d*$")
_DATE_ONLY = re.compile(r"^[\d\s./'’年月日-]+$")
_SENTENCE_AFTER_NUM = re.compile(r"^[\s]*(及び|および|項|、|,|と[^\s]|の[^\s]|に[^\s]|～|~|又は)")

_L1 = re.compile(r"^(\d{1,2})\s*[.．](?!\d)\s*(.*)$")
_L2_HYPHEN = re.compile(r"^(\d{1,2})\s*[-‐－−]\s*(\d{1,2})(?![\d])\s*[.．]?\s*(.*)$")
_L2_DOT = re.compile(r"^(\d{1,2})[.](\d{1,2})(?![\d.])\s*(.*)$")
_L3_DOT = re.compile(r"^(\d{1,2})[.](\d{1,2})[.](\d{1,2})(?![\d.])\s+(.*)$")
_APPENDIX = re.compile(r"^(別表|別紙|付表|添付資料|付録|別図)\s*[-‐－]?\s*(\d{0,2})\s*(.*)$")
_ITEM = re.compile(r"^(?:[（(]\s*\d{1,2}\s*[)）]|\d{1,2}\s*[)）]|[a-zA-Z]\s*[.．]|[①-⑳]|注\s*\d*\s*[)）:：]|[（(]\s*注\s*\d*\s*[)）]|・|※|[0-9A-Z]\s*改正)")
_DOC_NO = re.compile(r"^[A-Za-z]{2,5}\s*[-‐－]\s*[A-Za-z]?\s*\d+(\s*[A-Z])?$")
_ID_HEAD = re.compile(r"^[A-Za-z]{2,}(?:-[A-Za-z]+)*$")
_ID_TAIL = re.compile(r"^-[A-Za-z]*\d+[A-Za-z]?$")


def normalize(text: str) -> str:
    """全角英数・半角カナを標準の字形に揃える（丸付き数字はそのまま）。"""
    out = "".join(ch if ch in CIRCLED else unicodedata.normalize("NFKC", ch) for ch in text)
    return out.replace("�", "")


def _page_lines(page):
    """ページの行を (文字列, x0, y0, x1, y1) で返す。"""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                x0, y0, x1, y1 = line["bbox"]
                lines.append((text, x0, y0, x1, y1))
    return lines


def _split_frame(page, lines):
    """枠の行と本文の行に分ける。枠の有無はページごとに枠の語の位置で判定する。"""
    W, H = page.rect.width, page.rect.height
    has_top = any(y1 < 0.09 * H and _FRAME_TOP.match(normalize(t)) for t, x0, y0, x1, y1 in lines)
    bottoms = [y0 for t, x0, y0, x1, y1 in lines if y0 > 0.80 * H and _FRAME_BOTTOM.search(normalize(t))]
    has_left = any(x1 < 0.13 * W and _FRAME_LEFT.match(normalize(t)) for t, x0, y0, x1, y1 in lines)
    top_cut = 0.078 * H if has_top else 0.0
    bottom_cut = (min(bottoms) - 2.0) if bottoms else H
    left_cut = 0.126 * W if has_left else 0.0
    framed = has_top or bool(bottoms) or has_left
    long_x = sorted(x0 for t, x0, y0, x1, y1 in lines if len(t) >= 10 and top_cut <= y0 < bottom_cut and x0 >= left_cut)
    body_left = long_x[0] if long_x else 0.0
    body, frame = [], []
    for item in lines:
        t, x0, y0, x1, y1 = item
        nt = normalize(t)
        if y1 <= top_cut or y0 >= bottom_cut or (left_cut and x1 <= left_cut):
            frame.append(item)
        elif framed and x0 > 0.90 * W and len(nt) <= 3:
            continue                                  # 右上のページ番号・欄外の改訂番号
        elif framed and len(nt) <= 2 and x1 < body_left - 3 and re.fullmatch(r"[\d原]+", nt):
            continue                                  # 本文の左に置かれた改訂番号
        elif _REV_NOTE.match(nt):
            continue
        else:
            body.append(item)
    return body, frame, framed


def _tables(page, body):
    """本文中の表を取り出し、表の中にある行を本文から除く。枠そのもの（ページ全体の表）は対象外。"""
    out = []
    try:
        found = page.find_tables().tables
    except Exception:                                  # 表の検出に失敗しても本文は取り込む
        return body, out
    page_area = page.rect.width * page.rect.height
    for tb in found:
        x0, y0, x1, y1 = tb.bbox
        if (x1 - x0) * (y1 - y0) > 0.6 * page_area or tb.row_count < 2 or tb.col_count < 2:
            continue
        rows = [[normalize((c or "").replace("\n", " ")).strip() for c in row] for row in tb.extract()]
        cells = [c for row in rows for c in row]
        if not cells or sum(1 for c in cells if c) / len(cells) < 0.5:
            continue
        text = "\n".join(" | ".join(row) for row in rows if any(row))
        inside = [it for it in body if x0 - 2 <= (it[1] + it[3]) / 2 <= x1 + 2 and y0 - 2 <= (it[2] + it[4]) / 2 <= y1 + 2]
        if not inside:
            continue
        body = [it for it in body if it not in inside]
        out.append((text, x0, y0, x1, y1))
    return body, out


def _merge_same_row(items):
    """同じ高さにある行を左から順に 1 行にまとめる。"""
    items = sorted(items, key=lambda it: (round(it[2] / 4.0), it[1]))
    rows, cur, cur_y = [], [], None
    for it in items:
        if cur and abs(it[2] - cur_y) <= 3.0:
            cur.append(it)
        else:
            if cur:
                rows.append(cur)
            cur, cur_y = [it], it[2]
    if cur:
        rows.append(cur)
    return [(" ".join(c[0] for c in sorted(r, key=lambda c: c[1])), min(c[1] for c in r), r[0][2]) for r in rows]


def _classify(line: str, state: dict):
    """行が見出しかどうかを判定する。戻り値: ("l1"|"l2"|"appendix"|"item"|"text", 見出しの文字列)"""
    nt = normalize(line).strip()
    if not nt or _DATE_ONLY.match(nt):
        return "text", None
    m = _APPENDIX.match(nt)
    if m and len(nt) <= 60:
        return "appendix", nt
    m = _L3_DOT.match(nt)
    if m and int(m.group(1)) == state.get("l1_no"):
        return "l2", nt                                # 3.2.1 は 3.2 と同じ扱いで項の区切りにする
    for pat in (_L2_HYPHEN, _L2_DOT):
        m = pat.match(nt)
        if m and int(m.group(1)) == state.get("l1_no") and not _SENTENCE_AFTER_NUM.match(m.group(3) or ""):
            if pat is _L2_DOT and not re.search(r"[^\d\s.]", m.group(3) or ""):
                continue                               # 「1.0」「2.5」のような数値
            return "l2", nt
    m = _L1.match(nt)
    if m:
        no = int(m.group(1))
        rest = m.group(2) or ""
        expected = state.get("l1_no", 0) + 1
        if (no == expected or (no == 1 and state.get("l1_no", 0) == 0)) and not re.match(r"^[\d.]+\s*(rps|rpm|%|℃|kPa|MPa|mm)?$", rest):
            return "l1", nt
    if _ITEM.match(nt):
        return "item", None
    return "text", None


def _join(prev: str, line: str) -> str:
    """折り返された行をつなぐ。英数字同士のときだけ空白を入れる。"""
    if not prev:
        return line
    if prev[-1].isascii() and not prev[-1].isspace() and line[:1].isascii() and not line[:1].isspace():
        return prev + " " + line
    return prev + line


def _heading_is_title(heading: str) -> bool:
    """見出しの行が、表題だけの行か（True）、次の行へ続く文の 1 行目か（False）を見分ける。"""
    body = re.sub(r"^[\d\s.．\-‐－−]+", "", heading)
    return len(body) <= 30 or body.endswith(("。", ")", "）", "」"))


def _merge_wrapped_ids(rows: List[str]) -> List[str]:
    """表の中で複数行に割れた番号（「HS-VTR」…「-T001」）を 1 行に戻し、その間の行も同じ行にまとめる。"""
    out: List[str] = []
    for row in rows:
        head, _, rest = row.partition(" ")
        if _ID_TAIL.match(head):
            for back in range(1, min(3, len(out)) + 1):
                prev_head, _, prev_rest = out[-back].partition(" ")
                if _ID_HEAD.match(prev_head):
                    middle = out[len(out) - back + 1:]
                    merged = " ".join(x.strip() for x in [prev_head + head, prev_rest, rest] + middle if x.strip())
                    del out[-back:]
                    out.append(merged)
                    break
            else:
                out.append(row)
        else:
            out.append(row)
    # 改訂記号の欄（A B C … と 1 文字が並ぶだけの部分）は情報を持たないので除く
    return [re.sub(r"(?:(?<=\s)|^)(?:[A-Z]\s+){3,}[A-Z](?=\s|$)", "", r).strip() for r in out]


def _split_long(text: str, limit: int) -> List[str]:
    """長い項を、段落（小項目）の境目、次に句点で、上限以下に分ける。"""
    if len(text) <= limit:
        return [text]
    units = []
    for para in text.split("\n"):
        if len(para) <= limit:
            units.append(para)
            continue
        for sentence in re.split(r"(?<=。)", para):
            while len(sentence) > limit:               # 句点の無い長い行は上限で切る
                units.append(sentence[:limit])
                sentence = sentence[limit:]
            if sentence:
                units.append(sentence)
    parts, cur = [], ""
    for unit in units:
        if cur and len(cur) + len(unit) + 1 > limit:
            parts.append(cur)
            cur = unit
        else:
            cur = (cur + "\n" + unit) if cur else unit
    if cur:
        parts.append(cur)
    return parts


def load_structured_pdf(file_path, max_chars: int = MAX_CHARS, min_chars: int = MIN_CHARS) -> Optional[List[Document]]:
    """PDF を項番の構造に沿ったチャンクにして返す。構造が見つからなければ None。"""
    import fitz

    path = Path(file_path)
    stem = normalize(path.stem)
    pdf = fitz.open(str(path))
    title_lines, frame_text = [], []
    sections = []          # {"scope", "l1", "l2", "paras": [..], "pages": set}
    state = {"l1_no": 0}
    cur = {"scope": None, "l1": None, "l2": None, "paras": [], "pages": set()}
    n_headings = 0
    break_next = False     # 直前が表題だけの見出しなら、次の行は新しい段落にする
    plain_rows: List[str] = []   # 見出しが無い文書のための、本文の行の並び

    def flush():
        nonlocal cur
        if any(p.strip() for p in cur["paras"]):
            sections.append(cur)
        cur = {"scope": cur["scope"], "l1": cur["l1"], "l2": cur["l2"], "paras": [], "pages": set()}

    for page_index, page in enumerate(pdf):
        body, frame, framed = _split_frame(page, _page_lines(page))
        body, tables = _tables(page, body)
        if framed:
            ordered = sorted(frame, key=lambda it: (round(it[2] / 4.0), it[1]))
            top = [normalize(t) for t, x0, y0, x1, y1 in ordered if y1 < 0.09 * page.rect.height]
            if page_index == 0:
                title_lines = [t for t in top if len(t) > 2 and not _FRAME_TOP.match(t) and not _DOC_NO.match(t)]
                frame_text = [normalize(t) for t, *_ in ordered]
        rows = _merge_same_row(body) + [(text, x0, y0) for text, x0, y0, x1, y1 in tables]
        table_texts = {text for text, *_ in tables}
        for text, x0, y0 in sorted(rows, key=lambda r: (round(r[2] / 4.0), r[1])):
            if text in table_texts:
                cur["paras"].append("[表]\n" + text)
                cur["pages"].add(page_index + 1)
                plain_rows.append("[表]\n" + text)
                break_next = True
                continue
            line = normalize(text).strip()
            plain_rows.append(line)
            kind, heading = _classify(line, state)
            if kind == "appendix":
                flush()
                state["l1_no"] = 0
                cur.update(scope=heading, l1=None, l2=None)
                cur["paras"].append(heading)
                n_headings += 1
            elif kind == "l1":
                flush()
                state["l1_no"] = int(_L1.match(heading).group(1))
                cur.update(l1=heading, l2=None)
                cur["paras"].append(heading)
                n_headings += 1
            elif kind == "l2":
                flush()
                cur.update(l2=heading)
                cur["paras"].append(heading)
                n_headings += 1
            elif kind == "item" or not cur["paras"] or break_next:
                cur["paras"].append(line)
            else:
                cur["paras"][-1] = _join(cur["paras"][-1], line)
            break_next = kind in ("appendix", "l1", "l2") and _heading_is_title(heading)
            cur["pages"].add(page_index + 1)
    flush()

    title = " ".join(title_lines).strip()
    label = f"{stem}｜{title}" if title else stem

    if n_headings == 0:
        rows = [r for r in _merge_wrapped_ids(plain_rows) if not re.fullmatch(r"(?:[A-Za-z]\s*)+", r)]
        if sum(len(r) for r in rows) < 50:
            logging.info("structured_pdf: no text structure in %s; falling back to the default splitter", path.name)
            return None
        parts = _split_long("\n".join(rows), max_chars)
        logging.info("structured_pdf: %s has no clause headings -> %d chunks by rows", path.name, len(parts))
        return [Document(page_content=f"【{label}】" + (f"（{k + 1}/{len(parts)}）" if len(parts) > 1 else "") + "\n" + part,
                         metadata={"source": str(path), "page": 0, "page_number": 1, "section": "本文", "section_no": "",
                                   "chunk_kind": "body", "structured": True}) for k, part in enumerate(parts)]

    def short(h):
        return (h[:40] + "…") if h and len(h) > 41 else h

    # 短い項は、同じ章（同じ別表）の次の項とまとめる
    merged = []
    for sec in sections:
        text = "\n".join(sec["paras"])
        if merged and len(merged[-1]["text"]) < min_chars and merged[-1]["scope"] == sec["scope"] and merged[-1]["l1"] == sec["l1"] \
                and len(merged[-1]["text"]) + len(text) <= max_chars:
            merged[-1]["text"] += "\n" + text
            merged[-1]["pages"] |= sec["pages"]
            if sec["l2"] and sec["l2"] not in merged[-1]["l2s"]:
                merged[-1]["l2s"].append(sec["l2"])
        else:
            merged.append({"scope": sec["scope"], "l1": sec["l1"], "l2s": [sec["l2"]] if sec["l2"] else [], "text": text, "pages": set(sec["pages"])})

    docs: List[Document] = []
    info = "【" + label + "｜文書情報】"
    if frame_text:
        docs.append(Document(page_content=info + "\n" + " ".join(frame_text),
                             metadata={"source": str(path), "page": 0, "page_number": 1, "section": "文書情報", "section_no": "", "chunk_kind": "docinfo", "structured": True}))
    for sec in merged:
        crumbs = [short(x) for x in (sec["scope"], sec["l1"]) if x] + ([" / ".join(short(x) for x in sec["l2s"])] if sec["l2s"] else [])
        head = "【" + label + "】" + (" > ".join(crumbs) if crumbs else "")
        section_id = " > ".join(x for x in (sec["scope"], sec["l1"], sec["l2s"][0] if sec["l2s"] else None) if x) or "本文"
        nos = [m.group(0).replace(" ", "") for m in (re.match(r"^\d{1,2}(?:\s*[-.]\s*\d{1,2}){0,2}", normalize(h).replace("‐", "-").replace("－", "-").replace("−", "-")) for h in (sec["l2s"] or ([sec["l1"]] if sec["l1"] else []))) if m]
        section_no = ",".join(n.rstrip(".") for n in nos)
        parts = _split_long(sec["text"], max_chars)
        for k, part in enumerate(parts):
            suffix = f"（{k + 1}/{len(parts)}）" if len(parts) > 1 else ""
            first_page = min(sec["pages"]) if sec["pages"] else 1
            docs.append(Document(page_content=f"{head}{suffix}\n{part}",
                                 metadata={"source": str(path), "page": first_page - 1, "page_number": first_page,
                                           "section": section_id[:200], "section_no": section_no,
                                           "chunk_kind": "table" if part.startswith("[表]") else "body", "structured": True}))
    logging.info("structured_pdf: %s -> %d chunks (%d headings)", path.name, len(docs), n_headings)
    return docs
