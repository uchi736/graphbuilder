# -*- coding: utf-8 -*-
"""PDF の新しい変換（src/document_sources/pdf_regions、PDF_CONVERTER=regions）の試験。GPU も HTTP も使わない。

Nemotron Parse の応答は、調査で保存した出力（tests/fixtures/pdf_regions/nemotron_raw.json。
tools/kg/parser/survey_20261109/page-compare/nemotron/ と nemotron_modes/bodycrop/ の raw_out を集めたもの。
VTR-T003D・HS-VTR-T001E は 2026-11-09 の手直し 2 で起動済みの :8022 から取った出力）で差し替える。
実物の PDF（prod_night1109/pdf の本番 6 文書、full56_night1109/pdf の HS 56 本）が無ければ、それを使う試験は飛ばす。

実行（バックエンドのイメージを使い捨てのコンテナで使う。動いているバックエンドには入らない）:
  cd ~/ai-stack/llm-graph-builder && docker run --rm --memory=4g --network none -e PYTHONDONTWRITEBYTECODE=1 \\
    -v "$PWD/backend:/code:ro" -v "$PWD/tools/kg/hs/runs/prod_night1109/pdf:/pdf:ro" -e PDF_REGIONS_TEST_PDF_DIR=/pdf \\
    -v "$PWD/tools/kg/hs/runs/full56_night1109/pdf:/pdf56:ro" -e PDF_REGIONS_TEST_FULL56_DIR=/pdf56 \\
    -w /code llm-graph-builder-backend python -m pytest -q -p no:cacheprovider tests/test_pdf_regions.py
"""
import io
import json
import os
import re
import subprocess
import sys
from collections import Counter

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from src.document_sources.pdf_regions import converter as CV  # noqa: E402
from src.document_sources.pdf_regions import headings as HD  # noqa: E402
from src.document_sources.pdf_regions import nemotron as NM  # noqa: E402
from src.document_sources.pdf_regions import vlm as VL  # noqa: E402
from src.document_sources.pdf_regions.assemble import PageInput, assemble_page  # noqa: E402
from src.document_sources.pdf_regions.frame import Frame  # noqa: E402
from src.document_sources.pdf_regions.graph_builder import build_markdown, load_pdf_as_markdown_regions  # noqa: E402
from src.document_sources.pdf_regions.nemotron import Block  # noqa: E402
from src.document_sources.pdf_regions.textlayer import TextLayer  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "pdf_regions", "nemotron_raw.json")
PDF_DIR = os.environ.get("PDF_REGIONS_TEST_PDF_DIR") or os.path.join(os.path.dirname(ROOT), "tools", "kg", "hs", "runs",
                                                                         "prod_night1109", "pdf")
TOKENIZER = os.path.join(ROOT, "tokenizers", "ruri-v3-310m", "tokenizer.json")


FULL56_DIR = os.environ.get("PDF_REGIONS_TEST_FULL56_DIR") or os.path.join(os.path.dirname(ROOT), "tools", "kg", "hs", "runs",
                                                                         "full56_night1109", "pdf")


def _pdf(stem):
    path = os.path.join(PDF_DIR, stem + ".pdf")
    if not os.path.isfile(path):
        pytest.skip(f"実物の PDF が無い: {path}")
    return path


def _pdf56(stem):
    path = os.path.join(FULL56_DIR, stem + ".pdf")
    if not os.path.isfile(path):
        pytest.skip(f"実物の PDF が無い: {path}")
    return path


class FakeParser:
    """保存した Nemotron Parse の出力を返す。ページ全体の画像（1241×1755、横長は 1755×1241）か、本文の欄の切り出しかを
    画像の大きさで見分ける"""

    def __init__(self, fail_pages=(), fail_all=False):
        with open(FIXTURE, encoding="utf-8") as f:
            self.raw = json.load(f)["pages"]
        self.fail_pages = set(fail_pages)
        self.fail_all = fail_all
        self.calls = 0
        self.modes = {}

    def parse(self, png, tag=""):
        from PIL import Image
        self.calls += 1
        name, page = tag.rsplit(" p", 1)
        stem, n = name[:-4], int(page)
        if self.fail_all or n in self.fail_pages:
            raise NM.ParseError("fake failure")
        w, h = Image.open(io.BytesIO(png)).size
        mode = "full" if (w, h) in ((1241, 1755), (1755, 1241)) else "body"
        self.modes[n] = mode
        return NM.parse_blocks(self.raw[f"{stem} p{n} {mode}"])


def _convert(stem, figures="text", parser=None, reader=None, path=None):
    parser = parser or FakeParser()
    res = CV.convert_pdf(path or _pdf(stem), CV.RegionsConfig(figures=figures), parser=parser, reader=reader)
    return res, parser


def _pages(markdown):
    out = {}
    for m in re.finditer(r"<!-- page:(\d+) -->\n(.*?)(?=\n\n<!-- page:|\Z)", markdown, re.S):
        out[int(m.group(1))] = m.group(2)
    return out


def _bag(text):
    return Counter(re.sub(r"\s+", "", HD.normalize_text(text)))


# ---- 枠の検出（実物の PDF） ----
@pytest.mark.parametrize("stem,kinds,body", [
    ("VTR-T004C", ["body", "body", "none", "none", "grid"], (76.1, 70.6, 580.3, 737.8)),
    ("VTR-T005B", ["body", "body", "none", "none", "none", "grid"], (76.1, 70.9, 580.3, 737.5)),
    ("HS-VTR-T002F", ["body", "body", "body", "none", "none", "none", "none", "grid"], (77.8, 82.9, 578.8, 712.9)),
])
def test_frame_detection_on_real_pdfs(stem, kinds, body):
    from src.document_sources.pdf_regions import frame as FR, pdfpage
    doc = pdfpage.open_document(_pdf(stem))
    try:
        got = []
        for i in range(len(doc)):
            page = doc[i]
            W, H = pdfpage.page_size(page)
            hs, vs = pdfpage.line_segments(page)
            f = FR.detect_frame(hs, vs, W, H)
            got.append(f.kind)
            if i == 0:
                # 本文の欄は、以前の枠の処理（PyMuPDF の表の検出。2026-11-10 に外した）で測った値と 3pt 以内で一致する
                assert max(abs(a - b) for a, b in zip(f.body, body)) <= 3.0
                layer = pdfpage.text_layer(page)
                assert FR.frame_title(layer, f)
        assert got == kinds
    finally:
        doc.close()


def test_virtual_frame_when_frame_lines_are_images():
    """枠の線が画像で描かれた様式（罫線が 0 本）は、枠の語（配布先・承認・来歴…）の位置から本文の範囲を決める"""
    from src.document_sources.pdf_regions import frame as FR, pdfpage
    full56 = os.environ.get("PDF_REGIONS_TEST_FULL56_DIR") or os.path.join(os.path.dirname(ROOT), "tools", "kg", "hs", "runs",
                                                                           "full56_night1109", "pdf")
    path = os.path.join(full56, "HS-RU-003H.pdf")
    if not os.path.isfile(path):
        pytest.skip(f"実物の PDF が無い: {path}")
    doc = pdfpage.open_document(path)
    try:
        page = doc[0]
        layer = pdfpage.text_layer(page)
        hs, vs = pdfpage.line_segments(page)
        f = FR.detect_page_frame(layer, hs, vs)
        assert f.kind == "body" and f.virtual
        assert "品質要求基準" in FR.frame_title(layer, f)
        bx0, by0, bx1, by1 = f.body
        body = "".join(layer.chars[k].c for k in layer.ink_indices(f.body))
        assert "配布先" not in body and "承認" not in body
    finally:
        doc.close()


# ---- 文書全体（保存した Nemotron Parse の出力で差し替え） ----
def test_vtr_t005b_end_to_end_markdown():
    res, parser = _convert("VTR-T005B")
    assert res is not None
    md = build_markdown(res)
    pages = _pages(md)
    assert sorted(pages) == [1, 2, 3, 4, 5, 6]
    assert parser.modes == {1: "body", 2: "body", 3: "full", 4: "full", 5: "full"}   # 格子の 6 ページ目は送らない
    lines = md.split("\n")
    # 付属のラベル（柱）が題の行とまとまって ## になる
    assert "## 別表2 過給機性能試験及び性能換算法" in lines
    assert "## 別表1 コンプレッサ性能計測要領" in lines
    assert any(ln.startswith("## 添付資料 ") for ln in lines)
    # 題名の欄が先頭に、章・節・号の段
    assert pages[1].startswith("## TPL.TPS形過給機の初号機に関する コンプレッサ単独性能確認試験要領及び判定基準\n")
    for h in ("## 1. 適用", "## 2. 単独性能確認試験要領", "### 2-5 コンプレッサ性能計測時における運転条件", "##### 1) TPS",
              "## 7. 基準来歴"):
        assert h in lines
    # 番号で始まる題（3-1.サージ誤差計算）は見出しに戻る。付属の中なので 1 段下げる（4 ページ目の「1. テスト方法」も ###）
    assert any(re.match(r"^#### 3-1 サージ誤差計算", ln) for ln in lines)
    assert "### 1. テスト方法" in lines and "### 初号機 合否判定方法" in lines
    assert "## 協議・通知票" in lines
    # 表は GFM（Nemotron Parse は正しい 3 列）
    assert "| 試験回転速度 | 適用 | 方式 |" in lines
    # 枠の外の管理欄は入らない
    for page in (1, 2):
        for word in ("配布先", "改正回数", "承認", "回品管", "改正番号"):
            assert word not in pages[page], (page, word)
    # 囲み・ページ印の取り違え・行頭の # の本文が無い
    assert "```" not in md and "`" not in md
    assert md.count("<!--") == len(pages)


def test_grid_pages_keep_document_layer_rules():
    """格子の様式のページは、文書層の規則（協議・通知票の parse_notice、発行目録の parse_catalog）が今の変換と同じく読める形にする"""
    from src import document_layer_rules as R
    cfg = R.load_config()
    res, _ = _convert("VTR-T005B")
    notice = R.parse_notice(_pages(build_markdown(res))[6], cfg)
    assert notice == {"kind": "改訂", "number": "HS-VTR-T005", "old_rev": "A", "new_rev": "B", "date": "04.03.03"}
    res = CV.convert_pdf(_pdf("VTR-T_発行目録"), CV.RegionsConfig(figures="off"), parser=FakeParser(fail_all=True))
    md = build_markdown(res)
    assert md.split("\n")[1] == "## 発行目録"
    rows = R.parse_catalog(md, cfg)
    assert [r["number"] for r in rows][:3] == ["HS-VTR-T001", "HS-VTR-T002", "HS-VTR-T003"] and len(rows) == 5


def test_body_pages_keep_text_layer():
    """本文の欄のページは、欄の中の文字層の字の 99% 以上が出力に残る（文字の多重集合で数える）"""
    from src.document_sources.pdf_regions import frame as FR, pdfpage
    for stem, framed in (("VTR-T005B", (1, 2)), ("VTR-T004C", (1, 2)), ("HS-VTR-T002F", (1, 2, 3))):
        res, _ = _convert(stem)
        pages = _pages(build_markdown(res))
        doc = pdfpage.open_document(_pdf(stem))
        try:
            for n in framed:
                page = doc[n - 1]
                W, H = pdfpage.page_size(page)
                f = FR.detect_frame(*pdfpage.line_segments(page), W, H)
                ref = _bag(pdfpage.bounded_text(page, f.body))
                out = _bag(pages[n])
                kept = sum(min(c, out[k]) for k, c in ref.items()) / sum(ref.values())
                assert kept >= 0.99, (stem, n, kept)
        finally:
            doc.close()


