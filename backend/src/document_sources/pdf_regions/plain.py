"""PDF を、文字層（pypdfium2）だけで簡易に Markdown にする。Nemotron Parse は呼ばない。AGPL の部品を使わない。

regions（converter.convert_pdf）が使えなかったとき（失敗して None・例外、ページ数が max_pages を超える）の戻り道
（local_file.load_document_content）。

ページごとに、regions で Nemotron Parse が失敗したページと同じ組み立て（assemble.fallback_page。枠の本文の欄・格子の様式・
上下の端の柱の除去・改正の印の除去・段落）を行い、見出しは番号の規則（headings.render_lines）で付ける。
その組み立てが例外になったページは、文字層の行をそのまま並べる。表の格子・図の扱いは無い（表は行の並びになる）。
regions と違い、字の向きを回したページの柱から付属のラベルを取らない（converter._rotated_header を呼ばない）。

文字層の無いページ（スキャン・輪郭にした文字）の扱いは regions と同じ: 過半数なら None（ocr_route が真なら 1 ページでもあれば None）
を返し、呼び出し側の次の経路（EDC の OCR・ページごとの読み込み）に進ませる。この判定は組み立ての前に行う。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from . import pdfpage
from .assemble import PageInput, PageOutput, fallback_page
from .converter import REOPEN_PAGES, ConvertResult, _Batch, _page_count, _render, _survey, _too_many_missing
from .frame import detect_page_frame, frame_title, grid_label
from .headings import Line, normalize_text

LOG = "pdf_regions.plain"


def _page_output(page, number: int):
    """1 ページを読んで組み立てる（PDFIUM_LOCK の中で呼ぶ）。返り値は (PageOutput, 枠の題名)"""
    layer = pdfpage.text_layer(page)
    try:
        extra = layer.dominant_rotation()
        if extra:
            layer = pdfpage.text_layer(page, extra)
        hs, vs = pdfpage.line_segments(page, extra)
        frame = detect_page_frame(layer, hs, vs)
        inp = PageInput(number=number, layer=layer, frame=frame, extra=extra, needs_parse=False)
        title = frame_title(layer, frame) if frame.kind == "body" else None
        if frame.kind == "grid":
            inp.label = grid_label(layer, frame)
        return fallback_page(inp, "plain"), title
    except Exception as error:
        logging.warning("%s: page %d could not be assembled (%s); using the raw text lines", LOG, number, error)
        lines = []
        for ln in layer.lines(layer.ink_indices()):
            text = normalize_text(ln.text).strip()
            if text:
                lines.append(Line(text))
        return PageOutput(number, lines, framed=False, stats={"fallback": True, "reason": f"raw: {error}"}), None


def convert_pdf_plain(path, ocr_route: bool = False, title_line: bool = True) -> Optional[ConvertResult]:
    """PDF をページごとの Markdown にする（文字層だけ）。失敗したら None。ページ数の上限は無い"""
    name = Path(path).name
    started = time.time()
    count = _page_count(path, name, LOG)
    if not count:
        return None
    missing = _survey(path, name, count, LOG)
    if missing is None:
        return None
    if _too_many_missing(missing, count, ocr_route):
        logging.warning("%s: %s: %d of %d pages have no text layer (%s); leaving it to the next loader",
                        LOG, name, len(missing), count, missing[:30])
        return None
    if missing:
        logging.warning("%s: %s: %d of %d pages have no text layer (%s); their content is NOT ingested",
                        LOG, name, len(missing), count, missing[:30])
    outputs, titles = [], []
    try:
        for start in range(0, count, REOPEN_PAGES):
            with _Batch(path) as batch, pdfpage.PDFIUM_LOCK:
                for i in range(start, min(count, start + REOPEN_PAGES)):
                    page = batch.doc[i]
                    try:
                        out, title = _page_output(page, i + 1)
                    except Exception as error:
                        logging.warning("%s: %s page %d could not be read (%s)", LOG, name, i + 1, error)
                        continue
                    finally:
                        page.close()
                    outputs.append(out)
                    if title:
                        titles.append(title)
    except Exception as error:
        logging.warning("%s: cannot read %s (%s)", LOG, name, error)
        return None
    pages = _render(outputs)
    if not pages:
        return None
    title = normalize_text(titles[0]).strip() if (title_line and titles) else None
    stats = {"file": name, "pages": count, "seconds": round(time.time() - started, 2), "converter": "plain",
             "no_text_layer_pages": missing, "page_stats": [dict(o.stats, page=o.number) for o in outputs]}
    logging.info("%s: %s converted on the text layer only (%d pages, %.1fs)", LOG, name, count, stats["seconds"])
    return ConvertResult(pages=pages, title=title, stats=stats)
