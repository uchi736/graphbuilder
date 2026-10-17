"""PDF を Markdown に変換する（pymupdf4llm）。

変換後は .md と同じ経路（見出しで分割し、長い節は段落や行の切れ目で再分割）でチャンクにする。
文書ごとの規則は持たない。見出しかどうか、柱かどうかは、変換器が返すブロックの区分だけで決める。
"""
import json
import logging
import math
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from langchain_core.documents import Document

# ページの切れ目の印。分割のあとでページ番号に読み替え、本文からは消す（create_chunks.py）
PAGE_MARK = "<!-- page:{} -->"
PAGE_MARK_PATTERN = re.compile(r"<!-- page:(\d+) -->\n?")

# 変換器のブロックの区分
HEADING_CLASSES = {"section-header", "title"}
FURNITURE_CLASSES = {"page-header", "page-footer"}
# 画像がページの面積のこの割合以上を占め、文字の行が占める割合がこれ以下のページは、スキャンしたページとみなす
IMAGE_AREA_MIN = 0.5
TEXT_AREA_MAX = 0.05

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_PICTURE_MARK = re.compile(r"<!-- (?:Start|End) of picture text -->")
# 図の中の文字。変換器が同じ領域を 2 回書き出すことがあるので、ページ内の重複は 1 つにする
_PICTURE_TEXT = re.compile(r"<!-- Start of picture text -->(.*?)<!-- End of picture text -->", re.S)
_BR = re.compile(r"<br\s*/?>", re.I)
# 下線と、等幅の字体を表す印。字の飾りだけを表すので外す。和文の等幅フォントはコードと判定されやすく、
# 本文がバッククォートで囲まれる
_UNDERLINE = re.compile(r"</?u>", re.I)
# 上付き・下付き。指数や脚注番号は印を外すと値が変わる（10 の 5 乗が 105 になる）ので残す。
# 変換器は小さい字の和文も上付きと判定することがあるので、短い英数字のときだけ残す
_SCRIPT = re.compile(r"<(sup|sub)>(.*?)</\1>", re.S | re.I)
_SCRIPT_VALUE = re.compile(r"[+\-−]?[0-9A-Za-z]{1,4}[)\]*]?")
# 見出しでない本文の行頭にあって、Markdown の見出しやコードの囲みと読まれてしまう並び
_STRUCTURE_AT_LINE_START = re.compile(r"(?m)^([ \t]{0,3})(#{1,6}(?=[ \t])|~~~)")
_HEADING_MARK = re.compile(r"(?m)^([ \t]{0,3})#{1,6}[ \t]+")
# 全角の英数字と記号を半角に直す（文書番号などを検索語と揃える）。Markdown で意味を持つ記号は変えない
# 文末の ．！？ は、長い段落を文の切れ目で分けるときの目印なので、そのまま残す
_KEEP_FULLWIDTH = set("＃｜＊＿～｀＜＞［］＼．！？")
_FULLWIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F) if chr(code) not in _KEEP_FULLWIDTH}
_FULLWIDTH[0x3000] = 0x20
# 本文に HTML の書き方そのものが書かれている場合に、変換器の印と取り違えないための目印
_LITERAL_MARKS = ("<br", "<u>", "<!--")


def _script(match: re.Match) -> str:
    inner = match.group(2)
    return match.group(0) if _SCRIPT_VALUE.fullmatch(inner.strip()) else inner


def _clean(md: str, heading: bool = False, literal=frozenset()) -> str:
    """変換器が入れる注記・飾り・行内の改行タグを、通常の Markdown に直す。

    heading が偽のブロックでは、行頭の # や ~~~ が見出しやコードの囲みと読まれないよう無効にする。
    literal は、このページの本文にそのまま書かれている HTML の表記。それらは変換器の印ではないので残す。
    """
    md = _PICTURE_MARK.sub("", md)
    if "<!--" not in literal:
        md = _COMMENT.sub("", md)
    if "<u>" not in literal:
        md = _UNDERLINE.sub("", md)
    md = _SCRIPT.sub(_script, md.replace("`", "")).translate(_FULLWIDTH)
    if "<br" not in literal:
        lines = []
        for line in md.split("\n"):
            if line.lstrip().startswith("|"):
                # 表のセルの中の改行。別々の項目をつなげてしまわないよう、空白にする
                lines.append(_BR.sub(" ", line))
            else:
                lines.extend(_BR.split(line))
        md = "\n".join(lines)
    if not heading:
        md = _STRUCTURE_AT_LINE_START.sub(r"\1\\\2", md)
    return md