def test_chunks_and_section_layer_resolve_annex(monkeypatch):
    """変換 → 既存の分割（CreateChunksofDocument）→ 章・節の層の目録。別表2 が付属の節になり「別表2」を解ける"""
    if not os.path.isfile(TOKENIZER):
        pytest.skip("tokenizer が無い")
    monkeypatch.setenv("CHUNK_TOKENIZER", TOKENIZER)
    import src.create_chunks as cc
    from src.create_chunks import CreateChunksofDocument
    # Markdown の経路はトークン数の分割器を使わないが、最初に作られる（gpt2 の語彙を取りに外へ出る）ので差し替える
    monkeypatch.setattr(cc, "TokenTextSplitter", lambda **kw: None)
    from src import document_layer_rules as R
    from src import section_layer as SL

    fname = "VTR-T005B.pdf"
    docs = load_pdf_as_markdown_regions(_pdf("VTR-T005B"), CV.RegionsConfig(figures="text"), parser=FakeParser())
    assert len(docs) == 1
    meta = docs[0].metadata
    assert meta["markdown"] is True and meta["markdown_strict"] is True and meta["source"].endswith(fname)
    assert "page" not in meta and "length" not in meta
    docs[0].metadata["title"] = "VTR-T005B"
    chunks = CreateChunksofDocument(docs, None).split_file_into_chunks(512, 50, "t@example.com")
    assert chunks and all("<!-- page:" not in c.page_content for c in chunks)
    assert {c.metadata.get("page_number") for c in chunks} >= {1, 2, 3, 4, 5, 6}
    rows = [{"id": f"c{i}", "fileName": fname, "position": i + 1, "section": c.metadata.get("section"),
             "section_path": json.dumps(c.metadata.get("section_path") or c.metadata.get("_path") or [], ensure_ascii=False),
             "text": c.page_content, "page_number": c.metadata.get("page_number")} for i, c in enumerate(chunks)]
    secs, _ = SL.sections_from_catalogue(rows, R.load_config())
    idx = SL.SectionIndex(secs)
    assert idx.find(fname, {"form": "付属", "label": "別表2", "number": None})
    assert idx.find(fname, {"form": "付属", "label": "別表1", "number": None})   # 番号の見出しを含まない付属も節になる
    from src.chunk_reference_rules import Catalogue
    assert any("判定基準" in n for n in Catalogue(rows, ()).names.get(fname, ()))   # 題名の見出しから名称が読める
    chapters = {s["number"] for s in secs.values() if s.get("form") == "章"}
    assert {"1", "2", "3", "4", "5", "6", "7"} <= chapters
    # 4 ページ目の節は別表2 の中にある（付属の中の「1. テスト方法」が本文の章 1 と混ざらない）
    annex_id = idx.find(fname, {"form": "付属", "label": "別表2", "number": None})
    annex_id = annex_id[0] if isinstance(annex_id, (list, tuple)) else annex_id
    inner = [s for s in secs.values() if s.get("title", "").startswith("テスト方法")]
    assert inner and all(s.get("parent") == annex_id for s in inner)
    paths = [c.metadata.get("section_path") or c.metadata.get("_path") or [] for c in chunks if c.metadata.get("page_number") == 4]
    assert any(any(x.startswith("別表2") for x in p) for p in paths)


def test_page_level_failure_uses_text_layer():
    res, _ = _convert("VTR-T005B", parser=FakeParser(fail_pages={2}))
    assert res is not None
    pages = _pages(build_markdown(res))
    stats = {p["page"]: p for p in res.stats["page_stats"]}
    assert stats[2]["fallback"] and stats[2]["parse_failed"]
    assert "## 3. 性能管理判定基準" in pages[2].split("\n")   # 文字層の行だけでも章の見出しは立つ
    assert "配布先" not in pages[2]                          # 枠の外は入らない


def test_failure_returns_none(tmp_path):
    res, _ = _convert("VTR-T005B", parser=FakeParser(fail_all=True))
    assert res is None
    # 4 ページ中 3 ページ失敗（半分を超える）でも None
    res, _ = _convert("VTR-T004C", parser=FakeParser(fail_pages={1, 2, 3}))
    assert res is None
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    assert CV.convert_pdf(str(bad), CV.RegionsConfig(figures="off"), parser=FakeParser()) is None
    assert load_pdf_as_markdown_regions(str(tmp_path / "missing.pdf"), CV.RegionsConfig(), parser=FakeParser()) is None


# ---- 組み立ての規則（合成のページ） ----
W, H = 600.0, 800.0


def _page(lines, blocks_kind="none", body=None, png=None):
    layer = TextLayer.from_lines(W, H, lines)
    frame = Frame("body", outer=(10, 10, 590, 790), body=body) if body else Frame("none")
    return PageInput(number=1, layer=layer, frame=frame, png=png, view=body or (0.0, 0.0, W, H))


def _norm(rect):
    x0, y0, x1, y1 = rect
    return (x0 / W, y0 / H, x1 / W, y1 / H)


def _render(out):
    return HD.render_lines(out.lines, out.framed, HD.DocState())


def test_annex_header_becomes_heading_with_title():
    page = _page([("別 表 1", 80, 40, 14), ("HS-VTR-T005 B", 420, 40, 10), ("コンプレッサ性能計測要領", 200, 80, 14),
                  ("下図に示す計測点を確認する。この行は本文である。", 80, 150, 10)])
    blocks = [Block("Page-header", _norm((78, 38, 140, 56)), "**別表1**"),
              Block("Page-header", _norm((418, 38, 520, 52)), "HS-VTR-T005 B"),
              Block("Section-header", _norm((198, 78, 540, 96)), "コンプレッサ性能計測要領"),
              Block("Text", _norm((78, 148, 560, 162)), "下図に示す…")]
    out = assemble_page(page, blocks, None, "text", own_keys=HD.doc_number_keys("VTR-T005B"))
    md = _render(out)
    assert md.split("\n")[0] == "## 別表1 コンプレッサ性能計測要領"
    assert "HS-VTR-T005" not in md


def test_foreign_annex_header_is_not_a_heading():
    assert HD.header_annex("QC-KAI-001 別紙3", HD.doc_number_keys("HS-RU-001C")) is None
    # 語と番号の間のダッシュは残す（2026-11-09 の手直し 2。章・節の層の annex_key は「別紙-1」も「別紙1」と読む）
    assert HD.header_annex("HS-VTR-T003C 別紙-1", HD.doc_number_keys("VTR-T003D")) == "別紙-1"
    assert HD.header_annex("HSVTR-T002F添付資料(V-00862による)", HD.doc_number_keys("HS-VTR-T002F")) == "添付資料"
    assert HD.header_annex("HS-VTR-004 C", HD.doc_number_keys("VTR-T004C")) is None
    assert HD.header_annex("別 表 １") == "別表1"


def test_heading_levels_dotted_and_annex():
    st = HD.DocState()
    texts = ["3.性能換算法", "3.1 空気量", "3.1.2 吸入状態空気量", "3.1.2.1 補正", "1.5倍以上とする", "表1 計測点",
             "3−5 潤滑油供給条件", "(1) 羽根", "2) TPL", "a) 本体", "1.試運転方法及び性能換算方法・・・・・・HS-VTR-T005による",
             "1-1項と別表の摘要欄に追記", "# 本文の行頭のシャープ", "| 本文の縦棒", "[参考]",
             "別 表 ２ 過給機性能試験", "1.テスト方法", "1-1 試験回転速度", "3.2 圧力比"]
    md = HD.render_lines([HD.Line(HD.normalize_text(t)) for t in texts], framed=False, state=st).split("\n")
    assert md[:4] == ["## 3. 性能換算法", "### 3.1 空気量", "#### 3.1.2 吸入状態空気量", "##### 3.1.2.1 補正"]
    assert md[4] == "1.5倍以上とする"
    assert md[5] == "### 表1 計測点"
    assert md[6] == "### 3-5 潤滑油供給条件"      # 異体のハイフンは半角に
    assert md[7:10] == ["#### (1) 羽根", "##### 2) TPL", "###### a) 本体"]
    assert not md[10].startswith("#") and not md[11].startswith("#")
    assert md[12] == "\\# 本文の行頭のシャープ" and md[13] == "\\| 本文の縦棒" and md[14] == "\\[参考]"
    # 付属の見出しは ##、その中の番号の見出しは 1 段下げる
    assert md[15:] == ["## 別表2 過給機性能試験", "### 1. テスト方法", "#### 1-1 試験回転速度", "#### 3.2 圧力比"]
    # 協議・通知票などの最上位の見出しで付属の範囲は終わる
    st2 = HD.DocState(annex=True)
    out = HD.render_lines([HD.Line("## 協議・通知票", kind="final", root=True), HD.Line("1. 適用")], framed=False, state=st2)
    assert out.split("\n")[-1] == "## 1. 適用"
    # 点区切りは、章が無く見出しの領域でもない行では段にしない
    assert HD.render_lines([HD.Line("4.2 圧力比")], framed=False, state=HD.DocState()) == "4.2 圧力比"
    assert HD.render_lines([HD.Line("4.2 圧力比", hint=True)], framed=False, state=HD.DocState()) == "### 4.2 圧力比"


def test_normalize_text_rules():
    assert HD.normalize_text("ＨＳ－ＶＴＲ－Ｔ００５　ｴﾝｼﾞﾝ　３−５項") == "HS-VTR-T005 エンジン 3-5項"
    assert HD.normalize_text("文末。次！？＃｜") == "文末。次！？＃｜"
    assert "<!--" not in HD.normalize_text("<!-- x -->")


def _table_page(table_raw, tl_lines, rect, cls="Table"):
    page = _page(tl_lines)
    blocks = [Block(cls, _norm(rect), table_raw)]
    return assemble_page(page, blocks, None, "text")


def test_table_to_gfm_and_seventy_percent_rule():
    tl = [("項目 値 単位", 100, 100, 10), ("温度 130 ℃", 100, 114, 10), ("圧力 0.5 MPa", 100, 128, 10)]
    good = "\\begin{tabular}{ccc}\n項目 & 値 & 単位\\\\\n温度 & 130 & ℃\\\\\n圧力 & 0.5 & MPa\\\\\n\\end{tabular}"
    md = _render(_table_page(good, tl, (95, 95, 300, 142)))
    assert md.split("\n")[:3] == ["| 項目 | 値 | 単位 |", "|---|---|---|", "| 温度 | 130 | ℃ |"]
    # Nemotron Parse の字数が文字層の 7 割未満 → 文字層の行に戻す
    short = "\\begin{tabular}{cc}\n項目 & 値\\\\\n\\end{tabular}"
    out = _table_page(short, tl, (95, 95, 300, 142))
    md = _render(out)
    assert "|" not in md and "温度 130 ℃" in md and "圧力 0.5 MPa" in md
    assert out.stats["tables_to_text"] == 1


def test_big_table_is_read_as_body_text():
    tl = [("1. 適用", 60, 60, 10), ("この基準は試験に適用する。", 60, 80, 10), ("2. 判定", 60, 100, 10)]
    raw = "\\begin{tabular}{c}\n1. 適用\\\\\n\\end{tabular}"
    out = _table_page(raw, tl, (20, 20, 580, 780))       # 送った画像の 9 割を占める表
    md = _render(out)
    assert "## 1. 適用" in md.split("\n") and "## 2. 判定" in md.split("\n") and "|" not in md


def test_latex_spans_are_repeated():
    raw = ("\\begin{tabular}{ccc}\n\\multicolumn{2}{c}{\\thead{区分}} & 値\\\\\n\\multirow{2}{*}{A} & a1 & 1\\\\\n"
           " & a2 & 2\\\\\n\\end{tabular}")
    assert NM.table_grid(raw) == [["区分", "区分", "値"], ["A", "a1", "1"], ["A", "a2", "2"]]
    assert NM.table_grid("<table><tr><th colspan='2'>x|y</th></tr><tr><td>1</td><td>2</td></tr></table>") == \
        [["x|y", "x|y"], ["1", "2"]]


class FakeVlm:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat(self, prompt, png, max_tokens=700):
        self.calls += 1
        return self.reply


def _figure_page():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (600, 800), "white").save(buf, format="PNG")
    page = _page([("回転数 rps", 120, 300, 9), ("圧力比", 100, 200, 9), ("図2 ABBマップ", 200, 420, 10)], png=buf.getvalue())
    blocks = [Block("Chart", _norm((90, 180, 400, 400)), "<table>…</table>"),
              Block("Caption", _norm((195, 418, 400, 432)), "図2 ABBマップ")]
    return page, blocks


def test_figure_description_strips_fences_and_numbers():
    page, blocks = _figure_page()
    triage = FakeVlm('{"種類": "グラフ", "扱い": "図として説明する", "理由": "曲線"}')
    describe = FakeVlm("```markdown\n1. 横軸は空気量、縦軸は圧力比である。\n2. 回転数ごとの曲線がある。\n```")
    out = assemble_page(page, blocks, VL.FigureReader(triage, describe), "describe")
    md = _render(out)
    assert "```" not in md
    assert "（図の説明・自動生成）横軸は空気量、縦軸は圧力比である。回転数ごとの曲線がある。" in md.split("\n")
    assert any(ln.startswith("図中の文字: ") and "圧力比" in ln for ln in md.split("\n"))
    assert "図2 ABBマップ" in md.split("\n")
    assert triage.calls == 1 and describe.calls == 1
    # 写真は題だけ（説明も図中の文字も出さない）
    page, blocks = _figure_page()
    out = assemble_page(page, blocks, VL.FigureReader(FakeVlm('{"種類": "写真"}'), FakeVlm("x")), "describe")
    md = _render(out)
    assert "図の説明" not in md and "図中の文字" not in md and "図2 ABBマップ" in md
    # text: VLM を呼ばない。off: 図は何も出さない
    page, blocks = _figure_page()
    md = _render(assemble_page(page, blocks, None, "text"))
    assert "図中の文字: " in md and "図の説明" not in md
    page, blocks = _figure_page()
    md = _render(assemble_page(page, blocks, None, "off"))
    assert "図中の文字" not in md


