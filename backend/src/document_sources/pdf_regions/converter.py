"""PDF を、ページごとの Markdown にする（Graph Builder に依存しない部分の入口）。

流れ:
  0. 全ページの文字層の有無を軽く数える。文字層の無いページ（スキャン・輪郭にした文字）が過半数なら None
     （ocr_route が真なら、文字層の無いページが 1 つでもあれば None。今の変換と同じく、OCR を含む従来の読み込みに進ませる）
  1〜3 をページごとに別スレッドで進める（同時に持つページは作業スレッドの数まで。読み終えたページの画像は手放す。
     pdfium の文書は REOPEN_PAGES ページごとに開き直し、pdfium が溜める字形・画像の領域を手放す）
  1. pypdfium2 で文字層・罫線・ページ画像を読む。pdfium はスレッド安全ではないので、プロセスに 1 つの錠
     （pdfpage.PDFIUM_LOCK）の中で読む。同じプロセスで同時に走る別の変換とも、この錠で順番に読む
  2. 枠を求める。本文の欄のあるページは本文の欄だけを、枠の無いページは全体を Nemotron Parse に送る。
     格子の様式のうち、ページの題の位置に種類の語があるもの（協議・通知票・発行目録）は送らず、欄の文字を行ごとに並べる。
     種類の語の無い格子のページ（別紙の台帳など）はページ全体を送る（frame.grid_label）
  3. 領域と文字層を合わせて組み立てる（assemble.py）。図は既定（figures=text）では図中の文字と題だけ。describe なら E4B → 26B
  4. 文書全体を通して見出しの段を番号の規則で決める（headings.py）
Nemotron Parse・E4B・26B への同時の要求は、接続先ごとの上限で抑える（limits.py。プロセスの間でも共有する）。

締め切り（deadline 秒、0 なら無し）: 過ぎたら、まだ送っていないページは Nemotron Parse に送らず文字層の行で組み立て、
図は説明を省いて図中の文字だけにする。応答しない接続先は limits の遮断で、しばらく呼ばずにすぐ失敗にする。

失敗の扱い: 文書を開けない、読めるページが無い、ページ数が max_pages を超える、文字層の無いページが多い、
Nemotron Parse を要するページの半分を超えて失敗した、のいずれかなら None を返す（呼び出し側が今の変換に戻す）。
失敗が全ページの半分を超えた時点で、残りのページは送らない。ページ単位の失敗は、そのページだけ文字層の行で組み立てて続ける。
"""
from __future__ import annotations

import io
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import pdfpage
from .assemble import PageInput, assemble_page, fallback_page
from .frame import detect_page_frame, frame_title, grid_label
from .headings import DocState, doc_number_keys, header_annex, header_notes, normalize_text, render_lines
from .nemotron import NemotronClient
from .vlm import FigureReader, VlmClient

FIGURE_MODES = ("describe", "text", "off")
# 文字層の無いページ（pdf_markdown._survey と同じ条件）: 画像が 5 割以上で文字の行が 5% 以下、または字が 0 で図形だけ
IMAGE_AREA_MIN = 0.5
TEXT_AREA_MAX = 0.05