def _blocks(page: dict) -> list:
    """ページの Markdown を、変換器のブロックの並びに切り分ける。どのブロックにも入らない部分は本文として残す"""
    text = page.get("text") or ""
    blocks, cursor = [], 0
    boxes = sorted((b for b in page.get("page_boxes") or [] if b.get("pos")), key=lambda b: b["pos"][0])
    for box in boxes:
        start, stop = box["pos"]
        if start > cursor:
            blocks.append({"class": "text", "bbox": None, "text": text[cursor:start]})
        if stop > cursor:
            blocks.append({"class": box.get("class") or "text", "bbox": box.get("bbox"), "text": text[max(start, cursor):stop]})
            cursor = stop
    if cursor < len(text):
        blocks.append({"class": "text", "bbox": None, "text": text[cursor:]})
    return blocks


def _repeat_key(block: dict):
    """ページをまたいだ繰り返しを数えるための鍵。

    柱とノンブル（変換器が page-header / page-footer と区分したもの。落とす対象）は、数字だけが違うものを同じとみなす。
    見出し（印を外すだけで本文は残す）は、同じ文字列を同じとみなす。それ以外の区分のブロックは数えない（本文は落とさない）。
    """
    body = re.sub(r"\s+", "", _HEADING_MARK.sub(r"\1", _clean(block["text"], heading=True)))
    if not body:
        return None
    if block["class"] in FURNITURE_CLASSES:
        return True, re.sub(r"\d+", "0", body)
    if block["class"] in HEADING_CLASSES:
        return False, body
    return None


def _survey(file_path) -> dict:
    """ページごとに、文字の層があるか、本文に HTML の表記がそのまま書かれているかを調べる。

    scanned: 画像が大半を占め、文字の行がわずかなページ。outlined: 文字が 0 字で、図形だけがあるページ（輪郭化した文字など）。
    """
    import pymupdf

    result = {"total": 0, "scanned": [], "outlined": [], "literal": {}}
    with pymupdf.open(str(file_path)) as doc:
        result["total"] = len(doc)
        for number, page in enumerate(doc, start=1):
            area = page.rect.get_area() or 1.0
            text = page.get_text()
            image_area = sum((pymupdf.Rect(info["bbox"]) & page.rect).get_area() for info in page.get_image_info()) / area
            text_area = sum((pymupdf.Rect(block[:4]) & page.rect).get_area() for block in page.get_text("blocks") if block[6] == 0) / area
            if image_area >= IMAGE_AREA_MIN and text_area <= TEXT_AREA_MAX:
                result["scanned"].append(number)
            elif not text.strip() and page.get_drawings():
                result["outlined"].append(number)
            result["literal"][number] = frozenset(mark for mark in _LITERAL_MARKS if mark in text)
    return result


def _frame_pages(file_path) -> dict:
    """枠付きの様式のページを、別プロセス（src.document_sources.frame_pages）で読んで、ページ番号ごとの結果を返す。

    別プロセスにするのは、pymupdf4llm を読み込んだプロセスでは PyMuPDF の表の検出が変わり、枠の表が見つからなくなるため。
    失敗したときは空の辞書を返し、全ページを従来の経路で読む。
    """
    from src.shared.common_fn import get_value_from_env

    if not get_value_from_env("PDF_FRAME_TABLES", "true", "bool"):
        return {}
    code_dir = str(Path(__file__).resolve().parents[2])
    try:
        proc = subprocess.run([sys.executable, "-m", "src.document_sources.frame_pages", str(file_path)],
                              cwd=code_dir, capture_output=True, text=True, timeout=600)
    except Exception as error:
        logging.warning("PDF %s: frame analysis could not run (%s); using the plain route", Path(file_path).name, error)
        return {}
    if proc.returncode != 0:
        logging.warning("PDF %s: frame analysis failed: %s", Path(file_path).name, proc.stderr.strip()[-400:])
        return {}
    try:
        return {entry["page"]: entry for entry in json.loads(proc.stdout).get("pages", [])}
    except (ValueError, KeyError, TypeError) as error:
        logging.warning("PDF %s: frame analysis returned unreadable output (%s)", Path(file_path).name, error)
        return {}


def _frame_summary(frames: dict) -> dict:
    """文書の管理情報。各ページの枠から読めた番号・改訂記号・題名は多数決、来歴は最も長いもの、管理のページの番号。"""
    numbers, revisions, titles, dates, kinds = Counter(), Counter(), Counter(), Counter(), Counter()
    history, control = [], []
    for number, entry in sorted(frames.items()):
        kinds[entry.get("kind", "none")] += 1
        meta = entry.get("meta") or {}
        if meta.get("number"):
            numbers[meta["number"]] += 1
        if meta.get("revision"):
            revisions[meta["revision"]] += 1
        if meta.get("title"):
            titles[meta["title"]] += 1
        dates.update(meta.get("dates") or [])
        if len(meta.get("history") or []) > len(history):
            history = meta["history"]
        if entry.get("control"):
            control.append(number)

    def top(counter):
        return counter.most_common(1)[0][0] if counter else None

    return {"doc_number": top(numbers), "revision": top(revisions), "title": top(titles), "history": history,
            "dates": [d for d, _ in dates.most_common(5)], "kinds": dict(kinds), "control_pages": control}


