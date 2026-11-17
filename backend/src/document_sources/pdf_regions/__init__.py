"""PDF を Markdown に変換する（regions）。AGPL の部品（PyMuPDF・pymupdf4llm）を使わない。

本文の文字は文字層（pypdfium2）から取り、ページの構造（領域・読み順・表・図の位置）は Nemotron Parse で取る。
枠付きの様式のページは本文の欄だけを切り出して渡す。見出しの段は番号の規則で決める。図は図中の文字と題だけを残す（VLM は呼ばない）。

モジュール（Graph Builder に依存しない部分と、Graph Builder 向けの組み立てを分けた）:
    pdfpage.py       pypdfium2 でページを読む（文字層・罫線・画像・字の向き）。pdfium に触るのはここだけ
    textlayer.py     文字層のデータ（文字の矩形）と、領域への割り当て・行・段落
    frame.py         罫線から枠・本文の欄・格子の様式を求める。題名の欄、格子の行
    nemotron.py      Nemotron Parse のクライアントと、出力のトークンを領域・表の格子に直す処理
    headings.py      文字の正規化と、見出しの段の決め方（number_headings.py の規則＋点区切り・付属のラベル）
    tablegrid.py     文字層に戻した表を、罫線の線分から欄を求めて GFM の表の格子にする
    assemble.py      1 ページの組み立て
    limits.py        接続先ごとの同時数の上限（プロセスの中とプロセスの間）と、応答しない接続先の遮断
    converter.py     PDF 全体の流れ（convert_pdf、RegionsConfig）。ここまでが Graph Builder に依存しない
    plain.py         文字層だけの簡易変換（regions が使えなかったときの戻り道）
    graph_builder.py Graph Builder 向け（環境変数の設定、ページの印を付けた 1 本の Document、失敗時は None）

依存は pypdfium2・Pillow・httpx と標準ライブラリ、同じ階層の number_headings.py・page_marks.py だけ。
"""
from .converter import ConvertResult, RegionsConfig, convert_pdf  # noqa: F401  （パッケージの入口。cpt-data-pipeline が使う）
