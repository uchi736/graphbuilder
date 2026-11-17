"""ページの切れ目の印。PDF から変換した Markdown の各ページの先頭に 1 行で置き、分割のあとでページ番号に読み替えて
本文からは消す（create_chunks.py）。

変換器に依存しない置き場所。pdf_regions（Nemotron Parse の変換と、文字層だけの簡易変換 plain.py）がここから読む。
"""
import re

PAGE_MARK = "<!-- page:{} -->"
PAGE_MARK_PATTERN = re.compile(r"<!-- page:(\d+) -->\n?")