@dataclass
class RegionsConfig:
    parse_url: str = "http://localhost:8022/v1"
    parse_model: str = "nemotron-parse"
    triage_url: str = "http://localhost:8030/v1"
    triage_model: str = "google/gemma-4-E4B-it"
    vlm_url: str = "http://localhost:8000/v1"
    vlm_model: str = "google/gemma-4-26B-A4B-it"
    figures: str = "text"               # text（既定。VLM を呼ばず図中の文字と題だけ）| describe | off
    timeout: float = 180.0              # 1 回の要求の時間切れ（秒）。締め切りが近ければ締め切りまでに縮める
    retries: int = 2                    # 接続の失敗・5xx のときの送り直しの回数（応答の時間切れは送り直さない）
    parse_concurrency: int = 2          # Nemotron Parse への同時の要求の上限（コンテナの中の全作業プロセスで共有）
    vlm_concurrency: int = 2            # E4B・26B それぞれへの同時の要求の上限（同上。26B は画像付きの同時 6 本でハングした記録がある）
    deadline: float = 600.0             # 文書 1 本の締め切り（秒、0 なら無し）
    max_pages: int = 300                # これを超えるページ数の PDF は変換しない（None。今の変換に戻す）
    ocr_route: bool = False             # 真なら、文字層の無いページが 1 つでもあれば None（EDC_PDF_CONVERT の経路に任せる）
    lock_dir: Optional[str] = None      # プロセスをまたぐ上限のロック用ファイルの置き場所（None は既定、"" は使わない）
    title_line: bool = True             # 枠の題名の欄を文書の先頭に見出しとして置く（graph_builder.TITLE_LEVEL）
    big_table_area: float = 0.6
    table_min_coverage: float = 0.7
    long_side: int = pdfpage.LONG_SIDE_PX
    max_failed_share: float = 0.5       # Nemotron Parse を要するページのうち、これを超えて失敗したら None

    def normalized(self) -> "RegionsConfig":
        figures = (self.figures or "text").strip().lower()
        if figures not in FIGURE_MODES:
            logging.warning("pdf_regions: unknown figures mode %r; using text", self.figures)
            figures = "text"
        self.figures = figures
        self.parse_concurrency = max(1, int(self.parse_concurrency))
        self.vlm_concurrency = max(1, int(self.vlm_concurrency))
        self.deadline = max(0.0, float(self.deadline or 0))
        self.max_pages = max(0, int(self.max_pages or 0))
        return self


@dataclass
class ConvertResult:
    pages: list                 # [(ページ番号, Markdown)]（中身の無いページは入れない）
    title: Optional[str]
    stats: dict = field(default_factory=dict)


def _prepare(page, number: int, cfg: RegionsConfig, own_keys=frozenset()) -> PageInput:
    """pdfium でページを読む（文書を開いたスレッドで呼ぶ）。

    字の多くが同じ向きに倒れたページ（/Rotate は 0 のまま、横向きの付表を縦のページに置いたもの。HS-TP-023 の 5 ページ目）は、
    字が正立する向きに回して読む（文字層・罫線・画像とも。Nemotron Parse にも回した画像を送る）。回す前の向きで正立していた字
    （縦のページの上の柱「HS-TP-023 付表」）は回した後は倒れた字になり、柱の付属のラベルなら header_annex として取り出す。
    """
    layer = pdfpage.text_layer(page)
    extra = layer.dominant_rotation()
    if extra:
        layer = pdfpage.text_layer(page, extra)
    W, H = layer.width, layer.height
    hs, vs = pdfpage.line_segments(page, extra)
    frame = detect_page_frame(layer, hs, vs)
    inp = PageInput(number=number, layer=layer, frame=frame, extra=extra)
    if extra:
        inp.note = f"text rotated {extra}"
        _rotated_header(inp, own_keys)
    if frame.kind == "body":
        inp.title = frame_title(layer, frame)
    if frame.kind == "grid":
        inp.label = grid_label(layer, frame)
        if inp.label:
            inp.needs_parse = False
            return inp
    rotation = 0
    try:
        rotation = int(page.get_rotation() or 0)
    except Exception:
        pass
    if rotation % 360 and not extra:
        # 回転したページは、文字層だけで組み立てる（座標は表示の向きにそろえてあるが、Nemotron Parse での確認をしていない）
        inp.needs_parse = False
        inp.note = f"rotated {rotation}"
        return inp
    image, scale = pdfpage.render_image(page, cfg.long_side, extra)
    if frame.kind == "body" and frame.body:
        x0, y0, x1, y1 = frame.body
        box = (max(0, math.floor(x0 * scale)), max(0, math.floor(y0 * scale)),
               min(image.width, math.ceil(x1 * scale)), min(image.height, math.ceil(y1 * scale)))
        image = image.crop(box)
        inp.view = tuple(v / scale for v in box)
    else:
        inp.view = (0.0, 0.0, W, H)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    inp.png = buf.getvalue()
    return inp


def _rotated_header(inp: PageInput, own_keys=frozenset()) -> None:
    """回したページで、回す前の向きで正立していた字（いまは倒れた字）の行のうち、付属のラベルの柱（「HS-TP-023 付表」）を
    ページの付属のラベルにし、その字は本文に入れない"""
    layer = inp.layer
    rotated = [ch.i for ch in layer.chars if ch.ink and ch.rot]
    for ln in layer.lines(rotated):
        label = header_annex(ln.text, own_keys) if inp.annex_hint is None else None
        if label:
            inp.annex_hint = label
            inp.annex_notes = header_notes(ln.text)
            inp.drop.update(ln.indices)