def test_clean_vlm_text():
    assert VL.clean_vlm_text("```\n# 概要\n- **軸**: 空気量\n(1) 傾向は右上がり\n```") == "概要軸: 空気量傾向は右上がり"
    assert VL.clean_vlm_text("3-1 サージ線を示す。") == "サージ線を示す。"
    assert VL.clean_vlm_text("300 rpm の曲線") == "300 rpm の曲線"
    assert VL.clean_vlm_text("縦軸に$\\pi^c$、横軸に流量$V$(単位:$\\text{m}^3/\\text{s}$)") == "縦軸にπ^c、横軸に流量V(単位:m^3/s)"


def test_table_with_invented_values_is_treated_as_figure():
    tl = [("回転数 rps", 120, 300, 9), ("圧力比 空気量", 100, 200, 9)]
    raw = "\\begin{tabular}{cc}\n1.0 & 2.5\\\\\n1.2 & 2.9\\\\\n1.4 & 3.3\\\\\n1.6 & 3.8\\\\\n\\end{tabular}"
    out = _table_page(raw, tl, (90, 180, 400, 400))
    md = _render(out)
    assert "|" not in md and "図中の文字: " in md and out.stats["figures"] == 1


# ---- Nemotron Parse のクライアント（HTTP の差し替え） ----
def test_nemotron_request_shape_and_retry(monkeypatch):
    client = NM.NemotronClient("http://parse:8000/v1", retries=1, concurrency=1)
    sent = []
    replies = [RuntimeError("connection refused"),
               {"choices": [{"message": {"content": "<x_0.1><y_0.1>本文<x_0.5><y_0.2><class_Text>"}, "finish_reason": "stop"}]}]

    def post(payload, tag):
        sent.append(payload)
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(client, "_post", post)
    monkeypatch.setattr(NM.time, "sleep", lambda s: None)
    blocks = client.parse(b"\x89PNG", tag="x p1")
    assert [(b.cls, b.text) for b in blocks] == [("Text", "本文")]
    p = sent[0]
    assert p["model"] == "nemotron-parse" and p["skip_special_tokens"] is False and p["max_tokens"] == 8744
    assert p["messages"][0]["content"][0] == {"type": "text", "text": NM.PROMPT}
    assert p["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert client.calls == 2
    # 途中で切れた生成は送り直さずに失敗
    client2 = NM.NemotronClient("http://parse:8000/v1", retries=2)
    monkeypatch.setattr(client2, "_post", lambda payload, tag: {"choices": [{"message": {"content": "<x_0.1>"}, "finish_reason": "length"}]})
    with pytest.raises(NM.ParseError):
        client2.parse(b"x")
    assert client2.calls == 1


def test_nemotron_http_transport():
    import httpx
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "<x_0.1><y_0.1>**1.** **適用**<x_0.3><y_0.12><class_Section-header>"},
                                                      "finish_reason": "stop"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = NM.NemotronClient("http://parse:8000/v1", client=http)
        blocks = client.parse(b"png")
    assert seen["url"] == "http://parse:8000/v1/chat/completions"
    assert seen["body"]["temperature"] == 0.2 and seen["body"]["repetition_penalty"] == 1.05
    assert blocks[0].cls == "Section-header" and NM.plain_text(blocks[0].text) == "1. 適用"


# ---- Graph Builder への組み込み ----
def test_local_file_selects_converter(monkeypatch, tmp_path):
    """変換器は regions だけ。regions が None・例外なら文字層だけの簡易変換に戻り、それも None・例外なら従来の読み込み
    （EDC の OCR、無効ならページごとの読み込み PdfPageLoader）に進む。以前の値 pymupdf4llm は regions として扱う"""
    from langchain_core.documents import Document
    from src.document_sources import local_file
    from src.document_sources.pdf_regions import graph_builder

    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setenv("PDF_CHUNK_STRATEGY", "markdown")
    monkeypatch.setenv("MARKDOWN_CHUNKING", "true")
    monkeypatch.setenv("EDC_PDF_CONVERT", "false")
    calls = []
    regions_doc = Document(page_content="<!-- page:1 -->\nR", metadata={"markdown": True, "markdown_strict": True})
    plain_doc = Document(page_content="<!-- page:1 -->\nP", metadata={"markdown": True, "markdown_strict": True})
    monkeypatch.setattr(graph_builder, "load_pdf_as_markdown_regions", lambda p: calls.append("regions") or [regions_doc])
    monkeypatch.setattr(graph_builder, "load_pdf_as_markdown_plain", lambda p: calls.append("plain") or [plain_doc])
    # 既定（未設定）は regions
    monkeypatch.delenv("PDF_CONVERTER", raising=False)
    loader, raw = local_file.load_document_content(str(pdf))
    assert loader.load() == [regions_doc] and calls == ["regions"] and raw is True
    # 以前の値 pymupdf4llm・知らない値は regions として扱う
    for value in ("pymupdf4llm", "bogus"):
        calls.clear()
        monkeypatch.setenv("PDF_CONVERTER", value)
        loader, _ = local_file.load_document_content(str(pdf))
        assert loader.load() == [regions_doc] and calls == ["regions"]
    monkeypatch.setenv("PDF_CONVERTER", "regions")
    # regions が None なら簡易変換に戻る
    calls.clear()
    monkeypatch.setattr(graph_builder, "load_pdf_as_markdown_regions", lambda p: calls.append("regions") or None)
    loader, raw = local_file.load_document_content(str(pdf))
    assert loader.load() == [plain_doc] and calls == ["regions", "plain"] and raw is True

    # regions が例外でも簡易変換に戻る
    def boom(p):
        calls.append("regions")
        raise RuntimeError("regions down")
    calls.clear()
    monkeypatch.setattr(graph_builder, "load_pdf_as_markdown_regions", boom)
    loader, _ = local_file.load_document_content(str(pdf))
    assert loader.load() == [plain_doc] and calls == ["regions", "plain"]

    # 簡易変換も None（または例外）なら、従来の読み込み（ページごと）に進む
    def plain_boom(p):
        calls.append("plain")
        raise RuntimeError("plain down")
    for plain in (lambda p: calls.append("plain") or None, plain_boom):
        calls.clear()
        monkeypatch.setattr(graph_builder, "load_pdf_as_markdown_plain", plain)
        loader, raw = local_file.load_document_content(str(pdf))
        assert isinstance(loader, local_file.PdfPageLoader) and raw is False and calls == ["regions", "plain"]


def test_page_mark_is_shared():
    from src.document_sources import page_marks
    import src.create_chunks as cc
    src_text = open(cc.__file__, encoding="utf-8").read()
    assert "from src.document_sources.page_marks import PAGE_MARK_PATTERN" in src_text
    assert page_marks.PAGE_MARK.format(3) == "<!-- page:3 -->"


def test_new_code_does_not_load_agpl_parts():
    """PDF の経路は PyMuPDF・pymupdf4llm を読み込まない（ソースの字面と、実際に読み込まれたモジュールの両方で確かめる）。
    2026-11-10 に今の変換（pdf_markdown.py・frame_pages.py）を外し、requirements.txt からも外した"""
    pkg = os.path.join(ROOT, "src", "document_sources", "pdf_regions")
    banned = re.compile(r"^\s*(import|from)\s+(fitz|pymupdf|pymupdf4llm)\b|PyMuPDFLoader|pdf_markdown\s+import|import\s+.*pdf_markdown"
                        r"|frame_pages\s+import|import\s+.*frame_pages", re.M)
    sources = [os.path.join(pkg, n) for n in os.listdir(pkg) if n.endswith(".py")]
    sources += [os.path.join(ROOT, "src", "document_sources", n) for n in ("local_file.py", "gcs_bucket.py", "s3_bucket.py")]
    for path in sources:
        with open(path, encoding="utf-8") as f:
            assert not banned.search(f.read()), path
    for name in ("pdf_markdown.py", "frame_pages.py"):
        assert not os.path.exists(os.path.join(ROOT, "src", "document_sources", name)), name
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        assert not re.search(r"(?im)^\s*(pymupdf|pymupdf4llm|pymupdf-layout|pymupdf_layout)\b", f.read())
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from src.document_sources.pdf_regions import graph_builder, converter, plain\n"
            "from src.document_sources.pdf_regions.textlayer import TextLayer\n"
            "from src.document_sources import local_file\n"
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('fitz', 'pymupdf', 'pymupdf4llm')))\n") % ROOT
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_settings_defaults_use_regions(monkeypatch):
    """変換器の既定は regions（2026-11-10 に pymupdf4llm を外した）。図の扱いの既定は text（2026-11-09 の手直し 2: 図の説明は
    本番 6 文書の 6 個すべてに読み誤りがあった）。describe は選べる。設定の一覧・example.env・コードの既定がそろっている"""
    from src import settings_overview as so
    from src.document_sources.pdf_regions import graph_builder as GB
    env = {n: d for n, _g, _m, d in so.OVERVIEW_ENV}
    assert env["PDF_CONVERTER"] == "regions"
    texts = {n: m for n, _g, m, _d in so.OVERVIEW_ENV}
    assert "pymupdf4llm で変換" not in texts["PDF_REGIONS_MAX_PAGES"] and "簡易変換" in texts["PDF_REGIONS_MAX_PAGES"]
    with open(os.path.join(ROOT, "example.env"), encoding="utf-8") as f:
        lines = f.read().split("\n")
    assert '# PDF_CONVERTER="regions"' in lines and '# PDF_CONVERTER="pymupdf4llm"' not in lines
    assert env["PDF_REGIONS_FIGURES"] == "text"
    assert env["PDF_REGIONS_PARSE_CONCURRENCY"] == "2" and env["PDF_REGIONS_VLM_CONCURRENCY"] == "2"
    cfg = CV.RegionsConfig()
    assert (cfg.parse_concurrency, cfg.vlm_concurrency, cfg.figures) == (2, 2, "text")
    assert CV.RegionsConfig(figures="bogus").normalized().figures == "text"
    assert CV.RegionsConfig(figures="describe").normalized().figures == "describe"
    monkeypatch.delenv("PDF_REGIONS_FIGURES", raising=False)
    assert GB.config_from_env().figures == "text"
    monkeypatch.setenv("PDF_REGIONS_FIGURES", "describe")
    assert GB.config_from_env().figures == "describe"
    with open(os.path.join(ROOT, "example.env"), encoding="utf-8") as f:
        assert '# PDF_REGIONS_FIGURES="text"' in f.read().split("\n")


# ---- 反証役のレビューの指摘に対する試験（2026-11-09 の手直し） ----
def test_history_records_are_not_chapters():
    """来歴の章の中の記録（1. S62.03.30 新規作成 担当 …）は章にしない。日付の点区切り（7.03.07.07）も見出しにしない"""
    texts = ["9. 備考", "1)本品の溶接補修は一切許容しない。", "10. 基準来歴", "1. S62.03.30 新規作成 担当 品管部 大槻",
             "HS-RH-004B を基に分離独立した。", "2. S62.04.28 一部見直し 担当 品管部 大槻", "7.03.07.07 一部見直し 担当 回品管G",
             "1) 適用[対象部品]にRU140追加", "1. 新規作成 ’04.03.26"]
    md = HD.render_lines([HD.Line(HD.normalize_text(t)) for t in texts], framed=True, state=HD.DocState()).split("\n")
    assert [ln for ln in md if ln.startswith("#")] == ["## 9. 備考", "## 10. 基準来歴"]
    assert "1. S62.03.30 新規作成 担当 品管部 大槻" in md and "7.03.07.07 一部見直し 担当 回品管G" in md
    # 来歴の範囲は、来歴の次の番号の章・見出しの領域の章の行（補足のページ）・最上位の見出しで終わる
    st = HD.DocState()
    lines = [HD.Line("7. 基準来歴"), HD.Line("1. 新規作成 担当 回品管 堀川"), HD.Line("1.型式・スペック及び抜取頻度", hint=True),
             HD.Line("2.ガス流路部切断検査要領", hint=True)]
    md = HD.render_lines(lines, framed=True, state=st).split("\n")
    assert md == ["## 7. 基準来歴", "1. 新規作成 担当 回品管 堀川", "## 1. 型式・スペック及び抜取頻度", "## 2. ガス流路部切断検査要領"]
    st = HD.DocState()
    out = HD.render_lines([HD.Line("6. 基準来歴"), HD.Line("1. 適用範囲の見直し"), HD.Line("## 協議・通知票", kind="final", root=True),
                           HD.Line("1. 適用")], framed=True, state=st).split("\n")
    assert out == ["## 6. 基準来歴", "1. 適用範囲の見直し", "## 協議・通知票", "## 1. 適用"]
    # 章の番号が戻る行（章 3 の中の「1．材料試験…の実施」）は章にしない。0 で始まる点区切りの章 0 も見出しにしない
    out = HD.render_lines([HD.Line(t) for t in ("3. 試験、検査方法", "1．材料試験、検査の実施", "4. 製品表示", "0.7 D Ⅰ (翼付け根R部)")],
                          framed=True, state=HD.DocState()).split("\n")
    assert out == ["## 3. 試験、検査方法", "1．材料試験、検査の実施", "## 4. 製品表示", "0.7 D Ⅰ (翼付け根R部)"]


