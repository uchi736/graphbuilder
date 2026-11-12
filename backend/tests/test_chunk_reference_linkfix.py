# -*- coding: utf-8 -*-
"""LLM の参照リンクの結び先の選び方（11/10、chunk_references の CHUNK_REFERENCE_LINK_STYLE = outline）の単体試験。DB も LLM も使わない。

確かめること:
- (b) 参照でない文: 参照の言い回しも位置の語も文書の名前も無い一文（「…吸入温度により変化する。」）と見出しの行は参照としない。
  一文の行の続き（「3)…求める。上記例では…」）にある位置の語は数える
- (d) 参照元の中の箇所: 図・表・項の番号が参照元のチャンクの行の頭にすべてあれば結ばない（「(P2/5 図-1 参照)」と「図-1」の題の行）。
  範囲「上記2-1～2-3」は 2-1・2-2・2-3 に広げて判定する
- (c) 社外の文書: 文書番号は文書番号の規則で手元の文書に直す（手元に無い番号は社外）。番号の無い文書の名前（「形式別単独運転要領」）は
  手元の題名に無ければ社外。手元の題名に合う名前・付属の語は LLM に任せる
- (a) 節の一覧: 一覧は「番号 節の経路 ｜ 要点か本文」で、管理の節（来歴）と記述と同じ文言の見出しの節を外し、参照元の節に ★ を付ける。
  位置の語の番号で節が引ければ LLM を呼ばない。選んだ節に主所属するチャンクへ結び、参照元が主所属の節で他に主所属のチャンクが無ければ結ばない
- 後処理の全体（偽のグラフ）: リレーションに選んだ節（target_sections）を書く・参照でない文は書かない・設定を変えた回は抽出を呼ばずに特定だけやり直す・
  legacy で従来の一覧（チャンクの本文の先頭）に戻る
- 章・節の層: LLM が選んだ節をそのまま節への参照の結び先にする（id が変われば経路で引く）。SECTION_LAYER_USE_LLM_SECTIONS=false で従来
実行: docker run --rm --memory=2g --cpus=2 --network none -e PYTHONDONTWRITEBYTECODE=1 -v "$PWD/backend:/code:ro" -w /code \\
        llm-graph-builder-backend:latest python -m pytest -q -p no:cacheprovider tests/test_chunk_reference_linkfix.py
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from src import chunk_references as C  # noqa: E402
from src import document_layer_rules as R  # noqa: E402
from src import section_layer as SL  # noqa: E402

cfg = R.load_config()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("CHUNK_REFERENCE_LINK_STYLE", "CHUNK_REFERENCE_TARGET_LISTING", "CHUNK_REFERENCE_DROP_NON_REFERENCES",
                 "CHUNK_REFERENCE_LEDGER_EXTERNAL", "CHUNK_REFERENCE_SKIP_IN_CHUNK", "CHUNK_REFERENCE_MODE", "SECTION_LAYER_USE_LLM_SECTIONS"):
        monkeypatch.delenv(name, raising=False)


# ───────────────────────── (b) 参照でない文 ─────────────────────────
BODY_T005B_14 = ("#### 3-2 回転誤差計算\n\n試験回転数はサージ,大,中ともに吸入温度により変化する。何処の回転数を基に計算を行なうかであるが、"
                 "エンジン特性曲線にもっとも近い点での回転数を基に計算する事とする。\n\n1)ABBマップに計測点をプロットしカーブを入れる。\n\n"
                 "3)サージラインと合った点でマップ上の回転数Cを求める。上記例ではC≒168.8rpsと読む。")


def test_non_reference_sentences_are_dropped():
    like = lambda q, body=BODY_T005B_14, **kw: C.is_reference_like(dict({"quote": q}, **kw), body, R, cfg)
    assert not like("試験回転数はサージ,大,中ともに吸入温度により変化する。")          # 「により」は原因
    assert not like("何処の回転数を基に計算を行なうかであるが、エンジン特性曲線にもっとも近い点での回転数を基に計算する事とする。")
    assert not like("1)ABBマップに計測点をプロットしカーブを入れる。", target_document="ABBマップ")  # 文書らしくない表記
    assert like("3)サージラインと合った点でマップ上の回転数Cを求める。")                # 同じ行に「上記例」
    assert not like("### 2. 判定基準(ABBマップに対し)", body="### 2. 判定基準(ABBマップに対し)")  # 見出しの行
    for q in ("(P2/5 図-1 参照)", "2-1及び2-2項に示す運転状態にて、次の項目を計測する。", "2-1 N=1台目 ……別表1 符号Aによる。",
              "HS-VTR-T001(最新版) 過給機単独試運転要領及び判定基準", "記録保管は QC-KAI-012により保管管理する。",
              "コンプレッサ性能は形式別単独運転要領の規定チェック回転速度(Nt)×1域のサージ点", "試運転はJG及び各船級協会承認図及び組付指示書に基づき行う。",
              "管理カ-ブはHS VTR-T003により管理する。", "初号機に該当する過給機について、添付資料の要領でABBコンプレッサカーブにより性能確認を実施し"):
        assert like(q, body=q), q
    # 小数（168.3）は位置の語ではない
    assert not like("回転数は168.3rpsとする。", body="回転数は168.3rpsとする。")


# ───────────────────────── (d) 参照元の中の箇所 ─────────────────────────
BODY_T001E_3 = "(2) 性能計測手順は次により実施し、2～3点の計測を行う。\n\n(P2/5 図-1 参照)\n\na.回転上昇させ①の強サージング点を計測\n\n図中の文字: π ① ③\n\n図-1\n\n### 2-2 全力運転試験\n\n過給機の…"


def test_in_source_chunk():
    inside = lambda q, loc, body, **kw: C.in_source_chunk(dict({"quote": q, "locator": loc}, **kw), body)
    assert inside("(P2/5 図-1 参照)", "P2/5 図-1", BODY_T001E_3)                      # 図-1 の題の行が同じチャンクにある
    assert not inside("(P2/5 図-2 参照)", "P2/5 図-2", BODY_T001E_3)
    assert not inside("2-1及び2-2項に示す運転状態にて", "2-1及び2-2項", BODY_T001E_3)    # 2-2 はあるが 2-1 が無い
    assert not inside("(P2/5 図-1 参照)", "図-1", BODY_T001E_3, target_document="HS-VTR-T002")  # 他の文書の図
    assert not inside("上記例では", "上記例", BODY_T001E_3)                            # 番号の無い参照は判定しない
    body = "2-3 n=4台目以降・・別表1 符号Cによる。\n\n但しDU向け過給機については上記2-1～2-3の順序で実施する事。"
    q = "但しDU向け過給機については上記2-1～2-3の順序で実施する事。"
    assert not inside(q, "上記2-1～2-3", body)                                        # 2-1・2-2 は別のチャンク
    assert inside(q, "上記2-1～2-3", "2-1 n=1台目\n2-2 n=2台目\n" + body)              # 範囲の全部が参照元にある


# ───────────────────────── (c) 社外の文書 ─────────────────────────
DOCS = [{"fileName": "HS-VTR-T001E.pdf", "head": "## 過給機単独試運転要領及び判定基準 ## 1. 適 用 この基準は"},
        {"fileName": "VTR-T005B.pdf", "head": "## TPL.TPS形過給機の初号機に関するコンプレッサ単独性能確認試験要領及び判定基準"},
        {"fileName": "VTR-T003D.pdf", "head": "## コンプレッサ性能管理曲線の作成及び管理要領"}]


def test_ledger_document():
    led = lambda q, t=None, loc=None, src="VTR-T005B.pdf": C.ledger_document({"quote": q, "target_document": t, "locator": loc}, src, DOCS, R, cfg)
    assert led("コンプレッサ性能は形式別単独運転要領の規定チェック回転速度(Nt)×1域のサージ点") == ("external", "形式別単独運転要領")
    assert led("HS-VTR-T009による", "HS-VTR-T009") == ("external", "HS-VTR-T009")       # 手元に無い番号
    assert led("管理カ-ブはHS VTR-T003により管理する。", "HS VTR-T003") == ("file", "VTR-T003D.pdf")
    assert led("試運転方法…HS-VTR-T005 による。", "HS-VTR-T005") == ("self", None)       # 参照元自身の番号
    assert led("JIS B 8340に準拠する", "JIS B 8340タ-ボ圧縮機試験方法") == ("external", "JIS B 8340タ-ボ圧縮機試験方法")
    assert led("組付指示書の指示に依り", "組付指示書") == ("external", "組付指示書")
    assert led("過給機単独試運転要領による", "過給機単独試運転要領") is None               # 手元の題名に合う → LLM に任せる
    assert led("別表2による", None, "別表2") is None
    assert led("添付資料の要領で", "添付資料") is None
    assert led("下記要領にて運転を行ない") is None                                       # 「下記要領」は文書の名前ではない
    # 自分の文書の位置の語がある一文は、文書の名前があっても社外と決めない
    assert led("形式別単独運転要領の規定による。詳細は別表2による。") is None


# ───────────────────────── 設定 ─────────────────────────
def test_options(monkeypatch):
    assert C.options_key(C.link_options()) == "v%s-Lo-n1-e1-i1" % C.LINK_VERSION
    monkeypatch.setenv("CHUNK_REFERENCE_LINK_STYLE", "legacy")
    opts = C.link_options()
    assert opts["listing"] == "chunks" and C.options_key(opts) is None                 # 従来の保存と同じ印（無し）
    monkeypatch.setenv("CHUNK_REFERENCE_SKIP_IN_CHUNK", "true")                         # 個別の設定は LINK_STYLE より優先
    assert C.options_key(C.link_options()) == "v%s-Lc-n0-e0-i1" % C.LINK_VERSION
    monkeypatch.setenv("CHUNK_REFERENCE_LINK_STYLE", "unknown")
    assert C.link_options()["style"] == "outline"


# ───────────────────────── (a) 節の一覧 ─────────────────────────
SECTIONS = [
    {"id": "s1", "fileName": "B.md", "path": ["1 適用"], "title": "適用", "level": 1, "first_position": 1, "offset": 0, "form": "章", "number": "1",
     "summary": "TPL形過給機の初号機の単独性能の確認に適用する。", "text": "## 1. 適用\n本文", "control": False, "parent": None},
    {"id": "s2", "fileName": "B.md", "path": ["2 単独性能確認試験要領"], "title": "単独性能確認試験要領", "level": 1, "first_position": 2, "offset": 0,
     "form": "章", "number": "2", "summary": None, "text": "## 2. 単独性能確認試験要領\n組立部門は下記要領にて運転を行う。", "control": False, "parent": None},
    {"id": "s25", "fileName": "B.md", "path": ["2 単独性能確認試験要領", "2-5 運転条件"], "title": "運転条件", "level": 2, "first_position": 3, "offset": 0,
     "form": "節", "number": "2-5", "summary": None, "text": "性能計測時の運転条件", "control": False, "parent": "s2"},
    {"id": "h", "fileName": "B.md", "path": ["7 基準来歴"], "title": "基準来歴", "level": 1, "first_position": 4, "offset": 0, "form": "章", "number": "7",
     "summary": None, "text": "改訂", "control": True, "parent": None},
    {"id": "a2", "fileName": "B.md", "path": ["別表2 過給機性能試験及び性能換算法"], "title": "別表2 過給機性能試験及び性能換算法", "level": 1,
     "first_position": 5, "offset": 0, "form": "付属", "number": None, "summary": "空気量と圧力比の換算式を定める。", "text": "", "control": False, "parent": None},
    {"id": "x1", "fileName": "B.md", "path": ["添付資料 ABB方式", "1 試運転方法及び性能換算方法・・・・HS-VTR-T905による"],
     "title": "試運転方法及び性能換算方法・・・・HS-VTR-T905による", "level": 2, "first_position": 6, "offset": 0, "form": "章", "number": "1",
     "summary": None, "text": "", "control": False, "parent": None},
]
MEMBERS = {"s1": [("b1", True)], "s2": [("b2", True)], "s25": [("b2", False), ("b3", True)], "h": [("b4", True)], "a2": [("b5", True)], "x1": [("b6", True)]}
POS = {"b1": 1, "b2": 2, "b3": 3, "b4": 4, "b5": 5, "b6": 6}


def outline():
    return C._Outline([dict(s) for s in SECTIONS], MEMBERS, POS)


def test_outline_listing_and_mapping():
    prompts = []
    ask = lambda p: (prompts.append(p), "4")[1]   # 一覧は 1 適用・2 要領・2-5・別表2・添付資料（来歴は外す）
    src = {"id": "a9", "fileName": "A.md", "text": "[A > 3. 判定]\na.計算式はJISに準拠する。\n但しTPL形はHS-VTR-T905による。", "section": None}
    ref = {"quote": "但しTPL形はHS-VTR-T905による。", "locator": None, "topic": "TPL形の計算式", "target_document": "HS-VTR-T905"}
    targets, chosen, by_chunk = C.resolve_sections(ref, src, "B.md", outline(), {}, ask, None)
    p = prompts[0]
    assert "[1] 1 適用 ｜ 要点: TPL形過給機の初号機" in p                       # 要点があれば要点
    assert "[2] 2 単独性能確認試験要領 ｜ 本文: 組立部門は下記要領にて運転を行う。" in p   # 無ければ本文の先頭（見出しの行を除く）
    assert "基準来歴" not in p                                                  # 管理の節は出さない
    assert "参照元の節: A > 3. 判定" in p and "a.計算式はJISに準拠する。" in p        # 参照元の節と前の本文
    assert "★" not in p                                                        # 他の文書なので参照元の印は無い
    assert [c["id"] for c in targets] == ["b5"] and chosen == [{"id": "a2", "path": "別表2 過給機性能試験及び性能換算法"}]
    assert by_chunk == {"b5": ["a2"]}
    # 記述と同じ文言の見出しの節（同じ参照の繰り返し）は一覧から外す
    prompts.clear()
    ref2 = {"quote": "1.試運転方法及び性能換算方法・・・・HS-VTR-T905による", "locator": None, "topic": "", "target_document": "HS-VTR-T905"}
    C.resolve_sections(ref2, src, "B.md", outline(), {}, ask, None)
    assert "試運転方法及び性能換算方法・・・・HS-VTR-T905による ｜" not in prompts[0] and "[4] 別表2" in prompts[0]
    # 題名が参照先の文書自身の番号を名指ししている節（案内の行）は一覧から外す（記述の文言が違っても）
    prompts.clear()
    C.resolve_sections(ref, src, "HS-VTR-T905X.pdf", outline(), {}, ask, None)
    assert "HS-VTR-T905による ｜" not in prompts[0] and "[4] 別表2" in prompts[0]
    prompts.clear()
    C.resolve_sections(ref, src, "B.md", outline(), {}, ask, None)
    assert "[5] 添付資料 ABB方式 > 1 試運転方法及び性能換算方法・・・・HS-VTR-T905による ｜" in prompts[0]   # 番号の無い文書では外さない
    # 0（文書を名指ししているだけ）は結び先なし
    assert C.resolve_sections(ref, src, "B.md", outline(), {}, lambda p: "0", None) == ([], [], {})
    # 読めない答えは失敗（既存のリンクを残して次回に回す）
    with pytest.raises(ValueError):
        C.resolve_sections(ref, src, "B.md", outline(), {}, lambda p: "別表2 です", None)


def test_outline_same_document_and_numbers():
    called = []
    ask = lambda p: (called.append(p), "3")[1]
    # 同じ文書: 参照元の節に ★。参照元（b3）が主所属の節 2-5 だけを選んだ場合、他に主所属のチャンクが無いので結ばない（「前項」「表1」が同じチャンクにある型）
    src = {"id": "b3", "fileName": "B.md", "text": "### 2-5 運転条件\n1)…\n2)前項の圧力から表1の圧力まで上げる。", "section": None}
    ref = {"quote": "2)前項の圧力から表1の圧力まで上げる。", "locator": "前項", "topic": ""}
    targets, chosen, _ = C.resolve_sections(ref, src, "B.md", outline(), {}, ask, None)
    assert "2-5 運転条件 ★" in called[0] and OUTLINE_SOURCE_NOTE_IN(called[0])
    assert targets == [] and chosen == []
    # 位置の語の番号で節が引ける参照は LLM を呼ばない（章・節の層の番号の規則と同じ）。番号で決めた節は、節に掛かる他のチャンクにも結ぶ
    called.clear()
    targets, chosen, by_chunk = C.resolve_sections({"quote": "上記2-5による", "locator": "上記2-5"}, {"id": "b3", "fileName": "B.md", "text": ""},
                                                   "B.md", outline(), {}, ask, None)
    assert not called and [c["id"] for c in targets] == ["b2"] and chosen[0]["id"] == "s25"
    targets, chosen, _ = C.resolve_sections({"quote": "別表2による", "locator": "別表2"}, {"id": "a9", "fileName": "A.md", "text": ""}, "B.md", outline(), {}, ask, None)
    assert not called and [c["id"] for c in targets] == ["b5"] and chosen[0]["id"] == "a2"
    # 引けない番号（図の中の点の番号など）は LLM に選ばせる
    C.resolve_sections({"quote": "3の点の回転数", "locator": "3の点"}, {"id": "a9", "fileName": "A.md", "text": ""}, "B.md", outline(), {}, ask, None)
    assert called


def OUTLINE_SOURCE_NOTE_IN(prompt):
    return C.OUTLINE_SOURCE_NOTE in prompt


# ───────────────────────── 後処理の全体（偽のグラフ）─────────────────────────
def crow(cid, f, pos, text, stored=None, links=0):
    return {"id": cid, "text": text, "position": pos, "fileName": f, "page_number": None, "section": None, "external": None,
            "stored": stored, "unresolved": False, "links": links, "rule_links": 0}


A_TEXT = ("[A > 3. 判定]\n### 3-1 計算\na.空気量の計算式はJISに準拠する。\n但しTPL形過給機についてはHS-VTR-T902による。\n"
          "試験回転数は吸入温度により変化する。\nコンプレッサ性能は形式別単独運転要領の規定回転速度で計測する。")
ROWS = [crow("a1", "HS-VTR-T901A.pdf", 1, A_TEXT),
        crow("b1", "HS-VTR-T902B.pdf", 1, "[B > 1. 適用]\n## 1. 適用\nTPL形に適用する。"),
        crow("b5", "HS-VTR-T902B.pdf", 5, "[B > 別表2]\n## 別表2 過給機性能試験及び性能換算法\n空気量と圧力比の換算式。")]
B_SECTIONS = [
    {"id": "t1", "fileName": "HS-VTR-T902B.pdf", "path": ["1 適用"], "title": "適用", "level": 1, "first_position": 1, "offset": 0, "summary": None,
     "text": "TPL形に適用する。", "control": False, "parent": None, "form": "章", "number": "1"},
    {"id": "t2", "fileName": "HS-VTR-T902B.pdf", "path": ["別表2 過給機性能試験及び性能換算法"], "title": "別表2 過給機性能試験及び性能換算法", "level": 1,
     "first_position": 5, "offset": 0, "summary": "空気量と圧力比の換算式", "text": "", "control": False, "parent": None, "form": "付属", "number": None}]
EXTRACTION = {"references": [
    {"quote": "a.空気量の計算式はJISに準拠する。", "target_document": "JIS", "locator": None, "topic": "計算式"},
    {"quote": "但しTPL形過給機についてはHS-VTR-T902による。", "target_document": "HS-VTR-T902", "locator": None, "topic": "TPL形の計算式"},
    {"quote": "試験回転数は吸入温度により変化する。", "target_document": None, "locator": None, "topic": "回転数"},
    {"quote": "コンプレッサ性能は形式別単独運転要領の規定回転速度で計測する。", "target_document": None, "locator": None, "topic": "回転速度"}]}


class FakeGraph:
    def __init__(self, rows, sections=B_SECTIONS, members=(("b1", "t1", True), ("b5", "t2", True))):
        self.rows, self.sections, self.members = rows, sections, members
        self.writes = []

    def query(self, cypher, params=None):
        if cypher.startswith("CREATE INDEX"):
            return []
        if "MATCH (c:Chunk)-[:PART_OF]->(d:Document)" in cypher and "RETURN c.id AS id" in cypher:
            return [dict(r) for r in self.rows]
        if cypher.startswith("MATCH (s:Section) WHERE s.method = 'rule'"):
            return [dict(s) for s in self.sections]
        if "IN_SECTION" in cypher and cypher.startswith("MATCH (c:Chunk)-[r:IN_SECTION]"):
            return [{"chunk": c, "section": s, "primary": p} for c, s, p in self.members]
        self.writes.append((cypher, params or {}))
        return []

    def rows_written(self, marker):
        return [row for cypher, p in self.writes if marker in cypher for row in (p.get("rows") or [])]


def fake_ask(log):
    def ask(prompt):
        log.append(prompt)
        if prompt.startswith("次の本文は"):
            return json.dumps(EXTRACTION, ensure_ascii=False)
        if "節の内容の説明" in prompt:
            return "2"   # 別表2
        if "文書片" in prompt:
            return "2"
        if "この記述の中の「HS-VTR-T902」" in prompt:
            return "1"   # 従来（legacy）は文書番号も LLM が選ぶ。候補は HS-VTR-T902B.pdf だけ
        if "この記述の中の「" in prompt:
            return "0"   # 「JIS」だけの表記は文書番号の規則でも題名でも決まらず、従来どおり LLM が手元に無いと答える
        raise AssertionError("想定外の指示: " + prompt[:40])
    return ask


def test_link_chunk_references_outline():
    graph, log = FakeGraph(ROWS), []
    res = C.link_chunk_references(graph, ask=fake_ask(log), rerank=lambda q, t: [0.0] * len(t))
    chunk_rows = graph.rows_written("(t:Chunk {id: row.target})")
    assert [(r["source"], r["target"], r["sections"]) for r in chunk_rows] == [("a1", "b5", ["t2"])]
    assert chunk_rows[0]["paths"] == ["別表2 過給機性能試験及び性能換算法"]
    assert not any("文書「HS-VTR-T901A.pdf」に次の記述がある。\n\n記述: 試験回転数" in p for p in log)   # 参照でない文は特定に回さない
    assert not any("この記述の中の「HS-VTR-T902」" in p for p in log)                    # 文書番号は規則で決まり、文書の特定の指示は使わない
    summary = res["summary"]["link_style"]
    assert summary["not_reference"] == 1 and summary["external_by_ledger"] == 1 and summary["document_by_ledger"] == 1 and summary["outline"] == 1
    final = graph.rows_written("c.external_references = CASE")
    stored = json.loads([r for r in final if r["id"] == "a1"][0]["stored"])
    assert stored["opts"] == C.options_key(C.link_options())
    assert {r["scope"] for r in stored["refs"]} == {"external", "other_document", "not_reference"}
    assert "形式別単独運転要領" in [r for r in final if r["id"] == "a1"][0]["external"]

    # 設定を変えた回（legacy）: 保存した抽出を使い、抽出は呼ばずに特定だけやり直す。一覧はチャンクの本文の先頭（従来）
    os.environ["CHUNK_REFERENCE_LINK_STYLE"] = "legacy"
    try:
        saved = {r["id"]: r["stored"] for r in final}
        rows2 = [dict(r, stored=saved[r["id"]]) for r in ROWS]
        graph2, log2 = FakeGraph(rows2), []
        C.link_chunk_references(graph2, ask=fake_ask(log2), rerank=lambda q, t: [0.0] * len(t))
        assert not any(p.startswith("次の本文は") for p in log2)
        assert any("文書片" in p for p in log2) and not any("節の内容の説明" in p for p in log2)
        rows_legacy = graph2.rows_written("(t:Chunk {id: row.target})")
        assert rows_legacy and all(r["sections"] == [] for r in rows_legacy)
    finally:
        del os.environ["CHUNK_REFERENCE_LINK_STYLE"]


def test_outline_without_section_layer_uses_heading_paths():
    rows = [crow("a1", "HS-VTR-T901A.pdf", 1, A_TEXT)] + [
        dict(crow("b1", "HS-VTR-T902B.pdf", 1, "[B > 1. 適用]\n## 1. 適用\nTPL形に適用する。"), section="HS-VTR-T902B > 1. 適用"),
        dict(crow("b5", "HS-VTR-T902B.pdf", 5, "[B > 別表2]\n## 別表2 換算法\n換算式。"), section="HS-VTR-T902B > 別表2 換算法")]
    graph, log = FakeGraph(rows, sections=[], members=()), []
    C.link_chunk_references(graph, ask=fake_ask(log), rerank=lambda q, t: [0.0] * len(t))
    listing = [p for p in log if "節の内容の説明" in p][0]
    assert "[1] 1. 適用 ｜ 本文: TPL形に適用する。" in listing and "[2] 別表2 換算法" in listing
    chunk_rows = graph.rows_written("(t:Chunk {id: row.target})")
    assert [(r["target"], r["paths"]) for r in chunk_rows] == [("b5", ["別表2 換算法"])]


# ───────────────────────── 章・節の層 ─────────────────────────
SA, SB = "zz-A.md", "zz-B.md"
SL_ROWS = [
    {"id": "a1", "fileName": SA, "position": 1, "section": "zz-A > 1. 適用", "section_path": None, "page_number": None,
     "text": "[zz-A > 1. 適用]\n## 1. 適用\n但しTPL形はHS-VTR-T902による。"},
    {"id": "b1", "fileName": SB, "position": 1, "section": "zz-B > 3. 換算", "section_path": None, "page_number": None,
     "text": "[zz-B > 3. 換算]\n## 3. 換算\n換算を行う。\n### 3-1 空気量\n空気量の式。"},
    {"id": "b2", "fileName": SB, "position": 2, "section": "zz-B > 3. 換算 > 3-2 圧力比", "section_path": None, "page_number": None,
     "text": "[zz-B > 3. 換算 > 3-2 圧力比]\n### 3-2 圧力比\n圧力比の式。"},
]


class SLGraph:
    def __init__(self, links):
        self.links = links

    def query(self, cypher, params=None):
        if "IS_VERSION_OF" in cypher or "r.quotes AS quotes" in cypher:
            return []
        if "WHERE b:Chunk OR b:Document" in cypher:
            return [dict(l) for l in self.links]
        raise AssertionError(cypher[:60])


def sl_layer(links):
    sections, chunk_sections = SL.sections_from_catalogue(SL_ROWS, cfg)
    chunk_section = {cid: next((sid for sid, p in lst if p), lst[0][0]) for cid, lst in chunk_sections.items()}
    refs, summary = SL.resolve_section_refs(SLGraph(links), SL_ROWS, sections, chunk_sections, chunk_section, cfg)
    return sections, refs, summary


def test_section_layer_uses_llm_sections(monkeypatch):
    sections, _r, _s = sl_layer([])
    sid3 = next(s for s, v in sections.items() if v["fileName"] == SB and v["number"] == "3")
    sid32 = next(s for s, v in sections.items() if v["fileName"] == SB and v["number"] == "3-2")
    base = {"src": "a1", "tgt_file": SB, "locator": "", "quote": "但しTPL形はHS-VTR-T902による。", "method": "llm"}
    # LLM が 3 を選び、3 に主所属するチャンク b1 と下位の 3-2 の b2 へ結んだ: 節への参照は 3 の 1 本（チャンクごとの主所属 3・3-2 に割れない）
    links = [dict(base, tgt="b1", target_sections=[sid3], target_section_paths=["3 換算"]),
             dict(base, tgt="b2", target_sections=[sid3], target_section_paths=["3 換算"])]
    _s, refs, summary = sl_layer(links)
    assert list(refs) == [("a1", sid3)] and refs[("a1", sid3)]["via"] == "chunk" and summary["llm_section_targets"] == 1
    # id が変わった（目録が変わった）ときは経路で引き直す
    _s, refs, _ = sl_layer([dict(base, tgt="b1", target_sections=["old-id"], target_section_paths=[" > ".join(sections[sid3]["path"])])])
    assert list(refs) == [("a1", sid3)]
    # 従来（設定で無効）: チャンクごとの主所属の節
    monkeypatch.setenv("SECTION_LAYER_USE_LLM_SECTIONS", "false")
    _s, refs, summary = sl_layer(links)
    assert sorted(k[1] for k in refs) == sorted({sid3, sid32}) and summary["llm_section_targets"] == 0