def _missing_text(survey: dict) -> bool:
    if survey["chars"] == 0:
        return survey["paths"] > 0 or survey["image_share"] >= IMAGE_AREA_MIN
    return survey["image_share"] >= IMAGE_AREA_MIN and (survey["text_share"] or 0.0) <= TEXT_AREA_MAX


def convert_pdf(path, config: Optional[RegionsConfig] = None, parser=None, reader=None) -> Optional[ConvertResult]:
    """PDF をページごとの Markdown にする。失敗したら None。parser・reader は試験で差し替える"""
    cfg = (config or RegionsConfig()).normalized()
    name = Path(path).name
    started = time.time()
    deadline = started + cfg.deadline if cfg.deadline else None
    try:
        with _Batch(path) as batch, pdfpage.PDFIUM_LOCK:
            count = len(batch.doc)
    except Exception as error:
        logging.warning("pdf_regions: cannot open %s (%s)", name, error)
        return None
    return _convert(path, name, count, cfg, started, deadline, parser, reader)


REOPEN_PAGES = 32


class _Batch:
    """pdfium の文書を開いて閉じる。pdfium は開いている文書の中に、読んだページの字形・画像を溜めていく
    （56 本をつないだ 287 ページで約 260MiB）。閉じれば手放すので、REOPEN_PAGES ページごとに開き直して上限を抑える"""

    def __init__(self, path):
        self.path = path
        self.doc = None

    def __enter__(self):
        with pdfpage.PDFIUM_LOCK:
            self.doc = pdfpage.open_document(self.path)
        return self

    def __exit__(self, *exc):
        with pdfpage.PDFIUM_LOCK:
            try:
                self.doc.close()
            except Exception:
                pass
        return False