def test_history_section_through_real_chunks(monkeypatch):
    """HS-RU-006J（保存した実物の Nemotron Parse の出力）: 来歴のチャンクは「10. 基準来歴」の節で kind=control。
    図の範囲の「0.7 D Ⅰ (翼付け根R部)」は見出しにならず、表は「4) 欠陥検査」の下に残る"""
    if not os.path.isfile(TOKENIZER):
        pytest.skip("tokenizer が無い")
    monkeypatch.setenv("CHUNK_TOKENIZER", TOKENIZER)
    import src.create_chunks as cc
    from src.create_chunks import CreateChunksofDocument
    monkeypatch.setattr(cc, "TokenTextSplitter", lambda **kw: None)
    docs = load_pdf_as_markdown_regions(_pdf56("HS-RU-006J"), CV.RegionsConfig(figures="text"), parser=FakeParser())
    md = docs[0].page_content
    heads = [ln for ln in md.split("\n") if ln.startswith("#")]
    assert "## 10. 基準来歴" in heads
    assert not any("S62" in h or "H06" in h or "7.03" in h or re.search(r"0\.7 ?D", h) for h in heads), heads
    assert any(ln.startswith("図中の文字: ") and re.search(r"0\.7 ?D Ⅰ", ln) for ln in md.split("\n"))
    docs[0].metadata["title"] = "HS-RU-006J"
    chunks = CreateChunksofDocument(docs, None).split_file_into_chunks(512, 50, "t@example.com")
    hist = [c for c in chunks if "S62.03.30" in c.page_content]
    assert hist and all(c.metadata.get("kind") == "control" for c in hist)
    assert all((c.metadata.get("section_path") or [])[:1] == ["10. 基準来歴"] for c in hist)
    table = [c for c in chunks if "欠陥の間隔 | 欠陥の個数" in c.page_content]   # 「4) 欠陥検査」の領域の表（翼の領域図の後ろ）
    assert table and all((c.metadata.get("section_path") or [])[-1:] == ["4) 欠陥検査"] for c in table)


def _chart_page(lines, chart_rect, caption_rect=None):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (600, 800), "white").save(buf, format="PNG")
    page = _page(lines, png=buf.getvalue())
    blocks = [Block("Chart", _norm(chart_rect), "<table>…</table>")]
    if caption_rect:
        blocks.append(Block("Caption", _norm(caption_rect), "図"))
    return page, blocks


def test_figure_heading_candidates_need_known_chapter():
    """図の範囲の行は、その章が文書にあるときだけ見出しにする。ならなかった行は図中の文字に戻す（off・写真なら捨てる）"""
    lines = [("3-1.サージ誤差計算", 100, 210, 10), ("0.7 D Ⅰ (翼付け根R部)", 120, 240, 9), ("1.0 1.5 2.0 2.5", 120, 380, 9),
             ("圧力比", 100, 300, 9)]
    page, blocks = _chart_page(lines, (90, 200, 400, 400))
    out = assemble_page(page, blocks, None, "text")
    st = HD.DocState(chapters={"3"})
    md = HD.render_lines(out.lines, out.framed, st).split("\n")
    assert md[0] == "### 3-1 サージ誤差計算"
    fig = [ln for ln in md if ln.startswith("図中の文字: ")]
    assert len(fig) == 1 and "0.7 D Ⅰ (翼付け根R部)" in fig[0] and "1.0 1.5 2.0 2.5" in fig[0] and "圧力比" in fig[0]
    assert not any(ln.startswith("#") and "0.7" in ln for ln in md)
    assert "1.0 1.5 2.0 2.5" not in md                       # 本文の行として図から切り離さない
    # 章 3 が無い文書では「3-1」も見出しにしない
    page, blocks = _chart_page(lines, (90, 200, 400, 400))
    md = _render(assemble_page(page, blocks, None, "text")).split("\n")
    assert not any(ln.startswith("#") for ln in md) and "3-1.サージ誤差計算" in [ln for ln in md if ln.startswith("図中")][0]
    # off は図中の文字を出さないので、見出しにならなかった行も出さない
    page, blocks = _chart_page(lines, (90, 200, 400, 400))
    md = _render(assemble_page(page, blocks, None, "off"))
    assert "0.7 D" not in md and "1.0 1.5" not in md
    # 実物（VTR-T005B の添付資料のグラフ）: 軸の数字は図中の文字に入り、本文の行として出ない
    res, _ = _convert("VTR-T005B")
    lines = build_markdown(res).split("\n")
    assert "1.0 1.5 2.0 2.5" not in lines and "4.168.1rps" not in lines
    assert any(ln.startswith("図中の文字: ") and "1.0 1.5 2.0 2.5" in ln for ln in lines)


def test_no_double_output_when_text_belongs_to_other_region():
    """範囲の文字層の字が他の領域に入ったとき、Nemotron Parse の文字で 2 回目を出さない（VTR-T004C 4 ページ目、図を「文字だけ」と判定）"""
    class Reader:
        triage_client = describe_client = None

        def triage(self, png, cls):
            return ("文字だけ(図ではない)", "本文として読む") if cls == "Picture" else ("グラフ", "図として説明する")

        def describe(self, png, kind, cap):
            return "説明"

    from src.document_sources.pdf_regions import pdfpage
    cfg = CV.RegionsConfig(figures="describe").normalized()
    doc = pdfpage.open_document(_pdf("VTR-T004C"))
    try:
        page = doc[3]
        inp = CV._prepare(page, 4, cfg)
        page.close()
    finally:
        doc.close()
    with open(FIXTURE, encoding="utf-8") as f:
        raw = json.load(f)["pages"]["VTR-T004C p4 full"]
    out = assemble_page(inp, NM.parse_blocks(raw), Reader(), "describe", HD.doc_number_keys("VTR-T004C"))
    md = HD.render_lines(out.lines, out.framed, HD.DocState())
    assert md.count("1.62=1.85") == 1
    # 合成: 本文の領域の割り当てが 0 でも、範囲に文字層の字があれば Nemotron Parse の文字は出さない
    page = _page([("本文の行である。", 100, 100, 10)])
    blocks = [Block("Text", _norm((95, 95, 300, 112)), "本文の行である。"), Block("Text", _norm((98, 97, 200, 110)), "読み誤りの本文")]
    md = _render(assemble_page(page, blocks, None, "text"))
    assert md.count("本文の行") == 1 and "読み誤り" not in md


