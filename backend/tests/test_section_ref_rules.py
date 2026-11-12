# -*- coding: utf-8 -*-
"""参照追跡の精度向上 M1〜M4 の規則の単体試験（グラフも LLM も使わない。偽のグラフで流す）。

確かめること:
- M1 番号の読み取り（document_layer_rules.parse_locator）: 本番の LLM の参照リンク 52 本に現れる位置の語（locator）16 種と、
  章・節への参照 36 件（tools/kg/refsec/refs_dump.json）の一文のうち番号の手がかりを持つもの
- M2・M3 文書番号の直後の番号（numbers_after_document_numbers）: 文書層の一文。参照元自身の項番（「4-2項の…を HS VTR-T003 とした」）は読まない
- M4 主所属: 本文の先頭の行が見出しの行なら、その見出しの節を主所属にする（分割時のパンくずと「### 」のずれで直前の節にならない）
- 読み取りの誤りを防ぐ形: ハイフンの異体（「3−5項」「3ー5項」「3—5項」）、「上記3項目」、「( 2 )」、条の中の「第2項」
- E9・E10 の付け直し（section_layer.resolve_section_refs）: 章の番号・範囲・付属・号・解けない番号・文書番号による参照先の文書の決定・
  自分の文書を指す位置の語（「上記2項」）・同じ文書番号の別の版・文書層の一文からの節への線・
  同じ参照の結び先のまとめ（既定は無効）・何度流しても同じ結果
実行: docker exec -w /code backend python -m pytest -q -p no:cacheprovider tests/test_section_ref_rules.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from src import document_layer_rules as R  # noqa: E402
from src import section_layer as SL  # noqa: E402

cfg = R.load_config()


def forms(text):
    return [(t["form"], t["number"] if t["form"] != "付属" else t["label"]) for t in R.parse_locator(text)]


# ───────────────────────── M1 位置の語の読み取り ─────────────────────────
# 本番の LLM の参照リンク 52 本に現れる位置の語（11 月 8 日の本番グラフ）と、読み取るべき番号
REAL_LOCATORS = {
    "2-1及び2-2項": [("節", "2-1"), ("節", "2-2")],
    "": [],
    "別表1 符号A": [("付属", "別表1")],
    "別表1 符号C": [("付属", "別表1")],
    "別表1 符号B": [("付属", "別表1")],
    "添付資料": [],                       # 番号が無い（M7、LLM の結び先に頼る）
    "2項": [("章", "2")],
    "上記4-1及び4-2": [("節", "4-1"), ("節", "4-2")],
    "上記2項": [("章", "2")],
    "3項": [("章", "3")],
    "別表2": [("付属", "別表2")],
    "別紙-1": [("付属", "別紙1")],
    "3-5項": [("節", "3-5")],
    "上記2-1~2-3": [("節", "2-1"), ("節", "2-2"), ("節", "2-3")],
    "マップ上": [],                       # 図の上の位置で、節ではない
    "3の点": [],                         # 図の中の点の番号で、節ではない
}


def test_real_locators():
    for loc, expected in REAL_LOCATORS.items():
        assert forms(loc) == expected, (loc, forms(loc))


def test_other_forms():
    assert forms("付表6") == [("付属", "付表6")]
    assert forms("(2)") == [("項", "2")]
    assert forms("3)") == [("号", "3")]
    assert forms("2~3項") == [("章", "2"), ("章", "3")]
    assert forms("第3章") == [("章", "3")]
    assert forms("3-1-2項") == [("項", "3-1-2")]
    assert forms("2-1~3") == [("節", "2-1"), ("節", "2-2"), ("節", "2-3")]
    assert forms("上記2-1〜2-3") == [("節", "2-1"), ("節", "2-2"), ("節", "2-3")]
    # 文書番号・識別子・日付の中の数字は読まない
    assert forms("HS-VTR-T001-5") == []
    assert forms("98.5.9") == []
    assert forms("HS-VTR-T001 3-5項") == [("節", "3-5")]


# 章・節への参照 36 件（refs_dump.json）のうち、誤り・広すぎるの 20 件。番号 = その参照の LLM のリンクの位置の語から読めるもの
# （添付資料・上記例・図の中の語は番号を持たない。「- 3)サージライン…」の「3)」はその文自身の箇条の番号で、参照ではない）
REVIEW_CASES = [
    # (点検表の番号, 一文の先頭, 位置の語, 読み取るべき番号)
    (0, "2)作成された管理曲線は検査部門が「コンプレッサ性能管理曲線管理台帳」(別紙-1)", "別紙-1", [("付属", "別紙1")]),
    (1, "初号機に該当する過給機について、組立部門は下記要領にて運転を行ない", "添付資料", []),
    (4, "検査部門は上記2項の試験結果に基づき、次により管理カ-ブを作成し", "上記2項", [("章", "2")]),
    (5, "検査部門は2項で作成したコンプレッサ性能カーブを", "2項", [("章", "2")]),
    (6, "- 2)上記例では3の点(中風量)の回転数168.3rpsを基に", "3の点", []),
    (7, "- 3)サージラインと合った点でマップ上の回転数Cを求める。", "マップ上", []),
    (10, "検査部門は上記2項の試験結果及び3項の判定基準を基に", "上記2項", [("章", "2")]),
    (11, "検査部門は上記2項の試験結果及び3項の判定基準を基に", "3項", [("章", "3")]),
    (13, "初号機に該当する過給機について、組立部門は下記要領にて運転を行ない", "添付資料", []),
    (14, "但しVTR4E/P/D形過給機に付いてはHS-VTR-T002、TPL/S形過給機に付いては HS-VTR-T005による。", "", []),
    (20, "ｴﾝｼﾞﾝ特性曲線", "", []),
    (21, "6-3 関連部門は、問題解決に向けて迅速な活動を進め、必要に応じて2項の再試験を実施する。", "2項", [("章", "2")]),
    (22, "- 3 )サージラインと合った点でマップ上の回転数 C を求める。", "", []),
    (25, "HS VTR-T001の性能試験による。", "", []),
    (27, "検査部門は上記2項の試験結果及び3項の判定基準を基に、次により管理カ-ブを作成し、", "上記2項", [("章", "2")]),
    (30, "初号機に該当する過給機について、組立部門は下記要領にて運転を行ない過給機回転数、", "添付資料", []),
    (32, "初号機に該当する過給機について、組立部門は下記要領にて運転を行ない過給機回転数、", "添付資料", []),
    (33, "検査部門は2項で作成したコンプレッサ性能カーブを、設計部門より出図された", "2項", [("章", "2")]),
    (34, "過給機性能運転及び性能換算法について、JIS 方式及びABB 方式の2方法を各型式区分により、実施する。", "", []),
    (35, "過給機性能運転及び性能換算法について、JIS 方式及びABB 方式の2方法を各型式区分により、実施する。", "", []),
]


def test_dash_variants_and_non_numbers():
    # NFKC で「-」にならないハイフンの異体と長音記号。「5項」だけを章 5 と読まない
    for loc in ("3−5項", "3ー5項", "3—5項", "3‒5項", "3➖5項", "3－5項", "3‐5項", "3–5項", "3 - 5項", "3 − 5項"):
        assert forms(loc) == [("節", "3-5")], (loc, forms(loc))
    assert forms("上記4−1及び4ー2") == [("節", "4-1"), ("節", "4-2")]
    assert forms("別紙ー1") == [("付属", "別紙1")] and forms("別紙−1") == [("付属", "別紙1")]
    assert forms("HS−VTR−T001−5") == []          # 文書番号の中の数字は、異体のハイフンでも読まない
    # 番号ではないもの
    assert forms("上記3項目") == [] and forms("3項目") == []
    assert forms("第2項") == [] and forms("第3条第2項") == []   # 条の中の項。章の番号ではない
    assert forms("( 2 )") == [("項", "2")]       # 空白の入った項の印を号と読まない
    # 片仮名の長音記号は番号の間でなければそのまま（「データー2項」は章 2）
    assert forms("データー2項") == [("章", "2")]
    # 文書番号の直後の番号も同じ
    q = "性能計測時の過給機運転条件は、HS−VTR−T001の3−5項に依る。"
    assert [(c, t["form"], t["number"]) for c, t in R.numbers_after_document_numbers(q, cfg)] == [("HS-VTR-T001", "節", "3-5")]
    assert [(c, t["number"]) for c, t in R.numbers_after_document_numbers("HS-VTR-T001の3ー5項", cfg)] == [("HS-VTR-T001", "3-5")]
    assert R.numbers_after_document_numbers("HS-VTR-T001の3項目について", cfg) == []
    assert R.annex_key("別紙ー1 台帳") == "別紙1"


def test_review_cases_numbers():
    for index, quote, loc, expected in REVIEW_CASES:
        assert forms(loc) == expected, (index, loc, forms(loc))
    # 文書番号の手がかり（点検表 14・25）は文書を決める。節の番号は一文に無い
    assert R.document_numbers_in(REVIEW_CASES[9][1], cfg) == ["HS-VTR-T002", "HS-VTR-T005"]
    assert R.document_numbers_in(REVIEW_CASES[13][1], cfg) == ["HS-VTR-T001"]
    assert R.numbers_after_document_numbers(REVIEW_CASES[13][1], cfg) == []


# ───────────────────────── M2・M3 文書番号の直後の番号 ─────────────────────────
def test_numbers_after_document_numbers():
    q = "性能計測時の過給機運転条件は、HS-VTR-T001の3-5項に依る。"
    assert [(c, t["form"], t["number"]) for c, t in R.numbers_after_document_numbers(q, cfg)] == [("HS-VTR-T001", "節", "3-5")]
    q = "### 6-1 項のHS VTR-T001 付表6 ．．．削除"
    assert [(c, t["form"], t["label"]) for c, t in R.numbers_after_document_numbers(q, cfg)] == [("HS-VTR-T001", "付属", "付表6")]
    # 参照元自身の項番は読まない
    assert R.numbers_after_document_numbers("4-2項の管理カーブの管理要領をHS VTR-T003とした。", cfg) == []
    assert R.numbers_after_document_numbers("3-2 管理カ-ブはHS-VTR-T003により管理する。", cfg) == []
    assert R.numbers_after_document_numbers("HS VTR-T001の性能試験による。", cfg) == []
    # 文書番号と番号の間の空白で文書番号を見失わない
    assert [(c, t["number"]) for c, t in R.numbers_after_document_numbers("HS-VTR-T001 3-5項による", cfg)] == [("HS-VTR-T001", "3-5")]
    assert [(c, t["form"], t["number"]) for c, t in R.numbers_after_document_numbers("HS-VTR-T001E の 2 項", cfg)] == [("HS-VTR-T001", "章", "2")]


def test_annex_key():
    assert R.annex_key("別表2 過給機性能試験及び性能換算法") == "別表2"
    assert R.annex_key("# 別紙-1 台帳") == "別紙1"
    assert R.annex_key("別表21") == "別表21"
    assert R.annex_key("コンプレッサ性能計測要領") is None


# ───────────────────────── 偽のグラフで付け直しを流す ─────────────────────────
def chunk(cid, f, pos, text):
    """本番のチャンクと同じ形の行。section（見出しの経路）は本文の先頭のパンくず「[文書 > 見出し]」と同じにする。"""
    return {"id": cid, "fileName": f, "position": pos, "section": text.split("\n", 1)[0].strip("[]"), "section_path": None, "text": text,
            "page_number": None}


A, B = "zz-A.md", "zz-B.md"   # A = HS-VTR-T901（参照元）、B = HS-VTR-T902（参照先）
ROWS = [
    chunk("a1", A, 1, "[zz-A > 1. 適用]\n## 1. 適用\nこの基準は試験用の文書である。試験方法は別表2による。\n\n## 2. 試験要領\n組立部門は次により運転を行う。"),
    chunk("a2", A, 2, "[zz-A > 2. 試験要領]\n### 2-1 第1台目\n計測は別表1による。\n### 2-2 第2台目\n上記2-1と同じとする。"),
    chunk("a3", A, 3, "[zz-A > 3. 管理]\n## 3. 管理\n検査部門は上記2項の試験結果に基づき管理する。性能計測時の運転条件は、HS-VTR-T902の3-5項に依る。"),
    chunk("a4", A, 4, "[zz-A > 別表2 試験方法]\n# 別表2 試験方法\n試験方法を示す。潤滑油はHS-VTR-T902による。"),
    chunk("b1", B, 1, "[zz-B > 1. 適用]\n## 1. 適用\nこの基準は判定基準である。\n## 2. 確認項目\n確認を行う。\n## 3. 判定基準\n判定は次による。\n### 3-1 性能試験\n性能を確かめる。"),
    chunk("b2", B, 2, "[zz-B > 3. 判定基準 > 3-4 外観検査]\n### 3-4 外観検査\n漏れの無いこと。目視で確かめる。"),
    chunk("b3", B, 3, "[zz-B > 3. 判定基準 > 3-5 潤滑油供給条件]\n### 3-5 潤滑油供給条件\n単独運転装置で試運転時の潤滑油供給条件を示す。"),
    chunk("b4", B, 4, "[zz-B > 3. 判定基準]\n### 3-6 潤滑油温度\n130°C以下であること。"),
]
Q_CHAP = "検査部門は上記2項の試験結果に基づき管理する。"
Q_STD = "性能計測時の運転条件は、HS-VTR-T902の3-5項に依る。"
LINKS = [
    # LLM のリンク: (参照元, 結び先のチャンクか None, 結び先の文書, 位置の語, 一文)
    ("a3", "a1", A, "上記2項", Q_CHAP),                       # M1: LLM は 1 章のチャンクを選んだ → 2 章へ
    ("a3", None, A, "3-5項", Q_STD),                          # M2: LLM は文書 A（誤り）を選んだ → 文書番号で B の 3-5 へ
    ("a2", "a2", A, "上記2-1", "上記2-1と同じとする。"),
    ("a2", "a4", A, "別表1", "計測は別表1による。"),           # 別表1 の節は無い → LLM の結び先、unresolved_locator
    ("a1", "a4", A, "別表2", "試験方法は別表2による。"),       # M1: 付属の節を題名の先頭で引く
    ("a4", "b3", B, "", "潤滑油はHS-VTR-T902による。"),        # 同じ参照の結び先 2 つ → 両方を含む最も深い節 1 つ
    ("a4", "b4", B, "", "潤滑油はHS-VTR-T902による。"),
    ("a1", "b1", B, "3)", "上記の3)による。"),                  # 号: 同じ節の中の箇条。参照元の節（ただし LLM の文書が別なので解かない）
]


class FakeGraph:
    """resolve_section_refs が読む 3 つの照会（版・文書層の参照・参照リンク）に答える。書き込みは受け付けない。"""

    def __init__(self, links=LINKS, versions=((A, "HS-VTR-T901", "A"), (B, "HS-VTR-T902", "B")), doc_quotes=((A, "HS-VTR-T902", [Q_STD]),)):
        self.links, self.versions, self.doc_quotes = links, versions, doc_quotes
        self.calls = []

    def query(self, cypher, params=None):
        self.calls.append(cypher)
        assert not any(w in cypher for w in ("CREATE ", "MERGE ", "DELETE ", " SET ", "REMOVE ")), "書き込みの照会"
        if "IS_VERSION_OF" in cypher:
            return [{"f": f, "st": st, "rev": rev} for f, st, rev in self.versions]
        if "r.quotes AS quotes" in cypher:
            return [{"f": f, "st": st, "quotes": q} for f, st, q in self.doc_quotes]
        if "WHERE b:Chunk OR b:Document" in cypher:
            return [{"src": s, "tgt": t, "tgt_file": f, "locator": loc, "quote": q, "method": "llm"} for s, t, f, loc, q in self.links]
        raise AssertionError("想定外の照会: " + cypher[:80])


def layer(graph=None, rows=None, merge=False):
    rows = rows or ROWS
    sections, chunk_sections = SL.sections_from_catalogue(rows, cfg)
    chunk_section = {cid: next((sid for sid, p in lst if p), lst[0][0]) for cid, lst in chunk_sections.items()}
    saved, SL.MERGE_LLM_TARGETS = SL.MERGE_LLM_TARGETS, merge
    try:
        refs, summary = SL.resolve_section_refs(graph or FakeGraph(), rows, sections, chunk_sections, chunk_section, cfg)
    finally:
        SL.MERGE_LLM_TARGETS = saved
    return sections, chunk_sections, chunk_section, refs, summary


def label(sections, sid):
    s = sections[sid]
    return (s["fileName"], s["number"] or s["title"])


def by_source(sections, refs):
    out = {}
    for (src, sid), v in refs.items():
        out.setdefault(src, []).append((label(sections, sid), v["via"], v.get("standard"), v.get("unresolved_locator")))
    return {k: sorted(v, key=str) for k, v in out.items()}


def test_m4_heading_line_is_primary():
    sections, chunk_sections, chunk_section, _refs, _ = layer()
    # b3 は本文の先頭が「### 3-5 潤滑油供給条件」。従来は見出しの位置（「### 」の後ろ）がチャンクの先頭より後なので直前の 3-4 が主所属だった
    assert label(sections, chunk_section["b3"]) == (B, "3-5")
    assert label(sections, chunk_section["b4"]) == (B, "3-6")
    assert label(sections, chunk_section["a3"]) == (A, "3")
    assert label(sections, chunk_section["a2"]) == (A, "2-1")
    # 主所属は 1 チャンクに 1 つ。3-5 には b3 が主、b4 が副（3-6 の見出しの前の部分）で属する
    for cid, lst in chunk_sections.items():
        assert sum(1 for _, p in lst if p) == 1, cid
    members = {label(sections, sid): p for sid, p in chunk_sections["b4"]}
    assert members[(B, "3-5")] is False and members[(B, "3-6")] is True


def test_m4_falls_back_when_first_line_is_body():
    rows = [chunk("c1", "zz-C.md", 1, "[zz-C > 1. 適用]\n## 1. 適用\n本文。\n## 2. 要領\n本文の続き。"),
            chunk("c2", "zz-C.md", 2, "[zz-C > 2. 要領]\n前のチャンクからの続きの文。\n### 2-1 計算\n計算する。")]
    sections, chunk_sections = SL.sections_from_catalogue(rows, cfg)
    primary = {cid: next(sid for sid, p in lst if p) for cid, lst in chunk_sections.items()}
    assert (sections[primary["c2"]]["number"]) == "2"     # 先頭の行が本文なので従来どおり、チャンクの先頭を含む節
    assert (sections[primary["c1"]]["number"]) == "1"


def test_resolve_section_refs():
    sections, _cs, chunk_section, refs, summary = layer()
    got = by_source(sections, refs)
    # M1: 章の番号。LLM が選んだ a1（1 章）ではなく 2 章へ
    assert ((A, "2"), "number", None, None) in got["a3"]
    # M2: 文書番号の直後の番号。LLM が選んだ文書 A ではなく、文書番号 HS-VTR-T902 の文書 B の 3-5 へ
    assert ((B, "3-5"), "standard", "HS-VTR-T902", None) in got["a3"]
    # M1: 付属の節（別表2）は題名の先頭で引く。別表1 は節が無いので LLM の結び先の節に、解けなかった語を残す
    assert ((A, "別表2 試験方法"), "number", None, None) in got["a1"]
    assert ((A, "別表2 試験方法"), "chunk", None, "別表1") in got["a2"]
    # 「上記2-1と同じ」の 2-1 は a2 自身の中にある。節のチャンクが参照元だけなので線を張らない（11/9、X20 で誤り判定の 2 本の型）
    assert not any(k == (A, "2-1") for k, *_ in got.get("a2", []))
    assert summary["self_only_skipped"] >= 1
    # 同じ参照（a4 の一文）の結び先 b3・b4 は、既定（まとめ無効）では従来どおりチャンクごとの主所属の節へ
    assert got["a4"] == [((B, "3-5"), "chunk", "HS-VTR-T902", None), ((B, "3-6"), "chunk", "HS-VTR-T902", None)]
    # 号「3)」: 一文に文書番号が無く LLM の文書が別（B）なので、参照元の節には解かない → LLM の結び先（b1 の主所属）
    assert ((B, "1"), "chunk", None, "3)") in got["a1"]
    assert summary["resolved_by_number"] == 4 and summary["resolved_by_standard"] == 1
    assert summary["unresolved_locator"] == 2 and summary["merged_llm_targets"] == 0
    # E10（M3）: 文書層の一文から a3 → B の 3-5。E9 の線と同じなので 1 本
    assert summary["standard_section_refs"] == 1
    assert len([k for k in refs if k[0] == "a3"]) == 2


def test_document_number_overrides_llm_document():
    # 一文に文書番号（B）があり番号が無く、LLM が A のチャンクを選んだ: LLM の文書は使わない（線を作らない）
    links = [("a3", "a1", A, "", "判定はHS-VTR-T902による。")]
    _s, _cs, _c, refs, summary = layer(FakeGraph(links=links, doc_quotes=()))
    assert refs == {} and summary["dropped_by_standard"] == 1
    # 文書層が無い（版が読めない）なら従来どおり LLM の結び先
    _s, _cs, _c, refs, summary = layer(FakeGraph(links=links, versions=(), doc_quotes=()))
    assert list(refs.values())[0]["via"] == "chunk"


def test_document_list_entry_is_left_to_document_layer():
    # 関連基準の一覧の行（文書番号と名称だけ、位置の語も参照の言い回しも無い）を LLM が B の特定のチャンクに結んでも、節には結ばない（11/9）
    links = [("a3", "b2", B, "", "HS-VTR-T902(最新版) 判定基準")]
    _s, _cs, _c, refs, summary = layer(FakeGraph(links=links, doc_quotes=()))
    assert refs == {} and summary["document_list_skipped"] == 1
    # 「…による」と参照の言い回しがあれば従来どおり LLM の結び先の節に結ぶ（文書の中の箇所を選ぶのは LLM の文脈判断）
    links = [("a3", "b2", B, "", "判定方法はHS-VTR-T902(最新版)による。")]
    sections, _cs, _c, refs, summary = layer(FakeGraph(links=links, doc_quotes=()))
    assert [(label(sections, k[1]), v["via"]) for k, v in refs.items()] == [((B, "3-4"), "chunk")] and summary["document_list_skipped"] == 0
    # 「…により」「…に依り」も参照の言い回し
    for q in ("管理カーブはHS-VTR-T902により管理する。", "判定はHS-VTR-T902に依り行う。"):
        _s, _cs, _c, refs, summary = layer(FakeGraph(links=[("a3", "b2", B, "", q)], doc_quotes=()))
        assert len(refs) == 1 and summary["document_list_skipped"] == 0, q
    # 位置の語（3-5項）があれば番号で解く（この規則は掛からない）
    links = [("a3", "b2", B, "3-5項", "HS-VTR-T902(最新版) 3-5項")]
    sections, _cs, _c, refs, summary = layer(FakeGraph(links=links, doc_quotes=()))
    assert [(label(sections, k[1]), v["via"]) for k, v in refs.items()] == [((B, "3-5"), "standard")] and summary["document_list_skipped"] == 0


def test_unknown_document_numbers_are_not_resolved_here():
    # 手元に無い文書の番号の後の番号は、手元の文書の節に解かない
    links = [("a3", "a1", A, "2項", "HS-VTR-T999の2項による。")]
    sections, _cs, _c, refs, summary = layer(FakeGraph(links=links, doc_quotes=()))
    v = list(refs.values())
    assert len(v) == 1 and v[0]["via"] == "chunk" and v[0]["unresolved_locator"] == "2項"
    links = [("a3", "a1", A, "JIS B 8340 3-1", "JIS B 8340 3-1による。")]
    _s, _cs, _c, refs, summary = layer(FakeGraph(links=links, doc_quotes=()))
    assert list(refs.values())[0]["via"] == "chunk" and summary["unresolved_locator"] == 0


def test_item_in_same_section():
    # 号「3)」は参照元の節（2-1）に解ける。ただしこの仮データの 2-1 のチャンクは参照元 a2 だけなので、検索が足すものが無く線は張らない（11/9）
    links = [("a2", "a1", A, "3)", "上記3)による。")]
    sections, _cs, chunk_section, refs, summary = layer(FakeGraph(links=links, doc_quotes=()))
    assert refs == {} and summary["self_only_skipped"] == 1


def test_self_locator_prefers_own_document():
    # 「上記2項」は参照元の文書の 2 章。一文に他の文書の番号（HS-VTR-T902）があっても、その文書の 2 章（B の「2. 確認項目」）に解かない
    q = "上記2項の結果をHS-VTR-T902により管理する。"
    for tgt, tgt_file in (("a1", A), ("b1", B)):   # LLM が自分の文書を選んだ場合も、他の文書を選んだ場合も
        sections, _cs, _c, refs, summary = layer(FakeGraph(links=[("a3", tgt, tgt_file, "上記2項", q)], doc_quotes=()))
        assert [(label(sections, k[1]), v["via"], v.get("standard")) for k, v in refs.items()] == [((A, "2"), "number", None)], (tgt, refs)
        assert summary["dropped_by_standard"] == 0
    # 「上記規格の…」のように番号が続かない「上記」は、自分の文書を指すとはみなさない（一文の文書番号の文書で解く）
    sections, _cs, _c, refs, _ = layer(FakeGraph(links=[("a3", "b1", B, "上記規格の3-5項", "上記規格の3-5項による。HS-VTR-T902")], doc_quotes=()))
    assert [(label(sections, k[1]), v["via"]) for k, v in refs.items()] == [((B, "3-5"), "standard")]


def test_other_version_of_same_number_is_not_dropped():
    # 同じ文書番号 HS-VTR-T902 の旧い版（zz-B-old.md、版 A）を LLM が選んだ: 文書番号で見ると同じ文書なので線を消さない
    old = "zz-B-old.md"
    rows = ROWS + [chunk("o1", old, 1, "[zz-B-old > 1. 適用]\n## 1. 適用\n旧い版の判定基準である。\n## 2. 確認項目\n確認を行う。")]
    versions = ((A, "HS-VTR-T901", "A"), (B, "HS-VTR-T902", "B"), (old, "HS-VTR-T902", "A"))
    links = [("a3", "o1", old, "", "判定はHS-VTR-T902による。")]
    sections, _cs, _c, refs, summary = layer(FakeGraph(links=links, versions=versions, doc_quotes=()), rows=rows)
    assert summary["dropped_by_standard"] == 0
    assert [(label(sections, k[1]), v["via"], v.get("standard")) for k, v in refs.items()] == [((old, "1"), "chunk", "HS-VTR-T902")]
    # 番号があれば、番号は文書層の版（最新の版 B）で解く（M2。LLM が選んだ版は使わない）
    links = [("a3", "o1", old, "3-5項", "判定はHS-VTR-T902の3-5項による。")]
    sections, _cs, _c, refs, _ = layer(FakeGraph(links=links, versions=versions, doc_quotes=()), rows=rows)
    assert [(label(sections, k[1]), v["via"]) for k, v in refs.items()] == [((B, "3-5"), "standard")]


def test_merge_switch_and_idempotent():
    for merge in (False, True):
        _s, _cs, _c, refs1, s1 = layer(merge=merge)
        _s, _cs, _c, refs2, s2 = layer(FakeGraph(links=list(reversed(LINKS))), merge=merge)
        assert refs1 == refs2 and s1 == s2, ("リンクを読む順で結果が変わる", merge)
    assert SL.MERGE_LLM_TARGETS is False, "まとめ（承認済みの M1〜M6 の外）は既定で無効"
    sections, _cs, _c, refs, summary = layer(merge=True)
    # 有効にすると、同じ参照の結び先 b3・b4 を両方含む最も深い節 1 つ（3-5）にまとめる
    assert sorted(label(sections, k[1]) for k in refs if k[0] == "a4") == [(B, "3-5")]
    assert summary["merged_llm_targets"] == 1