def _convert(path, name, count, cfg, started, deadline, parser, reader) -> Optional[ConvertResult]:
    lock = pdfpage.PDFIUM_LOCK
    if count == 0:
        return None
    if cfg.max_pages and count > cfg.max_pages:
        logging.warning("pdf_regions: %s has %d pages (more than %d); leaving it to the current converter", name, count, cfg.max_pages)
        return None
    # 0. 文字層の有無
    surveys = []
    try:
        for start in range(0, count, REOPEN_PAGES):
            with _Batch(path) as batch, lock:
                for i in range(start, min(count, start + REOPEN_PAGES)):
                    page = batch.doc[i]
                    try:
                        surveys.append(pdfpage.survey_page(page))
                    except Exception as error:
                        logging.debug("pdf_regions: %s page %d could not be surveyed (%s)", name, i + 1, error)
                        surveys.append(None)
                    finally:
                        page.close()
    except Exception as error:
        logging.warning("pdf_regions: cannot read %s (%s)", name, error)
        return None
    missing = [i + 1 for i, sv in enumerate(surveys) if sv is not None and _missing_text(sv)]
    if missing and (cfg.ocr_route or 2 * len(missing) > count):
        logging.warning("pdf_regions: %s: %d of %d pages have no text layer (%s); leaving it to the current converter",
                        name, len(missing), count, missing[:30])
        return None
    if missing:
        logging.warning("pdf_regions: %s: %d of %d pages have no text layer (%s); their text comes from Nemotron Parse only",
                        name, len(missing), count, missing[:30])
    read_seconds = time.time() - started

    if parser is None:
        parser = NemotronClient(cfg.parse_url, cfg.parse_model, cfg.timeout, cfg.retries, cfg.parse_concurrency,
                                deadline=deadline, lock_dir=cfg.lock_dir)
    if reader is None and cfg.figures == "describe":
        reader = FigureReader(VlmClient(cfg.triage_url, cfg.triage_model, cfg.timeout, cfg.retries, cfg.vlm_concurrency,
                                        deadline=deadline, lock_dir=cfg.lock_dir),
                              VlmClient(cfg.vlm_url, cfg.vlm_model, cfg.timeout, cfg.retries, cfg.vlm_concurrency,
                                        deadline=deadline, lock_dir=cfg.lock_dir))
    own_keys = doc_number_keys(Path(path).stem)
    tally = {"failed": 0}
    tally_lock = threading.Lock()
    give_up = threading.Event()

    def past_deadline() -> bool:
        return deadline is not None and time.time() >= deadline

    current = {"batch": None}

    def work(index: int):
        t = time.time()
        number = index + 1
        try:
            with lock:
                page = current["batch"].doc[index]
                try:
                    inp = _prepare(page, number, cfg, own_keys)
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass
        except Exception as error:
            logging.warning("pdf_regions: %s page %d could not be read (%s)", name, number, error)
            return None
        inp.note = (inp.note + f" read {time.time() - t:.2f}s").strip()
        blocks, error = None, None
        if inp.needs_parse and inp.png:
            if give_up.is_set():
                error = "skipped (too many pages failed)"
            elif past_deadline():
                error = "skipped (past the document deadline)"
            else:
                try:
                    blocks = parser.parse(inp.png, tag=f"{name} p{number}")
                except Exception as exc:
                    error = exc
                    logging.warning("pdf_regions: %s page %d: Nemotron Parse failed (%s); using the text layer for this page",
                                    name, number, exc)
            if blocks is None:
                with tally_lock:
                    tally["failed"] += 1
                    if tally["failed"] > cfg.max_failed_share * count:
                        give_up.set()   # 結果が None に決まった。残りのページは送らない
        parse_seconds = time.time() - t
        figure_reader = None if past_deadline() else reader
        try:
            if blocks is None and inp.needs_parse:
                out = fallback_page(inp, f"parse failed: {error}" if error else inp.note)
            else:
                out = assemble_page(inp, blocks, figure_reader, cfg.figures, own_keys, cfg.big_table_area, cfg.table_min_coverage)
        except Exception as exc:
            logging.warning("pdf_regions: %s page %d could not be assembled (%s); using the text layer for this page",
                            name, number, exc)
            out = fallback_page(inp, f"assemble failed: {exc}")
        out.stats.update({"seconds": round(time.time() - t, 2), "parse_seconds": round(parse_seconds, 2),
                          "parse_failed": bool(inp.needs_parse and blocks is None), "needs_parse": inp.needs_parse,
                          "note": inp.note})
        title = inp.title
        inp.png = None   # 画像と文字層はここで手放す（返すのは組み立てた行だけ）
        return out, title

    workers = cfg.parse_concurrency + 2 * cfg.vlm_concurrency
    results = []
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, count))) as pool:
            for start in range(0, count, REOPEN_PAGES):
                with _Batch(path) as batch:
                    current["batch"] = batch
                    results += [r for r in pool.map(work, range(start, min(count, start + REOPEN_PAGES))) if r is not None]
    except Exception as error:
        logging.warning("pdf_regions: cannot read %s (%s)", name, error)
        return None
    if not results:
        return None
    outputs = [out for out, _ in results]

    needed = sum(1 for o in outputs if o.stats.get("needs_parse"))
    failed = sum(1 for o in outputs if o.stats.get("parse_failed"))
    if needed and failed > cfg.max_failed_share * needed:
        logging.warning("pdf_regions: %s: Nemotron Parse failed on %d of %d pages; giving up this converter", name, failed, needed)
        return None

    state = DocState()
    pages = []
    for out in outputs:
        md = render_lines(out.lines, out.framed, state)
        out.stats["chars"] = len(md)
        if md.strip():
            pages.append((out.number, md))
    if not pages:
        return None
    # 文書の題名は、題名の欄が読めた最初のページのもの（後のページの欄には社名が入る様式がある）
    title = next((normalize_text(t).strip() for _, t in results if t), None) if cfg.title_line else None
    stats = {"file": name, "pages": count, "seconds": round(time.time() - started, 2),
             "read_seconds": round(read_seconds, 2), "parse_calls": getattr(parser, "calls", None),
             "parse_failed_pages": failed, "figures_mode": cfg.figures, "no_text_layer_pages": missing,
             "past_deadline": past_deadline(),
             "triage_calls": getattr(getattr(reader, "triage_client", None), "calls", 0) if reader else 0,
             "describe_calls": getattr(getattr(reader, "describe_client", None), "calls", 0) if reader else 0,
             "page_stats": [dict(o.stats, page=o.number) for o in outputs]}
    logging.info("pdf_regions: %s converted (%d pages, %d sent to Nemotron Parse, %d failed, %.1fs)",
                 name, count, needed, failed, stats["seconds"])
    return ConvertResult(pages=pages, title=title, stats=stats)