def load_pdf_as_markdown(file_path):
    """PDF 全体を 1 本の Markdown にして返す。

    文字の層が無いページが過半数の PDF、または OCR の経路が有効で文字の無いページを含む PDF は None を返し、
    呼び出し側が従来の読み込み（OCR を含む）に進む。
    """
    import pymupdf4llm
    from src.shared.common_fn import get_value_from_env

    name = Path(file_path).name
    survey = _survey(file_path)
    missing = sorted(survey["scanned"] + survey["outlined"])
    if missing:
        ocr_enabled = get_value_from_env("EDC_PDF_CONVERT", "false", "bool")
        logging.warning("PDF %s: %d of %d pages have no text layer (scanned %s, drawings only %s); their content is NOT ingested without OCR",
                        name, len(missing), survey["total"], survey["scanned"][:30], survey["outlined"][:30])
        if ocr_enabled or len(missing) * 2 > survey["total"]:
            logging.warning("PDF %s: leaving the Markdown route so that the default loader can handle the scanned pages", name)
            return None

    # 枠付きの様式のページは、pymupdf4llm より先に、別プロセスで枠を表として読む（本文のセルの中だけを本文にする）
    frames = _frame_pages(file_path)
    pages = pymupdf4llm.to_markdown(str(file_path), page_chunks=True, use_ocr=False, show_progress=False)
    structured = any(page.get("page_boxes") for page in pages)
    if not structured:
        logging.warning("PDF %s: the converter returned no block classes; its heading lines are used as they are", name)
    page_blocks = []
    seen_on = defaultdict(set)
    for number, page in enumerate(pages, start=1):
        seen = set()

        def keep_first(match, seen=seen):
            key = match.group(1).strip()
            if key in seen:
                return ""
            seen.add(key)
            return match.group(0)

        blocks = _blocks(page)
        for block in blocks:
            block["text"] = _PICTURE_TEXT.sub(keep_first, block["text"])
            block["key"] = _repeat_key(block)
            if block["key"]:
                seen_on[block["key"]].add(number)
        page_blocks.append(blocks)

    # 柱とノンブル（変換器が page-header / page-footer と区分し、2 ページ以上に現れるもの）は、最初の 1 回だけ残す。
    # 見出しと区分されたブロックでも、同じ文字列が半数以上のページ（2 ページ以上）に現れるものは欄の名前であり、
    # 節の見出しではない。見出しの印を外して本文に戻す（本文は落とさない）
    heading_floor = max(2, math.ceil(len(pages) / 2))
    drop_furniture = get_value_from_env("PDF_DROP_RUNNING_HEADERS", "true", "bool")
    emitted = set()
    parts, dropped, demoted = [], 0, 0
    from src.document_sources.number_headings import promote_number_headings

    framed = 0
    for number, blocks in enumerate(page_blocks, start=1):
        literal = survey["literal"].get(number, frozenset())
        frame = frames.get(number) or {}
        if frame.get("kind") == "body" and (frame.get("markdown") or "").strip():
            # 枠の中の本文だけ。番号・改訂記号・配布先・承認欄は本文に入れない（文書の管理情報として別に持つ）
            text = promote_number_headings(_clean(frame["markdown"], heading=False, literal=literal))
            parts.append(f"{PAGE_MARK.format(number)}\n{text}")
            framed += 1
            continue
        if frame.get("kind") == "grid" and (frame.get("markdown") or "").strip():
            # 格子のページ（改正来歴・協議通知票・発行目録）。表全体を内容にし、見出しを立てて種類が分かるようにする
            label = frame.get("label") or "管理表"
            text = _clean(frame["markdown"], heading=False, literal=literal)
            parts.append(f"{PAGE_MARK.format(number)}\n## {label}\n\n{text}")
            framed += 1
            continue
        out = []
        for block in blocks:
            key = block["key"]
            count = len(seen_on[key]) if key else 0
            text = block["text"]
            heading = structured and block["class"] in HEADING_CLASSES
            if key and key[0] and count >= 2 and drop_furniture:
                if key in emitted:
                    dropped += 1
                    logging.debug("PDF %s page %d: running header/footer dropped: %s", name, number, text.strip()[:80])
                    continue
                emitted.add(key)
            elif heading and count >= heading_floor:
                text = _HEADING_MARK.sub(r"\1", text)
                heading = False
                demoted += 1
            out.append(_clean(text, heading=heading or not structured, literal=literal))
        text = re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()
        if text:
            parts.append(f"{PAGE_MARK.format(number)}\n{promote_number_headings(text)}")
    if not parts:
        return None
    logging.info("PDF converted to Markdown: %d pages from %s (%d framed, %d running headers/footers dropped, %d repeated labels demoted from headings)",
                 len(parts), name, framed, dropped, demoted)
    return [Document(page_content="\n\n".join(parts),
                     metadata={"source": str(file_path), "markdown": True, "markdown_strict": True,
                               "title": Path(file_path).stem, "pages_without_text": missing,
                               "frame_meta": _frame_summary(frames)})]