def _concurrency_probe(monkeypatch, url, clients, calls_each=2, hold=0.3):
    import threading
    import time as _time
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def post(payload, tag):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        _time.sleep(hold)
        with lock:
            state["now"] -= 1
        return {"choices": [{"message": {"content": "<x_0.1><y_0.1>a<x_0.2><y_0.2><class_Text>"}, "finish_reason": "stop"}]}

    for c in clients:
        monkeypatch.setattr(c, "_post", post)
    threads = [threading.Thread(target=lambda c=c: [c.parse(b"png", tag="t") for _ in range(calls_each)]) for c in clients for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return state["max"]


def test_nemotron_limit_is_shared_within_and_across_processes(monkeypatch, tmp_path):
    """Nemotron Parse の同時数の上限は、変換（クライアント）ごとではなく接続先ごと。作業プロセスの間でもロック用ファイルで共有する"""
    url = "http://parse-shared-test:8000/v1"
    clients = [NM.NemotronClient(url, concurrency=2, lock_dir=str(tmp_path)) for _ in range(3)]
    assert _concurrency_probe(monkeypatch, url, clients) == 2
    # プロセスをまたぐ: 2 つのプロセスがそれぞれ 3 本ずつ同時に送っても、同時は 2 本まで
    code = r"""
import json, os, sys, threading, time
sys.path.insert(0, %(root)r)
from src.document_sources.pdf_regions import nemotron as NM
log = %(log)r
def post(self, payload, tag):
    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    os.write(fd, ("%%f +1\n" %% time.time()).encode()); time.sleep(0.4)
    os.write(fd, ("%%f -1\n" %% time.time()).encode()); os.close(fd)
    return {"choices": [{"message": {"content": "<x_0.1><y_0.1>a<x_0.2><y_0.2><class_Text>"}, "finish_reason": "stop"}]}
NM.NemotronClient._post = post
c = NM.NemotronClient("http://parse-xproc-test:8000/v1", concurrency=2, lock_dir=%(locks)r)
ts = [threading.Thread(target=c.parse, args=(b"png",)) for _ in range(3)]
[t.start() for t in ts]; [t.join() for t in ts]
""" % {"root": ROOT, "log": str(tmp_path / "events.log"), "locks": str(tmp_path / "locks")}
    procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(2)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    events = sorted((float(t), int(d)) for t, d in (ln.split() for ln in open(tmp_path / "events.log")))
    now = peak = 0
    for _t, d in events:
        now += d
        peak = max(peak, now)
    assert len(events) == 12 and peak == 2


def test_frame_title_skips_label_cells():
    """題名の欄の候補から、型式・品名などの記入欄と英数字の多い欄を外す（報告書の様式）"""
    from src.document_sources.pdf_regions import frame as FR, pdfpage
    doc = pdfpage.open_document(_pdf56("報告書TPS48報告1"))
    try:
        page = doc[0]
        layer = pdfpage.text_layer(page)
        f = FR.detect_page_frame(layer, *pdfpage.line_segments(page))
        assert FR.frame_title(layer, f) == "気密不良による油もれ対策"
    finally:
        doc.close()
    assert FR._not_title("型式TPS48形過給機NV50N10N05Z") and FR._not_title("NV50N10N05Z")
    assert not FR._not_title("TPL.TPS形過給機の初号機に関するコンプレッサ単独性能確認試験要領及び判定基準")


def test_concurrent_conversions_in_one_process_do_not_crash():
    """同じプロセスで 2 本の変換を同時に走らせても落ちない（pdfium はスレッド安全ではないので、プロセスに 1 つの錠で読む）。
    落ちるとプロセスごと止まるので、別プロセスで確かめる"""
    if not os.path.isfile(os.path.join(PDF_DIR, "VTR-T005B.pdf")):
        pytest.skip("実物の PDF が無い")
    code = r"""
import asyncio, sys
sys.path.insert(0, %(root)r); sys.path.insert(0, %(tests)r)
from test_pdf_regions import FakeParser
from src.document_sources.pdf_regions.converter import RegionsConfig
from src.document_sources.pdf_regions.graph_builder import load_pdf_as_markdown_regions
files = [%(a)r, %(b)r]
async def main():
    for _ in range(4):
        docs = await asyncio.gather(*(asyncio.to_thread(load_pdf_as_markdown_regions, f, RegionsConfig(figures="text"), FakeParser())
                                      for f in files))
        assert all(d and d[0].page_content for d in docs)
asyncio.run(main())
print("ok")
""" % {"root": ROOT, "tests": HERE, "a": os.path.join(PDF_DIR, "VTR-T005B.pdf"), "b": os.path.join(PDF_DIR, "HS-VTR-T002F.pdf")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0 and out.stdout.strip().endswith("ok"), (out.returncode, out.stderr[-800:])


def test_unresponsive_endpoint_is_skipped_and_not_retried(tmp_path):
    """接続を受けて応答しない相手（ハングした vLLM）: 応答の時間切れは送り直さず、2 回続いたら接続先をしばらく呼ばない"""
    import socket
    import threading
    import time as _time
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    held = []

    def accept():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            held.append(c)

    threading.Thread(target=accept, daemon=True).start()
    url = f"http://127.0.0.1:{srv.getsockname()[1]}/v1"
    client = NM.NemotronClient(url, timeout=1.0, retries=2, concurrency=2, lock_dir=str(tmp_path))
    t0 = _time.time()
    for _ in range(2):
        with pytest.raises(NM.ParseError):
            client.parse(b"png")
    assert client.calls == 2                      # 時間切れは送り直さない
    with pytest.raises(NM.ParseError):
        client.parse(b"png")                      # 遮断中はすぐ失敗
    assert client.calls == 2 and _time.time() - t0 < 6
    srv.close()
    # 文書の締め切り: 締め切りを過ぎたページは送らず、文字層で組み立てる（半分を超えれば None で今の変換に戻る）
    # （同時 1 本の接続先が 1 枚 0.6 秒で答える。締め切り 0.5 秒なら 2 枚目以降は順番を待つ間に締め切りを過ぎる）
    slow = NM.NemotronClient("http://parse-deadline-test:8000/v1", concurrency=1, lock_dir=str(tmp_path),
                             deadline=_time.time() + 0.5)

    def answer(payload, tag):
        _time.sleep(0.6)
        return {"choices": [{"message": {"content": "<x_0.1><y_0.1>本文<x_0.5><y_0.2><class_Text>"}, "finish_reason": "stop"}]}
    slow._post = answer
    t0 = _time.time()
    res = CV.convert_pdf(_pdf("VTR-T005B"), CV.RegionsConfig(figures="text", deadline=0.5), parser=slow)
    assert res is None and _time.time() - t0 < 5 and slow.calls == 1


def test_rotated_and_cropped_pages_use_display_coordinates(tmp_path):
    """/Rotate と CropBox のずれがあっても、文字・罫線の座標はページ画像と同じ表示の座標になる"""
    import pypdfium2 as pdfium
    from src.document_sources.pdf_regions import frame as FR, pdfpage
    src = _pdf("VTR-T004C")
    for rot in (0, 90, 180, 270):
        pdf = pdfium.PdfDocument(src)
        pdf[0].set_rotation(rot)
        out = str(tmp_path / f"rot{rot}.pdf")
        pdf.save(out)
        pdf.close()
        doc = pdfpage.open_document(out)
        try:
            page = doc[0]
            layer = pdfpage.text_layer(page)
            W, H = layer.width, layer.height
            assert (W, H) == ((842.0, 595.0) if rot in (90, 270) else (595.0, 842.0)) or abs(W - (842 if rot in (90, 270) else 595)) < 1
            ink = [c for c in layer.chars if c.ink]
            assert all(-1 <= c.cx <= W + 1 and -1 <= c.cy <= H + 1 for c in ink)
            # 文字の矩形の中に、ページ画像の黒い点がある（座標が画像と合っている）
            img, scale = pdfpage.render_image(page, 1200)
            gray = img.convert("L")
            hits = 0
            sample = ink[::max(1, len(ink) // 60)][:60]
            for c in sample:
                box = (int(c.x0 * scale), int(c.y0 * scale), max(int(c.x0 * scale) + 1, int(c.x1 * scale)),
                       max(int(c.y0 * scale) + 1, int(c.y1 * scale)))
                if min(gray.crop(box).getdata()) < 128:
                    hits += 1
            assert hits >= 0.9 * len(sample), (rot, hits, len(sample))
            f = FR.detect_frame(*pdfpage.line_segments(page), W, H)
            assert f.kind == "body"
        finally:
            doc.close()
    # CropBox を (40, 40) ずらすと、文字も本文の欄も同じだけずれる
    pdf = pdfium.PdfDocument(src)
    pdf[0].set_cropbox(40, 40, 555, 802)
    out = str(tmp_path / "crop.pdf")
    pdf.save(out)
    pdf.close()
    base, crop = pdfpage.open_document(src), pdfpage.open_document(out)
    try:
        a, b = pdfpage.text_layer(base[0]), pdfpage.text_layer(crop[0])
        assert (b.width, b.height) == (515.0, 762.0)
        k = next(c.i for c in a.chars if c.ink)
        # 上端は 842 - 802 = 40pt、左端は 40pt ずれる
        assert abs((a.chars[k].x0 - b.chars[k].x0) - 40) < 0.2 and abs((a.chars[k].y0 - b.chars[k].y0) - 40) < 0.2
        fa = FR.detect_frame(*pdfpage.line_segments(base[0]), a.width, a.height)
        fb = FR.detect_frame(*pdfpage.line_segments(crop[0]), b.width, b.height)
        assert max(abs((x - y) - 40) for x, y in zip(fa.body, fb.body)) < 0.5
    finally:
        base.close()
        crop.close()


def test_pages_are_read_one_by_one_and_released(monkeypatch, tmp_path):
    """読み終えたページは手放す（同時に生きている PageInput は作業スレッドの数まで）。ページ数の上限を超える PDF は None"""
    import threading
    import weakref
    alive = {"now": 0, "max": 0}
    lock = threading.Lock()
    orig = CV._prepare

    def spy(page, number, cfg, *args):
        inp = orig(page, number, cfg, *args)
        with lock:
            alive["now"] += 1
            alive["max"] = max(alive["max"], alive["now"])

        def gone():
            with lock:
                alive["now"] -= 1
        weakref.finalize(inp, gone)
        return inp

    monkeypatch.setattr(CV, "_prepare", spy)
    cfg = CV.RegionsConfig(figures="text", parse_concurrency=1, vlm_concurrency=1)
    res = CV.convert_pdf(_pdf("HS-VTR-T002F"), cfg, parser=FakeParser())
    assert res is not None and alive["max"] <= 3 and alive["now"] == 0
    assert CV.convert_pdf(_pdf("HS-VTR-T002F"), CV.RegionsConfig(figures="text", max_pages=5), parser=FakeParser()) is None


def test_detect_frame_with_many_lines_is_fast():
    """方眼のように長い罫線が多いページでも、枠の検出はすぐ終わる（総当たりをしない）"""
    import time as _time
    from src.document_sources.pdf_regions import frame as FR
    n = 120
    hs = [(20.0, 575.0, 20.0 + i * 6.7) for i in range(n)]
    vs = [(20.0, 822.0, 20.0 + i * 4.6) for i in range(n)]
    t0 = _time.time()
    f = FR.detect_frame(hs, vs, 595.0, 842.0)
    assert _time.time() - t0 < 2.0 and f.kind in ("grid", "none")


def test_latex_ranges_and_symbols_in_tables():
    """範囲の ~ は「～」に、記号の命令は字に直す（空白にしたり消したりしない）"""
    raw = ("\\begin{tabular}{cc}\n表面硬度 & 1000~1350HV0.5\\\\\n深さ & 0.07~0.1mmにて 345~410HV0.5\\\\\n"
           "公差 & $\\pm$0.5 \\times 2\\\\\n温度 & 130$^{\\circ}$C $\\leq$ T\\\\\n範囲 & 900 \\textasciitilde{}1,050\\\\\n\\end{tabular}")
    grid = NM.table_grid(raw)
    assert grid[0][1] == "1000～1350HV0.5" and grid[1][1] == "0.07～0.1mmにて 345～410HV0.5"
    assert grid[2][1] == "±0.5 ×2" and grid[3][1] == "130°C ≤ T" and grid[4][1] == "900 ～1,050"
    # 実物（HS-TP-017D の保存出力）: 規格の範囲が残り、読み誤りの「室化」は文字層の「窒化」に直る
    res, _ = _convert("HS-TP-017D", path=_pdf56("HS-TP-017D"))
    md = build_markdown(res)
    assert "1000～1350HV0.5" in md and "0.07～0.1mm" in md and "345～410HV0.5" in md
    assert "室化" not in md and "| 1 | 窒化深さ検査(TPによる) |" in md
    assert "プラスト" not in md and "ICMで実施" in md


def test_table_cells_follow_text_layer():
    """表の構造は Nemotron Parse、セルの字は文字層に寄せる。文字層にある字どうしの食い違い（並びの違い）は直さない"""
    tl = [("No 検査項目 頻度", 100, 100, 10), ("1 窒化深さ検査 初品", 100, 114, 10), ("2 窒化範囲検査 全数", 100, 128, 10)]
    raw = "\\begin{tabular}{ccc}\nNo & 検査項目 & 頻度\\\\\n1 & 室化深さ検査 & 初品\\\\\n2 & 室化範囲検査 & 全数\\\\\n\\end{tabular}"
    md = _render(_table_page(raw, tl, (95, 95, 400, 142))).split("\n")
    assert md[2:] == ["| 1 | 窒化深さ検査 | 初品 |", "| 2 | 窒化範囲検査 | 全数 |"]
    rows = [[("材質", 1, 1), ("備考", 1, 1)], [("FC200", 1, 1), ("硬度", 1, 1)]]
    aligned, absent = __import__("src.document_sources.pdf_regions.assemble", fromlist=["x"]).align_cells(rows, "備考材質FC200硬度")
    assert [c for row in aligned for c, _, _ in row] == ["材質", "備考", "FC200", "硬度"] and absent == 0


def test_revision_stamp_is_not_appended_to_body_lines():
    """本文の欄の右端の改正の印（改正番号 6）は、本文の行の末尾につながらない（領域の組み立てと文字層だけの組み立ての両方）"""
    lines = [("1. 適用", 80, 90, 10), ("この基準は試験に適用する。", 80, 110, 10), ("改正番号 6", 520, 110, 9)]
    layer = TextLayer.from_lines(W, H, lines)
    frame = Frame("body", outer=(10, 10, 590, 790), body=(70, 80, 580, 700))
    page = PageInput(number=1, layer=layer, frame=frame, view=(70, 80, 580, 700))
    from src.document_sources.pdf_regions.assemble import fallback_page
    md = _render(fallback_page(page))
    assert "改正番号" not in md and "この基準は試験に適用する。" in md
    x0, y0, x1, y1 = 70, 80, 580, 700
    blocks = [Block("Text", ((78 - x0) / (x1 - x0), (108 - y0) / (y1 - y0), (575 - x0) / (x1 - x0), (122 - y0) / (y1 - y0)), "x")]
    md = _render(assemble_page(page, blocks, None, "text"))
    assert "改正番号" not in md
    res, _ = _convert("HS-VTR-T002F")
    assert "改正番号" not in build_markdown(res)


def test_spans_are_counted_once():
    """結合セルの中身は 1 回だけ数える（縦結合の長い欄で図扱いにしない、横結合の見出しで行の欠けを見逃さない）"""
    tl = [("熱処理後の硬さ試験 1 210", 100, 100, 10), ("2 205", 230, 114, 10), ("3 200", 230, 128, 10), ("4 198", 230, 142, 10),
          ("5 197", 230, 156, 10), ("6 196", 230, 170, 10)]
    raw = ("\\begin{tabular}{ccc}\n\\multirow{6}{*}{熱処理後の硬さ試験} & 1 & 210\\\\\n & 2 & 205\\\\\n & 3 & 200\\\\\n"
           " & 4 & 198\\\\\n & 5 & 197\\\\\n & 6 & 196\\\\\n\\end{tabular}")
    out = _table_page(raw, tl, (95, 95, 400, 185))
    assert out.stats["figures"] == 0 and out.stats["tables_gfm"] == 1
    tl = [("項目 区分 規格", 100, 100, 10), ("圧力比 計測値 ±1%以内", 100, 114, 10), ("誤差 マップ 1%以内", 100, 128, 10),
          ("空気量 計測値 ±1%以内", 100, 142, 10), ("温度 計測値 ±2℃以内", 100, 156, 10)]
    raw = "\\begin{tabular}{ccc}\n\\multicolumn{3}{c}{項目 区分 規格}\\\\\n圧力比 & 計測値 & ±1%以内\\\\\n\\end{tabular}"
    out = _table_page(raw, tl, (95, 95, 400, 172))
    md = _render(out)
    assert out.stats["tables_gfm"] == 0 and "誤差 マップ 1%以内" in md and "空気量 計測値 ±1%以内" in md
    assert NM.plain_text(raw) == "項目 区分 規格\n圧力比 計測値 ±1%以内"   # 結合セルを繰り返さない


def test_superscripts_are_joined_without_space():
    """字間がほぼ 0 の上付きの字は空白を入れずにつなぐ（m3/s）。横に重なる分数の分子と分母はつながない"""
    from src.document_sources.pdf_regions.textlayer import Char
    chars = []
    x = 100.0
    for c in "V(m":
        chars.append(Char(len(chars), c, x, 100, x + 5.5, 110, False, 10.0))
        x += 5.5
    chars.append(Char(len(chars), "3", x, 97, x + 3.5, 103, False, 6.0))
    x += 3.5
    for c in "/s)":
        chars.append(Char(len(chars), c, x, 100, x + 5.5, 110, False, 10.0))
        x += 5.5
    layer = TextLayer(W, H, chars)
    assert layer.text(layer.ink_indices()) == "V(m3/s)"


def test_scanned_pdf_is_left_to_the_current_converter(tmp_path):
    """文字層の無い PDF（スキャン）は None を返す（今の変換と同じく OCR を含む従来の読み込みに進ませる）"""
    from PIL import Image
    from src.document_sources.pdf_regions import pdfpage
    doc = pdfpage.open_document(_pdf("VTR-T005B"))
    try:
        img, _ = pdfpage.render_image(doc[0], 1000)
    finally:
        doc.close()
    scan = tmp_path / "scan.pdf"
    img.save(scan, "PDF", resolution=120.0)
    parser = FakeParser()
    assert CV.convert_pdf(str(scan), CV.RegionsConfig(figures="off"), parser=parser) is None and parser.calls == 0
    # 文字層のあるページが多数なら変換する。OCR の経路（ocr_route）が有効なら、スキャンのページが 1 つでもあれば None
    import pypdfium2 as pdfium
    mixed = pdfium.PdfDocument(_pdf("VTR-T005B"))
    mixed.import_pages(pdfium.PdfDocument(str(scan)))
    out = str(tmp_path / "mixed.pdf")
    mixed.save(out)
    mixed.close()
    survey = CV._missing_text
    doc = pdfpage.open_document(out)
    try:
        flags = [survey(pdfpage.survey_page(doc[i])) for i in range(len(doc))]
    finally:
        doc.close()
    assert flags == [False] * 6 + [True]
    assert CV.convert_pdf(out, CV.RegionsConfig(figures="off", ocr_route=True), parser=FakeParser()) is None


def test_ligatures_are_not_dropped(tmp_path):
    """1 つの字形が 2 字に当たる合字（ﬀ → ff）は、重ね書きとみなして落とさない"""
    from src.document_sources.pdf_regions import pdfpage
    cmap = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) "
            b"/Supplement 0 >> def /CMapName /Adobe-Identity-UCS def /CMapType 2 def 1 begincodespacerange <00> <FF> "
            b"endcodespacerange 2 beginbfchar <41> <00660066> <42> <006600660069> endbfchar endcmap CMapName currentdict "
            b"/CMap defineresource pop end end")
    content = b"BT /F1 24 Tf 72 700 Td (oAice  oBce  suffix) Tj ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(cmap) + cmap + b"\nendstream"]
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(buf.tell())
        buf.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = buf.tell()
    buf.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offsets:
        buf.write(b"%010d 00000 n \n" % o)
    buf.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    path = tmp_path / "lig.pdf"
    path.write_bytes(buf.getvalue())
    doc = pdfpage.open_document(str(path))
    try:
        layer = pdfpage.text_layer(doc[0])
    finally:
        doc.close()
    assert layer.text(layer.ink_indices()) == "office office suffix"


def test_history_records_in_one_region_are_not_joined():
    """来歴の 1 件ずつの記録（0改正… / A改正… / B改正…）は、いちばん長い行を折り返しとみなしてつながない"""
    layer = TextLayer.from_lines(W, H, [("0改正:H6. 8. 4新規調整 担当 回技部 福沢", 80, 100, 10),
                                        ("A改正:96. 1.20全面見直し 担当 回技部 大槻", 80, 114, 10),
                                        ("B改正:04. 3. 3一部見直し 担当 回品管 赤羽", 80, 128, 10)])
    assert len(layer.text(layer.ink_indices()).split("\n")) == 3


# ---- 本番 6 文書の計測で見つかった崩れの手直し（2026-11-09 の手直し 2） ----
def _chunks_of(stem, monkeypatch, path=None):
    """変換 → 既存の分割（CreateChunksofDocument）→ 章・節の層の目録。(Markdown, チャンク, 節, 目録の索引) を返す"""
    if not os.path.isfile(TOKENIZER):
        pytest.skip("tokenizer が無い")
    monkeypatch.setenv("CHUNK_TOKENIZER", TOKENIZER)
    import src.create_chunks as cc
    from src.create_chunks import CreateChunksofDocument
    monkeypatch.setattr(cc, "TokenTextSplitter", lambda **kw: None)
    from src import document_layer_rules as R
    from src import section_layer as SL
    fname = stem + ".pdf"
    docs = load_pdf_as_markdown_regions(path or _pdf(stem), CV.RegionsConfig(figures="text"), parser=FakeParser())
    docs[0].metadata["title"] = stem
    chunks = CreateChunksofDocument(docs, None).split_file_into_chunks(512, 50, "t@example.com")
    rows = [{"id": f"c{i}", "fileName": fname, "position": i + 1, "section": c.metadata.get("section"),
             "section_path": json.dumps(c.metadata.get("section_path") or [], ensure_ascii=False),
             "text": c.page_content, "page_number": c.metadata.get("page_number")} for i, c in enumerate(chunks)]
    secs, _ = SL.sections_from_catalogue(rows, R.load_config())
    return docs[0].page_content, chunks, secs, SL.SectionIndex(secs)


def test_grid_label_comes_from_the_page_title():
    """1. 格子のページの種類は、ページの題の位置（上の帯の、種類の語で終わる行）で決める。小欄の名前（台帳の下の
    「見直し確認欄(3年毎)」）や、協議・通知票の理由欄の文（「3．基準来歴が存在しないため。」）では決めない"""
    from src.document_sources.pdf_regions import frame as FR, pdfpage
    outer = Frame("grid", outer=(60.0, 70.0, 560.0, 790.0))
    layer = TextLayer.from_lines(W, H, [("協議・通知票", 250, 100, 16), ("3．基準来歴が存在しないため。", 90, 170, 11),
                                        ("見直し確認欄(3年毎)", 90, 650, 10)])
    assert FR.grid_label(layer, outer) == "協議・通知票"
    layer = TextLayer.from_lines(W, H, [("コンプレッサ性能管理曲線管理台帳", 200, 60, 12), ("管理番号 仕様区分", 90, 100, 10),
                                        ("見直し確認欄(3年毎)", 90, 650, 10)])
    assert FR.grid_label(layer, outer) == ""
    cases = [(_pdf("VTR-T003D"), 2, ""), (_pdf("VTR-T003D"), 3, "協議・通知票"), (_pdf("VTR-T_発行目録"), 1, "発行目録"),
             (_pdf56("HS-RU-013B"), 5, "協議・通知票"), (_pdf56("HS-RU-014A"), 7, "協議・通知票"),
             (_pdf56("HS-TP-013A"), 4, "協議・通知票")]
    for path, n, label in cases:
        doc = pdfpage.open_document(path)
        try:
            page = doc[n - 1]
            layer = pdfpage.text_layer(page)
            f = FR.detect_page_frame(layer, *pdfpage.line_segments(page))
            assert f.kind == "grid", (path, n)
            assert FR.grid_label(layer, f) == label, (path, n)
        finally:
            doc.close()


def test_annex_ledger_page_becomes_annex_section(monkeypatch):
    """1. VTR-T003D 2 ページ目（別紙-1 の台帳）は格子の様式にせず Nemotron Parse に送り、柱「HS-VTR-T003C 別紙-1」と枠の外の題名で
    「## 別紙-1 コンプレッサ性能管理曲線管理台帳」の付属の見出しにする。章・節の層で「別紙-1」が付属の節として引ける"""
    res, parser = _convert("VTR-T003D")
    md = build_markdown(res)
    lines = md.split("\n")
    pages = _pages(md)
    assert parser.modes.get(2) == "full"
    assert "## 別紙-1 コンプレッサ性能管理曲線管理台帳" in lines
    assert "## 見直し確認欄" not in lines and "HS-VTR-T003C" not in pages[2]
    assert "## 協議・通知票" in lines
    _md, _chunks, secs, idx = _chunks_of("VTR-T003D", monkeypatch)
    from src.document_layer_rules import parse_locator
    locator = parse_locator("別紙-1")          # 本文の「(別紙-1)」の参照の読み方
    assert locator and locator[0]["form"] == "付属"
    found = idx.find("VTR-T003D.pdf", locator[0])
    assert found
    sid = found[0] if isinstance(found, (list, tuple)) else found
    assert "コンプレッサ性能管理曲線管理台帳" in secs[sid].get("title", "")


def test_table_with_missing_row_falls_back_to_text_layer():
    """2. 表の領域の文字層の行（のかけら）に、表のセルのどれにも突き合わないものがあれば、表を文字層の行に戻す。
    字数は 7 割を超えるので、字数の規則では見つからない（VTR-T003D の来歴の「・2-3項の確認用を判定用に変更」）。
    欠けた行を補う方式は行と列の位置を推し量ることになり、規格の表で値を別の行に結び付けるおそれがあるので採らない
    （56 本では 220 表のうち 6 表が戻り、いずれも Nemotron Parse の表から字が欠けていた）"""
    tl = [("0改正 97.6.23 新規制定 担当 回技部 堀川", 80, 100, 10), ("C改正 02.08.19 一部見直し 担当 品管G 那須", 80, 114, 10),
          ("・AT形過給機を新規追加", 200, 128, 10), ("・2-3項の確認用を判定用に変更", 200, 142, 10),
          ("D改正 02.12.17 一部見直し 担当 品管G 那須", 80, 156, 10)]
    raw = ("\\begin{tabular}{cccccc}\n0改正 & 97.6.23 & 新規制定 & 担当 & 回技部 & 堀川\\\\\n"
           "C改正 & 02.08.19 & 一部見直し & 担当 & 品管G & 那須\\\\\n & & ・AT形過給機を新規追加 & & & \\\\\n"
           "D改正 & 02.12.17 & 一部見直し & 担当 & 品管G & 那須\\\\\n\\end{tabular}")
    out = _table_page(raw, tl, (75, 95, 560, 170))
    md = _render(out)
    assert "|" not in md and "・2-3項の確認用を判定用に変更" in md.split("\n")
    assert out.stats["tables_missing_rows"] == 1 and out.stats["tables_gfm"] == 0
    # 1 字の読み違い（VTR0.1 → VTRO.1）や空欄の印は欠けとみなさない
    tl = [("RU.RH.RH3.AT 20℃", 100, 100, 10), ("VTR0.1 15℃", 100, 114, 10), ("VTR4.4A 27℃", 100, 128, 10), ("――――― ―", 100, 142, 10)]
    raw = "\\begin{tabular}{cc}\nRU.RH.RH3.AT & 20°C\\\\\nVTRO.1 & 15°C\\\\\nVTR4.4A & 27°C\\\\\n & \\\\\n\\end{tabular}"
    out = _table_page(raw, tl, (95, 95, 400, 155))
    assert out.stats["tables_gfm"] == 1 and not out.stats.get("tables_missing_rows")
    # 実物
    res, _ = _convert("VTR-T003D")
    page1 = _pages(build_markdown(res))[1].split("\n")
    assert "・2-3項の確認用を判定用に変更" in page1 and "C改正 02.08.19 一部見直し 担当 品管G検査担当 那須" in page1
    assert {p["page"]: p for p in res.stats["page_stats"]}[1].get("tables_missing_rows") == 1


def test_annex_header_note_and_title_are_kept():
    """3. HS-VTR-T002F 7 ページ目: 付属のラベルと同じ柱に入った「(V−00862による)」を見出しの直後に残す。題「ABB方式 コンプレッサ性能」も
    柱と判定されていたので、ラベルより上の柱の短い行を題にする。VTR-T004C・VTR-T005B の同じ位置と同じ組み立てになる"""
    assert HD.header_notes("HSVTR-T002F添付資料(V−00862による)") == ["(V-00862による)"]
    assert HD.header_notes("HS-VTR-T005 B") == []
    heads = {}
    for stem, n in (("HS-VTR-T002F", 7), ("VTR-T004C", 4), ("VTR-T005B", 5)):
        res, _ = _convert(stem)
        page = [ln for ln in _pages(build_markdown(res))[n].split("\n") if ln.strip()]
        assert page[0] == "## 添付資料 ABB方式 コンプレッサ性能", (stem, page[:3])
        assert re.fullmatch(r"\(V-0\d{4} ?による\)", page[1]), (stem, page[1])
        assert page[2] == "### 初号機 合否判定方法", (stem, page[2])
        heads[stem] = page[1]
    assert heads["HS-VTR-T002F"].replace(" ", "") == "(V-00862による)"


def test_formula_under_figure_heading_belongs_to_the_section(monkeypatch):
    """4. 図の範囲の中か直下の番号の見出し（3-1.サージ誤差計算）の下にある、文・式・定義の行は図中の文字に入れず、見出しの後ろの本文に
    y の順に置く。評価の問い S25（サージラインのずれ = (実測風量a − マップ風量b) ÷ b × 100、±3%以内）の答えの箇所なので、
    3-1 の節に「実測風量」「マップ風量」が入ることを 3 文書で確かめる"""
    # 合成: グラフの範囲の下の方に見出しと式がある。軸の目盛り・凡例は図中の文字に残る
    lines = [("圧力比", 100, 300, 9), ("1.0 1.5 2.0 2.5", 120, 380, 9), ("3-1.サージ誤差計算", 100, 400, 10),
             ("a=実測風量 b=マップ風量", 140, 414, 10), ("15%", 300, 421, 9), ("X=±3%以内", 140, 432, 10)]
    page, blocks = _chart_page(lines, (90, 200, 400, 445))
    out = assemble_page(page, blocks, None, "text")
    md = HD.render_lines(out.lines, out.framed, HD.DocState(chapters={"3"})).split("\n")
    k = md.index("### 3-1 サージ誤差計算")
    assert md[k + 1:k + 3] == ["a=実測風量 b=マップ風量", "X=±3%以内"]
    fig = [ln for ln in md if ln.startswith("図中の文字: ")]
    assert len(fig) == 1 and "1.0 1.5 2.0 2.5" in fig[0] and "15%" in fig[0] and "実測風量" not in fig[0]
    # 実物の 3 文書: 見出しは「3-1 サージ誤差計算」だけ（右に並んだ「a=1.65」を入れない）。節の本文に式と定義が入る
    for stem in ("VTR-T005B", "VTR-T004C", "HS-VTR-T002F"):
        md, chunks, _secs, _idx = _chunks_of(stem, monkeypatch)
        lines = md.split("\n")
        assert "#### 3-1 サージ誤差計算" in lines, stem
        assert not any(ln.startswith("図中の文字: ") and ("実測風量" in ln or "マップ風量" in ln) for ln in lines), stem
        holder = [c.page_content for c in chunks if "#### 3-1 サージ誤差計算" in c.page_content.split("\n")]
        assert len(holder) == 1, stem
        body = holder[0].split("#### 3-1 サージ誤差計算", 1)[1]
        body = re.split(r"(?m)^#", body, maxsplit=1)[0]
        assert "実測風量" in body and "マップ風量" in body and "1.85%" in body, (stem, body)


def test_document_number_lines_are_not_joined():
    """5. 行頭が文書番号の行（関連基準の 1 件ずつ）は前の行につなげない（「…判定基準HS-VTR-T004(最新版)」にしない）。
    前の行が文の途中（「…付いては」「[QC-KAI-005.」）なら従来どおりつなぐ。品番（SL-1081B）は文書番号とみなさない"""
    from src.document_sources.pdf_regions.textlayer import Line as TL, paragraph_text

    def para(a, b):
        return paragraph_text([TL(a, 80, 100, 500, 110, 10, []), TL(b, 60, 112, 300, 122, 10, [])])

    assert para("コンプレッサ単独性能確認試験要領及び判定基準", "HS-VTR-T004(最新版) RU.RH.RH3.AT形過給機の初号機に関する") == \
        "コンプレッサ単独性能確認試験要領及び判定基準\nHS-VTR-T004(最新版) RU.RH.RH3.AT形過給機の初号機に関する"
    assert para("但しVTR4E/P/D形過給機に付いてはHS-VTR-T002、TPL/S形過給機に付いては", "HS-VTR-T005による。").count("\n") == 0
    assert para("製造部門は直ちに不適合処理手続き[QC-KAI-005.", "QC-KAI-018.QC-KAI-019]に従って処理する。").count("\n") == 0
    assert para("Wetterwart 1K-ZINC rich primer", "SL-1081B(Silver gray)") == "Wetterwart 1K-ZINC rich primer SL-1081B(Silver gray)"
    res, _ = _convert("VTR-T003D")
    md = build_markdown(res)
    assert "判定基準HS-VTR" not in md
    assert "コンプレッサ単独性能確認試験要領及び判定基準\nHS-VTR-T004(最新版) RU.RH.RH3.AT形過給機の初号機に関する" in md


def test_numbered_heading_line_is_not_joined_with_next_paragraph():
    """6. 行頭が番号の見出しの形の行（その行だけで題になるもの）には次の行をつなげず、見出しの判定に回す。題の末尾の括弧書きの注記の中の
    「は」では文と判定しない（HS-VTR-T001E の「2-5 外部振動計測 (VTR形過給機に適用、測定値は組付チェックシートに記録)」）"""
    from src.document_sources.pdf_regions.textlayer import Line as TL, paragraph_text
    head = "2-5 外部振動計測 (VTR形過給機に適用、測定値は組付チェックシートに記録)"
    text = paragraph_text([TL(head, 60, 100, 500, 110, 10, []), TL("振動計測は、最高回転数にて次に示す箇所で測定する。", 60, 112, 330, 122, 10, [])])
    assert text.split("\n") == [head, "振動計測は、最高回転数にて次に示す箇所で測定する。"]
    md = HD.render_lines([HD.Line(t) for t in text.split("\n")], framed=True, state=HD.DocState()).split("\n")
    assert md == ["### " + head, "振動計測は、最高回転数にて次に示す箇所で測定する。"]
    # 題が次の行に折り返した見出し（括弧が閉じていない）と、文の行（番号の後が文）は従来どおりつなぐ
    assert paragraph_text([TL("2-6 ブローバイガス量 (RU.RH.RH3.AT,TPL,", 60, 100, 500, 110, 10, []),
                           TL("TPS形過給機に適用)", 60, 112, 200, 122, 10, [])]).count("\n") == 0
    assert paragraph_text([TL("1. 本基準は過給機の単独試運転の判定に用いる性能管理曲線の", 60, 100, 500, 110, 10, []),
                           TL("管理について定める。", 60, 112, 200, 122, 10, [])]).count("\n") == 0
    assert HD.heading_title_ok("外部振動計測 (VTR形過給機に適用、測定値は組付チェックシートに記録)")
    assert not HD.heading_title_ok("本基準は試験に用いる (略)")
    res, _ = _convert("HS-VTR-T001E")
    lines = build_markdown(res).split("\n")
    assert "### " + head in lines
    assert "振動計測は、最高回転数にて次に示す箇所で測定する。" in lines


# ---- 2026-11-09 の手直し 3（parser3。HS 56 本の変換で見つかった崩れ） ----
def _one_page(stem, n, path=None):
    """実物の PDF の 1 ページを、保存した Nemotron Parse の出力で組み立てた Markdown（文書全体の変換は通さない）。
    返り値は (Markdown, PageOutput, PageInput)"""
    from src.document_sources.pdf_regions import pdfpage
    path = path or _pdf56(stem)
    own = HD.doc_number_keys(stem)
    doc = pdfpage.open_document(path)
    try:
        page = doc[n - 1]
        try:
            inp = CV._prepare(page, n, CV.RegionsConfig(), own)
        except TypeError:
            inp = CV._prepare(page, n, CV.RegionsConfig())   # 直す前のコード（試験が直す前に落ちることを確かめるため）
        page.close()
    finally:
        doc.close()
    blocks = FakeParser().parse(inp.png, tag=f"{stem}.pdf p{n}") if inp.needs_parse and inp.png else None
    out = assemble_page(inp, blocks, None, "text", own)
    return HD.render_lines(out.lines, out.framed, HD.DocState()), out, inp


def _gfm_rows(md):
    return [[c.strip() for c in ln.strip()[1:-1].split("|")] for ln in md.split("\n") if ln.startswith("| ")]


def test_numbered_heading_in_page_header_region_is_kept():
    """③ 本文の欄の上端の節の題を Nemotron Parse が柱（Page-header）と判定しても、番号の見出しの形の 1 行なら見出しとして残す
    （HS-TP-002C の 10・13 ページ目は図のページで、「5-13 コンプレッサケーシングアウターパート」「6-7 ベアリングカバー」
    「6-8 ウォールインサート」が消えて節が欠けていた）。文書番号の柱は今までどおり捨てる"""
    page = _page([("5-13 コンプレッサケーシングアウターパート", 100, 90, 11), ("HS-TP-002C", 450, 40, 10),
                  ("5-14 コンプレッサケーシングインナーパート", 100, 470, 11)])
    blocks = [Block("Page-header", _norm((98, 88, 360, 103)), "5-13 コンプレッサケーシングアウターパート"),
              Block("Page-header", _norm((448, 38, 520, 52)), "HS-TP-002C"),
              Block("Picture", _norm((150, 120, 450, 380)), ""),
              Block("Text", _norm((98, 468, 360, 483)), "5-14 コンプレッサケーシングインナーパート")]
    md = _render(assemble_page(page, blocks, None, "text", own_keys=HD.doc_number_keys("HS-TP-002C"))).split("\n")
    assert md[0] == "### 5-13 コンプレッサケーシングアウターパート"
    assert "### 5-14 コンプレッサケーシングインナーパート" in md and not any("HS-TP-002C" in ln for ln in md)
    md10, _out, _inp = _one_page("HS-TP-002C", 10)
    assert md10.split("\n")[0] == "### 5-13 コンプレッサケーシングアウターパート"
    md13 = _one_page("HS-TP-002C", 13)[0].split("\n")
    assert md13[:3] == ["### 6-7 ベアリングカバー", "", "### 6-8 ウォールインサート"]


def test_rotated_text_page_is_read_upright():
    """④ /Rotate は 0 のまま字だけを倒して横向きの付表を置いたページ（HS-TP-023 の 5 ページ目）は、字の向き（字の行列）を見て
    字が正立する向きに回して読む（文字層・罫線・Nemotron Parse に送る画像とも）。題と注記が 1 字ずつの行に逆順で並び、表の ○ が 0 に
    なり、付表のラベルも見出しにならなかった。回す前の向きで正立していた柱「ＨＳ－ＴＰ－０２３ 付表」は付属のラベルにし、題と 1 行の
    見出しにする。向きの違う字は、その字の向きで行にまとめる"""
    md, out, inp = _one_page("HS-TP-023", 5)
    lines = md.split("\n")
    assert lines[0] == "## 付表 国および船級協会の加工完成部品の輸入に伴う受検一覧表"
    assert not [ln for ln in lines if len(ln.strip()) == 1]
    rows = _gfm_rows(md)
    jg = next(r for r in rows if r[0] == "JG")
    assert jg[1:6] == ["○", "○", "○", "○", "○(水冷)"]
    assert "一覧表中○印でも立会が必要な部品はエンジン出力、シリンダ径により異なるので本規定を参照のこと。" in lines
    assert "HS-TP-023" not in md
    assert inp.extra == 90 and inp.needs_parse
    # 合成: 倒れた字（上へ並ぶ字）は、その字の向きで 1 行にまとめる。字の多くが倒れたページだけを回す
    from src.document_sources.pdf_regions.textlayer import Char
    up = [Char(k, c, 100.0, 488.0 - 12 * k, 112.0, 500.0 - 12 * k, False, 12.0, 270) for k, c in enumerate("受検一覧表")]
    layer = TextLayer(600, 800, up)
    lines = layer.lines([c.i for c in up])
    assert [ln.text for ln in lines] == ["受検一覧表"] and lines[0].rot == 270
    assert layer.dominant_rotation(min_chars=5) == 90
    flat = [Char(10 + k, c, 100.0 + 12 * k, 50.0, 112.0 + 12 * k, 62.0, False, 12.0) for k, c in enumerate("正立した柱の行です")]
    assert TextLayer(600, 800, up[:2] + flat).dominant_rotation(min_chars=1) == 0


def test_row_mismatch_returns_table_to_text_layer():
    """①② 行ごとの整合: 文字層の見た目の行を、表のどの行から来たかに順を保って対応させ、対応した表の行のセルに無い語があれば表を文字層に
    戻す。表全体の字の並びで見る欠け行の検査（unmatched_pieces）をすり抜けた 2 つの崩れを見つける:
      値が隣の行に移った表（材料試験成績表の「161 Pass」が試験片番号の行に入り、「130～180」が上の行に入った。Ni・Cr・Mg・Ti の列も落ちた）
      別の行に同じ字の並びがある行の欠け（HS-RU-004E 3 ページ目の部位①の許容欠陥数「≦1(合計3以下)」は部位④の行にもある）
    字間の空いた 1 字の語の並び（「材 料 試 験 成 績 表」）は 1 語につないで突き合わせる"""
    from src.document_sources.pdf_regions import assemble as AS
    # 値が隣の行に移った表
    tl = [("番号", 100, 100, 10), ("降伏点", 180, 100, 10), ("引張強さ", 260, 100, 10), ("硬さ", 360, 100, 10), ("判定", 440, 100, 10),
          ("規格", 100, 114, 10), ("250以上", 180, 114, 10), ("400以上", 260, 114, 10), ("130～180", 360, 114, 10),
          ("Yブロック", 100, 128, 10), ("310", 180, 128, 10), ("420", 260, 128, 10), ("161", 360, 128, 10), ("Pass", 440, 128, 10)]
    raw = ("\\begin{tabular}{ccccc}\n番号 & 降伏点 & 引張強さ & 硬さ & 判定\\\\\n規格 & 250以上 & 400以上 & 161 & Pass\\\\\n"
           "Yブロック & 310 & 420 & 130～180 & \\\\\n\\end{tabular}")
    out = _table_page(raw, tl, (95, 95, 480, 142))
    md = _render(out).split("\n")
    assert out.stats.get("tables_row_mismatch") == 1 and out.stats["tables_gfm"] == 0
    assert "規格 | 250以上 | 400以上 | 130～180" in md and "Yブロック | 310 | 420 | 161 | Pass" in md
    # 別の行に同じ字の並びがある行の欠け
    tl = [("部位", 100, 100, 10), ("欠陥", 160, 100, 10), ("大きさ", 240, 100, 10), ("個数", 340, 100, 10),
          ("①", 100, 114, 10), ("表面欠陥", 160, 114, 10), ("φ3以下", 240, 114, 10), ("1個(合計3以下)", 340, 114, 10),
          ("④", 100, 128, 10), ("表面欠陥", 160, 128, 10), ("φ5以下", 240, 128, 10), ("1個(合計3以下)", 340, 128, 10)]
    raw = ("\\begin{tabular}{cccc}\n部位 & 欠陥 & 大きさ & 個数\\\\\n1 & 表面欠陥 & φ3以下 & \\\\\n"
           "4 & 表面欠陥 & φ5以下 & 1個(合計3以下)\\\\\n\\end{tabular}")
    out = _table_page(raw, tl, (95, 95, 440, 142))
    assert out.stats.get("tables_row_mismatch") == 1
    assert "① | 表面欠陥 | φ3以下 | 1個(合計3以下)" in _render(out).split("\n")
    # 字間の空いた 1 字の語の並びは 1 語。その行が表に無ければ欠けとみなす
    layer = TextLayer.from_lines(W, H, [("材 料 試 験 成 績 表", 100, 100, 10)])
    assert AS.piece_words(layer, layer.ink_indices()) == ["材料試験成績表"]
    tl = [("材 料 試 験 成 績 表", 100, 100, 10), ("温度 130 ℃", 100, 114, 10), ("圧力 0.5 MPa", 100, 128, 10),
          ("流量 2.5 m3/s", 100, 142, 10), ("回転数 12000 rpm", 100, 156, 10)]
    raw = ("\\begin{tabular}{ccc}\n温度 & 130 & ℃\\\\\n圧力 & 0.5 & MPa\\\\\n流量 & 2.5 & m3/s\\\\\n回転数 & 12000 & rpm\\\\\n"
           "\\end{tabular}")
    out = _table_page(raw, tl, (95, 95, 300, 170))
    assert out.stats.get("tables_missing_rows") == 1 and "材 料 試 験 成 績 表" in _render(out)
    # 実物: 材料試験成績表（2 本）は格子に戻し、値が列名の下に来る。HS-RU-004E は部位①の許容欠陥数が入る
    md, out, _ = _one_page("TPS48BH19abura", 1)
    assert out.stats.get("tables_row_mismatch") == 1 and out.stats["tables_grid"] == 1
    rows = _gfm_rows(md)
    head = next(r for r in rows if "Brinell ブリネル HB" in r)
    yb = next(r for r in rows if r[0] == "Yブロック")
    assert yb[head.index("Brinell ブリネル HB")] == "161" and yb[head.index("Desision 判定")] == "Pass"
    assert yb[head.index("Tensile Strength 引張試験 N/m㎡")] == "420" and yb[head.index("Yield Point 降伏点 N/m㎡")] == "310"
    element = next(r for r in rows if r[0].startswith("Element") and "C" in r)
    assert {"Ni", "Cr", "Mg", "Ti", "Cu"} <= set(element)
    assert _one_page("TPS48BH19abura-2", 1)[1].stats.get("tables_row_mismatch") == 1
    md, out, _ = _one_page("HS-RU-004E", 3)
    lines = md.split("\n")
    assert out.stats.get("tables_row_mismatch") == 1
    assert "| 部位 | 欠陥 | 許容欠陥大きさ(mm) | 許容欠陥数 | 判定基準面積 |" in lines
    assert "| ① | 表面欠陥 | ≦φ3×0.3d | ≦1(合計3以下) | 羽根と羽根の間 |" in lines
    assert "| ④ | 突起 | ≦φ3×0.2d | ≦2(合計5以下) | 羽根1枚当たり |" in lines


def test_layer_table_is_built_from_rulings():
    """⑤ 文字層に戻した表は、表の領域に罫線があれば pdfium の線分から欄を求めて文字を欄に割り当て、GFM の表にする（罫線の 1 行に値が
    何段も並ぶときは段ごとに行を分ける）。罫線が無ければ、かけらの間を「 | 」で区切った行にする。行の中のかけらは x の順
    （書き順のかけら「Z-2 428 19」と「340」が入り組む行で、340 が 428 の右に来て値と列名の結び付きが分からなくなっていた）"""
    from src.document_sources.pdf_regions.textlayer import Char
    tl = [("番号", 100, 98, 10), ("降伏点", 165, 98, 10), ("伸び", 245, 98, 10),
          ("規格", 100, 112, 10), ("250以上", 165, 112, 10), ("12以上", 245, 112, 10),
          ("Z-1", 100, 127, 10), ("324", 165, 127, 10), ("18.5", 245, 127, 10),
          ("Z-2", 100, 139, 10), ("340", 165, 139, 10), ("19", 245, 139, 10)]
    short = "\\begin{tabular}{cc}\n番号 & 降伏点\\\\\n\\end{tabular}"
    hs = [(95.0, 320.0, y) for y in (95.0, 110.0, 124.0, 152.0)]
    vs = [(95.0, 152.0, x) for x in (95.0, 160.0, 240.0, 320.0)]
    page = PageInput(number=1, layer=TextLayer.from_lines(W, H, tl), frame=Frame("none", hs=hs, vs=vs), view=(0.0, 0.0, W, H))
    out = assemble_page(page, [Block("Table", _norm((95, 95, 320, 152)), short)], None, "text")
    assert out.stats["tables_to_text"] == 1 and out.stats["tables_grid"] == 1
    assert _render(out).split("\n") == ["| 番号 | 降伏点 | 伸び |", "|---|---|---|", "| 規格 | 250以上 | 12以上 |",
                                        "| Z-1 | 324 | 18.5 |", "| Z-2 | 340 | 19 |"]
    out = _table_page(short, tl, (95, 95, 320, 152))
    assert out.stats["tables_pieces"] == 1
    assert _render(out).split("\n") == ["番号 | 降伏点 | 伸び", "規格 | 250以上 | 12以上", "Z-1 | 324 | 18.5", "Z-2 | 340 | 19"]
    # 書き順のかけらが入り組む行は、かけらを大きな字間で切り直して左から並べる
    chars, i = [], 0
    for text, x in (("Z-2", 59.0), ("428", 317.0), ("19", 361.0)):
        for k, c in enumerate(text):
            chars.append(Char(i, c, x + 5 * k, 384.0, x + 5 * k + 5, 394.0, False, 10.0))
            i += 1
    chars.append(Char(i, "\n", 371.0, 384.0, 371.0, 394.0, True, 1.0))
    for k, c in enumerate("340"):
        chars.append(Char(i + 1 + k, c, 277.0 + 5 * k, 384.0, 282.0 + 5 * k, 394.0, False, 10.0))
    layer = TextLayer(842, 595, chars)
    assert [ln.text for ln in layer.lines(layer.ink_indices())] == ["Z-2 340 428 19"]
    # 実物: 材料試験成績表（TPS48BH19abura-3）の 3 表。試験片ごとの行で、降伏点・引張強さ・伸びが列名の下に来る
    for n, specimen, values in ((1, "Z-1", ("324", "428", "18.5")), (2, "Z-3", ("311", "438", "19.8")),
                                (3, "Z-4", ("300", "438", "18.1"))):
        md, out, _ = _one_page("TPS48BH19abura-3", n)
        assert out.stats["tables_grid"] == 1, n
        rows = _gfm_rows(md)
        head = next(r for r in rows if "Yield Point 降伏点 N/m㎡" in r and "Elongation 伸び %" in r)
        row = next(r for r in rows if r[0] == specimen)
        got = tuple(row[head.index(h)] for h in ("Yield Point 降伏点 N/m㎡", "Tensile Strength 引張試験 N/m㎡", "Elongation 伸び %"))
        assert got == values, (n, row)


# ---- 文字層だけの簡易変換（regions の戻り道）と、ページごとの読み込み（2026-11-10、AGPL の部品を外した置き換え） ----
def _tiny_pdf(path, pages):
    """試験用の小さな PDF（Helvetica の英数字）。pages はページごとの行の並び"""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None]
    kids = []
    for lines in pages:
        content = b"BT /F1 12 Tf 72 720 Td 16 TL " + b" ".join(b"(%s) Tj T*" % ln.encode("ascii") for ln in lines) + b" ET"
        page_no = len(objs) + 1
        kids.append(page_no)
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                    b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (page_no + 1, 3 + 2 * len(pages)))
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objs[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(buf.tell())
        buf.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = buf.tell()
    buf.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offsets:
        buf.write(b"%010d 00000 n \n" % o)
    buf.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    path.write_bytes(buf.getvalue())
    return str(path)


def test_plain_converter_on_a_small_pdf(tmp_path):
    """簡易変換: ページごとに <!-- page:N --> と本文の行。番号の見出しは番号の規則で見出しにする。Nemotron Parse は呼ばない"""
    from src.document_sources.pdf_regions.plain import convert_pdf_plain
    path = _tiny_pdf(tmp_path / "tiny.pdf", [["1. Scope", "This standard applies to boiler tubes."],
                                             ["2. Terms", "Tube means a pressure part."]])
    res = convert_pdf_plain(path)
    assert res is not None and [n for n, _ in res.pages] == [1, 2] and res.stats["converter"] == "plain"
    md = build_markdown(res)
    pages = _pages(md)
    assert "This standard applies to boiler tubes." in pages[1] and "Tube means a pressure part." in pages[2]
    assert re.search(r"(?m)^#+ 1\. Scope$", pages[1]) and re.search(r"(?m)^#+ 2\. Terms$", pages[2])
    # 開けない PDF は None
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 broken")
    assert convert_pdf_plain(str(bad)) is None


def test_plain_converter_matches_text_layer_on_real_pdf():
    """実物の PDF（本番 6 文書の 1 本）: 全ページに印が付き、本文の字は文字層の字をほぼ保つ（柱・改正の印は落とす）"""
    import pypdfium2 as pdfium
    from src.document_sources.pdf_regions.plain import convert_pdf_plain
    path = _pdf("VTR-T005B")
    res = convert_pdf_plain(path)
    assert res is not None and res.title
    pages = _pages(build_markdown(res))
    doc = pdfium.PdfDocument(path)
    try:
        count = len(doc)
        layer_text = ""
        for i in range(count):
            tp = doc[i].get_textpage()
            layer_text += tp.get_text_range()
            tp.close()
    finally:
        doc.close()
    assert sorted(pages) == list(range(1, count + 1))
    got, want = _bag("".join(pages.values())), _bag(layer_text)
    kept = sum(min(c, got[ch]) for ch, c in want.items()) / max(1, sum(want.values()))
    assert kept >= 0.85, kept
    assert any(re.match(r"#{2,6} ", ln) for md in pages.values() for ln in md.split("\n"))


def test_regions_over_page_limit_falls_back_to_plain(monkeypatch):
    """ページ数が PDF_REGIONS_MAX_PAGES を超える PDF は、regions が None を返し、簡易変換で変換する（以前は pymupdf4llm）"""
    from src.document_sources import local_file
    path = _pdf("VTR-T005B")
    monkeypatch.setenv("PDF_CHUNK_STRATEGY", "markdown")
    monkeypatch.setenv("MARKDOWN_CHUNKING", "true")
    monkeypatch.setenv("PDF_CONVERTER", "regions")
    monkeypatch.setenv("EDC_PDF_CONVERT", "false")
    monkeypatch.setenv("PDF_REGIONS_MAX_PAGES", "2")
    # Nemotron Parse に届かないこと（--network none でも、ページ数の判定が先なので要求は作られない）
    monkeypatch.setenv("PDF_REGIONS_PARSE_URL", "http://127.0.0.1:9/v1")
    loader, raw = local_file.load_document_content(path)
    docs = loader.load()
    assert raw is True and len(docs) == 1 and docs[0].metadata["pdf_converter"] == "plain"
    assert docs[0].metadata["markdown"] and docs[0].metadata["markdown_strict"]
    assert "<!-- page:1 -->" in docs[0].page_content and "<!-- page:6 -->" in docs[0].page_content


def test_plain_converter_leaves_scanned_pdf_to_the_next_loader(tmp_path, monkeypatch):
    """文字層の無い PDF は簡易変換も None。local_file は従来の読み込み（ページごとの PdfPageLoader）に進む"""
    from src.document_sources import local_file
    from src.document_sources.pdf_regions import pdfpage
    from src.document_sources.pdf_regions.plain import convert_pdf_plain
    doc = pdfpage.open_document(_pdf("VTR-T005B"))
    try:
        img, _ = pdfpage.render_image(doc[0], 1000)
    finally:
        doc.close()
    scan = tmp_path / "scan.pdf"
    img.save(scan, "PDF", resolution=120.0)
    assert convert_pdf_plain(str(scan)) is None
    monkeypatch.setenv("PDF_CHUNK_STRATEGY", "markdown")
    monkeypatch.setenv("MARKDOWN_CHUNKING", "true")
    monkeypatch.setenv("EDC_PDF_CONVERT", "false")
    monkeypatch.setenv("PDF_REGIONS_PARSE_URL", "http://127.0.0.1:9/v1")
    loader, raw = local_file.load_document_content(str(scan))
    assert isinstance(loader, local_file.PdfPageLoader) and raw is False
    pages = loader.load()
    assert len(pages) == 1 and pages[0].metadata == {"source": str(scan), "page": 0}


def test_pdf_page_loader_reads_each_page(tmp_path, monkeypatch):
    """PdfPageLoader（以前の PyMuPDF の読み込みの置き換え）: 1 ページ 1 Document、metadata は source と page（0 始まり）"""
    from src.document_sources import local_file
    path = _tiny_pdf(tmp_path / "pages.pdf", [["first page text"], ["second page text"], ["third"]])
    pages = local_file.PdfPageLoader(path).load()
    assert [p.metadata for p in pages] == [{"source": path, "page": i} for i in range(3)]
    assert "first page text" in pages[0].page_content and "second page text" in pages[1].page_content
    # 分割の経路は metadata の page でページ番号を付ける（create_chunks.split_file_into_chunks の token の経路）
    assert "page" in pages[0].metadata
    # get_documents_from_file_by_path（EDC 無効・markdown 無効のとき）もこの読み込みを使う
    monkeypatch.delenv("PDF_CHUNK_STRATEGY", raising=False)
    monkeypatch.setenv("EDC_PDF_CONVERT", "false")
    name, docs, ext = local_file.get_documents_from_file_by_path(path, "pages.pdf")
    assert ext == ".pdf" and len(docs) == 3 and docs[2].page_content.strip() == "third"
