"""Graph Builder 向けの組み立て: 設定（環境変数）を読み、ページごとの Markdown を 1 本の Document にする。

返す形（文字層だけの簡易変換 load_pdf_as_markdown_plain も同じ形）:
  - list[Document]（PDF 全体で 1 つ）
  - metadata: markdown=True・markdown_strict=True・source。page と length は入れない（入れると分割の経路が変わる）
  - 各ページの先頭に <!-- page:N --> を 1 行。ページ同士は空行でつなぐ。中身の無いページは出さない
  - 文書の題名（枠の題名の欄）が読めれば、最初のページの先頭に「## 題名」（段は TITLE_LEVEL の注記を参照）
失敗したら None（呼び出し側の local_file が、文字層だけの簡易変換 load_pdf_as_markdown_plain に戻す）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..page_marks import PAGE_MARK
from .converter import ConvertResult, RegionsConfig, convert_pdf


def _shared_options() -> dict:
    """regions と簡易変換に共通の設定"""
    from src.shared.common_fn import get_value_from_env

    return {
        # OCR の経路（EDC_PDF_CONVERT）が有効なら、文字層の無いページを含む PDF はその経路に任せる
        "ocr_route": get_value_from_env("EDC_PDF_CONVERT", "false", "bool"),
        "title_line": get_value_from_env("PDF_REGIONS_TITLE", "true", "bool"),
    }


def config_from_env() -> RegionsConfig:
    from src.shared.common_fn import get_value_from_env

    return RegionsConfig(
        parse_url=get_value_from_env("PDF_REGIONS_PARSE_URL", "http://host.docker.internal:8022/v1"),
        parse_model=get_value_from_env("PDF_REGIONS_PARSE_MODEL", "nemotron-parse"),
        timeout=get_value_from_env("PDF_REGIONS_TIMEOUT", "180", "float"),
        retries=get_value_from_env("PDF_REGIONS_RETRIES", "2", "int"),
        parse_concurrency=get_value_from_env("PDF_REGIONS_PARSE_CONCURRENCY", "2", "int"),
        deadline=get_value_from_env("PDF_REGIONS_DEADLINE", "600", "float"),
        max_pages=get_value_from_env("PDF_REGIONS_MAX_PAGES", "300", "int"),
        lock_dir=get_value_from_env("PDF_REGIONS_LOCK_DIR", "") or None,
        **_shared_options(),
    )


# 文書の題名の見出しの段。章と同じ ## にする（# にすると以後のすべての見出しが題名の下に入り、章・節の層で
# 「番号の無い最上位の見出し」が題名だけになる。その結果、番号の見出しを含まない付属（別表1 の計測要領の用紙など）が
# 付属の節にならず「別表1」を解けない。名称の読み取りは、先頭のチャンクの最初の見出しであれば段を問わない）
TITLE_LEVEL = 2


def build_markdown(result: ConvertResult) -> str:
    parts = []
    for number, md in result.pages:
        if not parts and result.title:
            md = f"{'#' * TITLE_LEVEL} {result.title}\n\n{md}"
        parts.append(f"{PAGE_MARK.format(number)}\n{md}")
    return "\n\n".join(parts)


def _documents(result, file_path, converter: str):
    """変換の結果を Document 1 つの並びにする。結果が無いか中身が空なら None"""
    from langchain_core.documents import Document

    if result is None:
        return None
    markdown = build_markdown(result)
    if not markdown.strip():
        return None
    return [Document(page_content=markdown,
                     metadata={"source": str(file_path), "markdown": True, "markdown_strict": True,
                               "title": Path(file_path).stem, "pdf_converter": converter})]


def load_pdf_as_markdown_regions(file_path, config: RegionsConfig = None, parser=None):
    """PDF 全体を 1 本の Markdown（Document 1 つの並び）にして返す。失敗したら None"""
    result = convert_pdf(file_path, config or config_from_env(), parser=parser)
    docs = _documents(result, file_path, "regions")
    if docs:
        stats = result.stats
        logging.info("PDF converted to Markdown by regions: %d of %d pages from %s (%.1fs, Nemotron Parse %s calls, "
                     "%d pages on the text layer only)",
                     len(result.pages), stats.get("pages", 0), Path(file_path).name, stats.get("seconds", 0.0),
                     stats.get("parse_calls"), sum(1 for p in stats.get("page_stats", []) if p.get("fallback")))
    return docs


def load_pdf_as_markdown_plain(file_path):
    """regions が使えなかったときの戻り道。文字層（pypdfium2）だけで、同じ形（ページの印を付けた 1 本の Document）にする。
    Nemotron Parse は呼ばない。ページ数の上限は無い。失敗したら None（呼び出し側が次の経路に進む）"""
    from .plain import convert_pdf_plain

    return _documents(convert_pdf_plain(file_path, **_shared_options()), file_path, "plain")
