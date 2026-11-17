# -*- coding: utf-8 -*-
"""文書台帳（11/10）の試験: 名寄せの漏れ（外部の識別子・空白の違い）、略称・旧名称・旧番号・種類・制定日、現行版、正本の表、参照の一文の解決の内訳。
グラフにも DB にもつながない（plan_document_layer と規則の関数だけ）。架空の文書と 56 本の書き出しがあれば、件数も確かめる。
実行: cd backend && python3 -m pytest tests/test_document_registry.py -q"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from src import document_layer_rules as R  # noqa: E402
from src import document_registry as REG  # noqa: E402
from src.document_layer import pick_external_ids, plan_document_layer  # noqa: E402

cfg = R.load_config()
KG = os.path.join(os.path.dirname(os.path.dirname(HERE)), "tools", "kg")


def ch(f, pos, text):
    return {"id": "%s#%d" % (f, pos), "fileName": f, "position": pos, "text": text, "section": None}


ROWS = [
    ch("HS-VTR-T001D.pdf", 1, "[HS-VTR-T001D]\n## 過給機単独試運転要領及び判定基準\n\n## 1. 適用\nこの基準は単独試運転に適用する。\n\n## 3. 試運転\n### 3-5 運転条件\n潤滑油は標準とする。"),
    ch("HS-VTR-T001D.pdf", 2, "## 5. 基準来歴\n0改正 S59.3.26 新規作成 担当 品管部\nA改正 98.5.9 全面見直し\nD改正 02.08.19 一部見直し"),
    ch("HS-VTR-T001E.pdf", 1, "[HS-VTR-T001E]\n## 過給機単独試運転要領及び判定基準\n\n## 1. 適用\nこの基準は単独試運転に適用する。\n\n## 3. 試運転\n### 3-5 運転条件\n潤滑油は標準とする。"),
    ch("HS-VTR-T001E.pdf", 2, "## 5. 基準来歴\n0改正 S59.3.26 新規作成 担当 品管部\nE改正 03.06.06 一部見直し"),
    ch("HS-VTR-T002F.pdf", 1, "[HS-VTR-T002F]\n## コンプレッサ単独性能確認試験要領及び判定基準\n\n## 1. 適用\nこの基準（以下「試験基準」という。）は試験に適用する。\n\n"
                              "## 2. 試験\n性能計測時の過給機運転条件は、HS-VTR-T001の3-5項に依る。\n計測は『コンプレッサ性能計測標準試験方案;V-00862』による。\n"
                              "差圧はHZTL 20559の値を使用する。\n組立は組付指示書に基づき行う。\n記録は文書管理規程による。\n単独運転装置（以下「運転台」という。）を用いる。"),
]


# ---- 1. 名寄せの漏れ（11/10）----
def test_external_key_and_ids():
    assert R.external_key("V-00862") == R.external_key("V00862") == "V00862"
    assert R.external_key("HZTL 20559") == R.external_key("HZTL20559")
    assert R.external_key("JIS H 5202") == R.external_key("JIS-H5202") == R.external_key("JISH5202")
    assert R.external_key("JIS Z 2201") != R.external_key("JIS Z 2241")
    # 表示形（id）は従来どおり（JIS の空白は残す）
    assert R.external_id("V-00862") == "V00862" and R.external_id("JIS B 8340") == "JIS B 8340"
    # 既存の id が 1 つならそれを選ぶ（作り直しても id を変えない）。無ければ多い表記、同じなら長い表記
    assert pick_external_ids({"HZTL 20559": 1, "HZTL20559": 1}, existing=["HZTL20559"]) == {"HZTL20559": "HZTL20559"}
    assert pick_external_ids({"HZTL 20559": 1, "HZTL20559": 1}) == {"HZTL20559": "HZTL 20559"}
    assert pick_external_ids({"HZTL 20559": 1, "HZTL20559": 3}) == {"HZTL20559": "HZTL20559"}
    assert pick_external_ids({"HZTL 20559": 1, "HZTL20559": 1}, existing=["HZTL 20559", "HZTL20559"]) == {"HZTL20559": "HZTL 20559"}


def test_merge_external_identifier_entities_and_rule_variants():
    ents = [{"id": "V-00862", "labels": ["__Entity__", "Standard"], "eid": "e1", "description": None},
            {"id": "V-02313", "labels": ["__Entity__", "Standard"], "eid": "e2", "description": None},
            {"id": "QC-KAI-005.QC-KAI-018.QC-KAI-019", "labels": ["__Entity__", "Standard"], "eid": "e3", "description": None},
            {"id": "VTR-T001", "labels": ["__Entity__", "Standard"], "eid": "e4", "description": None}]
    rows = ROWS + [ch("VTR-T005B.pdf", 1, "[VTR-T005B]\n## 試験要領\n\n## 1. 適用\n計測は『TPL.TPS形過給機 コンプレッサ性能計測標準試験方案;V02313』による。\n"
                                          "ΔPKA:HZTL20559の値を使用する。")]
    rule_std = [{"id": "HZTL 20559", "eid": "r1", "aliases": [], "external": True}, {"id": "HZTL20559", "eid": "r2", "aliases": [], "external": True},
                {"id": "V00862", "eid": "r3", "aliases": [], "external": True}]
    p = plan_document_layer(rows, cfg, entities=ents, rule_standards=rule_std)
    plan = {k: sorted(e["id"] for e in v) for k, v in p["merge_plan"].items()}
    assert plan["V00862"] == ["V-00862"]
    assert plan["V02313"] == ["V-02313"]
    assert plan["HS-VTR-T001"] == ["VTR-T001"]
    # HZTL は 1 つだけ残る（既存が 2 つ → 多い表記、同じなら長い表記の「HZTL 20559」）。もう一方は寄せる
    assert "HZTL 20559" in p["standards"] and "HZTL20559" not in p["standards"]
    assert plan["HZTL 20559"] == ["HZTL20559"]
    assert "HZTL20559" in p["standards"]["HZTL 20559"]["aliases"]
    assert not any("QC-KAI-005.QC" in e["id"] for v in p["merge_plan"].values() for e in v)   # 複合の語は寄せない
    assert p["summary"]["merge_rule_variants"] == 1


# ---- 2. 略称・旧名称・旧番号・種類・制定日 ----
def test_kind_and_dates():
    assert R.document_kind("過給機単独試運転要領及び判定基準") == "基準"
    assert R.document_kind("コンプレッサ性能計測標準試験方案") == "方案"
    assert R.document_kind("検査記録及び帳票管理規定") == "規定"
    assert R.document_kind("組付指示書", cfg) == "指示書"
    assert R.document_kind("規格値の一覧について") is None
    assert R.is_self_reference("この基準") and R.is_self_reference("本規程") and not R.is_self_reference("この装置")
    assert R.iso_date("H6. 8. 4") == "1994-08-04" and R.iso_date("S59.3.26") == "1984-03-26" and R.iso_date("'07.06.11") == "2007-06-11"
    assert R.iso_date("98.10.21") == "1998-10-21" and R.iso_date("2006.06.10") == "2006-06-10" and R.iso_date("xx") is None
    assert R.rev_rank(None) == R.rev_rank("0") == 0 < R.rev_rank("A") < R.rev_rank("F")
    assert R.parse_enacted("## 6. 基準来歴\n制定: 59．1．11 新規制定") == "1984-01-11"
    assert R.parse_enacted("1)新規制定 ;’01.06.04 担当") == "2001-06-04"
    assert R.parse_enacted("- 0: 1996.03.20 新規制定（過検基 12 号）。") == "1996-03-20"


def test_abbreviations_document_vs_term():
    a = R.parse_abbreviations("この基準（以下「試験基準」という。）は試験に適用する。単独運転装置（以下「運転台」という。）を用いる。", cfg)
    assert {"antecedent": "この基準", "abbr": "試験基準", "how": "definition"} in a
    assert any(x["abbr"] == "運転台" and x["antecedent"] == "単独運転装置" for x in a)
    b = R.parse_abbreviations("本文で略して呼ぶ場合がある。例えば KS-TC-P001 は「計測共通」、QK-TC-01 は「帳票規定」と呼ぶことがある。", cfg)
    assert {(x["antecedent"], x["abbr"]) for x in b} >= {("KS-TC-P001", "計測共通"), ("QK-TC-01", "帳票規定")}
    c = R.parse_abbreviations("| 主管部門 | 検査課 |\n| 旧番号 | 過検基 15 号 |\n| 通称 | 計測共通 |", cfg, cover=True)
    assert {"antecedent": "", "abbr": "計測共通", "how": "cover"} in c
    d = R.parse_abbreviations("この基準は、通称を「計測共通」といい、他の基準類でこの名で引かれることがある。", cfg)
    assert any(x["antecedent"] == "この基準" and x["abbr"] == "計測共通" for x in d)
    e = R.parse_abbreviations("ここに述べる手順を、以下、乾燥手順と称する。", cfg)
    assert any(x["abbr"] == "乾燥手順" and x["how"] == "say" for x in e)


def test_former_names_and_numbers():
    t = "| 旧番号 | 現行の番号 |\n|---|---|\n| 過検基 12 号 | KS-TC-T001 |\n| 品規 7 号 | QK-TC-01 |\n"
    f = R.parse_former_names(t, cfg)
    assert [(x["former"], x["target"]) for x in f] == [("過検基 12 号", "KS-TC-T001"), ("品規 7 号", "QK-TC-01")]
    h = "## 7. 基準来歴\n1. S63.03.26 新規作成 担当 品管部\n   HS-RH-014A を全面見直しの上、調整した。"
    assert R.parse_former_names(h, cfg, own_number="HS-RU-007", in_history=True) == [{"former": "HS-RH-014", "kind": "number", "target": None, "how": "revised"}]
    assert R.parse_former_names(h, cfg, own_number="HS-RU-007", in_history=False) == []   # 来歴の外では読まない
    r = R.parse_former_names("C改正 02.08.19 一部見直し\n・名称変更 (ブロワ-からコンプレッサに変更)", cfg, in_history=True)
    assert r == [{"former": "ブロワー", "kind": "rename_word", "target": None, "how": "rename_word", "new_word": "コンプレッサ"}]
    s = R.parse_former_names("- 0: 1998.07.10 新規制定（過検基 15 号）。担当 検査課", cfg, own_number="KS-TC-P001", in_history=True)
    assert s == [{"former": "過検基 15 号", "kind": "number", "target": None, "how": "established"}]
    n = R.parse_former_names("旧名称「過給機ブロワー性能管理基準」から改称した。", cfg, in_history=True)
    assert {x["former"] for x in n} == {"過給機ブロワー性能管理基準"}


def test_name_near_identifier():
    assert R.name_near_identifier("QC-KAI-027初品管理規定の初品の分類に定める", "QC-KAI-027") == "初品管理規定"
    assert R.name_near_identifier("『コンプレッサ性能計測標準試験方案;V00862』に基き", "V00862") == "コンプレッサ性能計測標準試験方案"
    assert R.name_near_identifier("JIS B 8340「ターボ形ブロワ・圧縮機の試験及び検査方法」により", "JIS B 8340") == "ターボ形ブロワ・圧縮機の試験及び検査方法"
    assert R.name_near_identifier("JIS G 4052 及び注文要領による", "JIS G 4052") is None


def test_plan_ledger_fields():
    p = plan_document_layer(ROWS, cfg)
    st = p["standards"]
    assert st["HS-VTR-T002"]["abbreviations"] == {"試験基準"}          # この基準（以下「試験基準」という。）
    assert "運転台" not in {a for s in st.values() for a in s["abbreviations"]}   # 用語の定義は文書の略称にしない
    assert p["summary"]["ledger_fields"]["abbr_terms_skipped"] == 1
    assert st["HS-VTR-T001"]["kind"] == "基準" and st["HS-VTR-T001"]["official_name"] == "過給機単独試運転要領及び判定基準"
    assert st["HS-VTR-T001"]["enacted"] == "1984-03-26"
    assert st["V00862"]["official_name"] == "コンプレッサ性能計測標準試験方案" and st["V00862"]["kind"] == "方案"


# ---- 3. 現行版 ----
def test_current_version():
    p = plan_document_layer(ROWS, cfg)
    assert p["current"]["HS-VTR-T001"] == {"file": "HS-VTR-T001E.pdf", "rev": "E", "up_to_date": True}
    assert p["current"]["HS-VTR-T002"]["file"] == "HS-VTR-T002F.pdf"     # 版が 1 つでもその版を現行版にする
    assert p["summary"]["current_versions"] == 2
    # 2 つの版があると、規則は「HS-VTR-T001」を決められない（目録の識別子が 2 文書に当たる）。文書層は文書番号で結ぶ
    assert ("HS-VTR-T002F.pdf", "HS-VTR-T001") in p["doc_refs"]
    assert p["doc_refs"][("HS-VTR-T002F.pdf", "HS-VTR-T001")]["via"] == {"number"}
    # 目録が新しい版を知っていて手元に無い: up_to_date = False
    rows = ROWS + [ch("VTR-T_発行目録.pdf", 1, "発行目録\nHS-VTR-T001 / 過給機単独試運転要領及び判定基準 / 59.3.26 / A / 98.5.9 / E / 03.06.06 / F / 05.01.01 / G")]
    p = plan_document_layer(rows, cfg)
    assert p["current"]["HS-VTR-T001"] == {"file": "HS-VTR-T001E.pdf", "rev": "E", "up_to_date": False}
    assert p["standards"]["HS-VTR-T001"]["latest_rev"] == "F" and p["summary"]["current_not_latest"] == 1


def test_retrieval_queries_prefer_current_version():
    with open(os.path.join(os.path.dirname(HERE), "src", "rerank_retriever.py"), encoding="utf-8") as f:
        src = f.read()
    q = src.split("DOCUMENT_LAYER_REFERENCE_QUERY = \"\"\"", 1)[1].split("\"\"\"", 1)[0]
    assert "HAS_CURRENT_VERSION]->(d2)" in q and "NOT EXISTS { MATCH (st)-[:HAS_CURRENT_VERSION]->(:Document) }" in q
    with open(os.path.join(os.path.dirname(HERE), "src", "document_replace.py"), encoding="utf-8") as f:
        assert "REPLACES|HAS_CURRENT_VERSION]->(d:Document)" in f.read()


# ---- 4. 正本の表 ----
REGISTRY = [
    {"doc_number": "HS-VTR-T001", "official_name": "過給機 単独試運転要領", "kind": "要領", "department": "品質保証部 検査課",
     "current_rev": "D", "aliases": ["単独試運転要領"], "parent_doc": None, "note": "人が直した", "updated_by": "uchida", "updated_at": "2026-11-10 10:00:00+09"},
    {"doc_number": "QC-BUN-001", "official_name": "文書管理規程", "kind": None, "department": None, "current_rev": None,
     "aliases": ["組付指示書"], "parent_doc": None, "note": None, "updated_by": "uchida", "updated_at": None},
    {"doc_number": "v-00862", "official_name": None, "kind": None, "department": "設計部", "current_rev": None, "aliases": [], "parent_doc": None,
     "note": None, "updated_by": "seed:graph", "updated_at": None},
]


def test_registry_overrides_graph_values():
    p = plan_document_layer(ROWS, cfg, registry=REGISTRY)
    st = p["standards"]
    t1 = st["HS-VTR-T001"]
    assert t1["official_name"] == "過給機 単独試運転要領" and t1["kind"] == "要領"      # 表の値が文書の値より優先
    assert p["current"]["HS-VTR-T001"] == {"file": "HS-VTR-T001D.pdf", "rev": "D", "up_to_date": True}   # 表の現行版
    assert st["V00862"]["registry"]["department"] == "設計部"                          # 表の番号の表記ゆれも正規形で引く
    assert st["V00862"]["official_name"] == "コンプレッサ性能計測標準試験方案"        # 表が空の項目は文書の値
    assert "QC-BUN-001" in st and st["QC-BUN-001"]["external"] and p["summary"]["registry_created"] == 1   # 表だけにある文書
    # 表の別名・正式名称で、台帳に無かった名称の参照が解決する
    res = p["summary"]["resolution"]
    assert res["unmatched_name"] == 0 and res["resolved_alias"] == 2
    assert p["doc_refs"][("HS-VTR-T002F.pdf", "QC-BUN-001")]["via"] == {"alias"}
    assert p["summary"]["registry_rows"] == 3


def test_registry_row_written_to_standard():
    from src.document_layer import _standard_row
    p = plan_document_layer(ROWS, cfg, registry=REGISTRY)
    row = _standard_row("HS-VTR-T001", p["standards"]["HS-VTR-T001"])
    assert row["department"] == "品質保証部 検査課" and row["registry_aliases"] == ["単独試運転要領"] and row["registry"] is True
    assert row["registry_updated_by"] == "uchida" and row["latest_rev"] == "D"
    row = _standard_row("HS-VTR-T002", p["standards"]["HS-VTR-T002"])
    assert row["registry"] is False and row["department"] is None and row["abbreviations"] == ["試験基準"]


def test_load_registry_off_and_failures(monkeypatch):
    monkeypatch.delenv("DOCUMENT_REGISTRY_DSN", raising=False)
    assert REG.load_registry() == (None, "off")

    def boom():
        raise RuntimeError('connection failed: password=hunter2 for "postgresql://u:hunter2@h:5432/d"')
    rows, status = REG.load_registry(dsn="postgresql://u:hunter2@h:5432/d", connect=boom)
    assert rows is None and status.startswith("unavailable: RuntimeError") and "hunter2" not in status


def test_load_registry_rows():
    class Cur:
        def execute(self, sql):
            assert sql.startswith("SELECT doc_number, official_name") and sql.endswith("FROM document_registry")

        def fetchall(self):
            return [("HS-VTR-T001", "名称", "基準", None, "E", ["a", "", None], None, None, "seed:graph", "2026-11-10")]

    class Conn:
        def cursor(self):
            return Cur()

        def close(self):
            pass
    rows, status = REG.load_registry(dsn="x", connect=lambda: Conn())
    assert status == "ok: 1 rows" and rows[0]["doc_number"] == "HS-VTR-T001" and rows[0]["aliases"] == ["a"]


# ---- 5. 参照の一文の解決の内訳 ----
def test_resolution_summary():
    p = plan_document_layer(ROWS, cfg)
    res = p["summary"]["resolution"]
    assert res["unmatched_name"] == 2
    assert dict(res["unmatched_names_top"]) == {"組付指示書": 1, "文書管理規程": 1}
    assert res["external_created"] == 2 and res["resolved_document"] == 1     # V-00862・HZTL 20559 と、HS-VTR-T001 の 3-5 項
    assert res["rate"] == round(3 / 5, 3)
    detail = p["resolution_detail"]["name"]
    assert {d["text"] for d in detail} == {"組付指示書", "文書管理規程"} and all(d["source_doc"] == "HS-VTR-T002F.pdf" for d in detail)


# ---- 件数（書き出しがあれば）----
def _dump(path):
    sys.path.insert(0, os.path.join(KG, "registry"))
    from count_ledger_fields import load_dump
    return load_dump(path)


PROD6 = os.path.join(KG, "hs", "runs", "prod_parser1110", "graph_dump.json")
HS56 = os.path.join(KG, "hs", "runs", "full56_night1109", "graph_dump_linked.json")
SYNTH = os.path.join(KG, "synth", "graph_dump7_edc_fold.json")


@pytest.mark.skipif(not os.path.exists(PROD6), reason="6 文書の書き出しが無い")
def test_counts_prod6():
    rows, links, ents, rule_std = _dump(PROD6)
    p = plan_document_layer(rows, cfg, chunk_links=links, entities=ents, rule_standards=rule_std)
    f = p["summary"]["ledger_fields"]
    assert p["summary"]["current_versions"] == 5 and f["abbreviations"] == 0 and f["enacted"] == 5
    assert {"V00862", "V02313"} <= set(p["merge_plan"])
    assert sum(1 for k in p["standards"] if R.external_key(k) == "HZTL20559") == 1


@pytest.mark.skipif(not os.path.exists(HS56), reason="56 本の書き出しが無い")
def test_counts_hs56():
    rows, links, ents, rule_std = _dump(HS56)
    p = plan_document_layer(rows, cfg, chunk_links=links, entities=ents, rule_standards=rule_std)
    f = p["summary"]["ledger_fields"]
    assert p["summary"]["current_versions"] == 48
    assert "HS-RH-014" in p["standards"]["HS-RU-007"]["former_numbers"]
    assert f["enacted"] >= 40 and f["kinds"] >= 50
    keys = [R.external_key(k) for k, v in p["standards"].items() if v["external"]]
    assert len(keys) == len(set(keys))   # 外部の文書の表記ゆれ（JIS H 5202 / JIS-H5202 / JIS H5202）が 1 つずつ


@pytest.mark.skipif(not os.path.exists(SYNTH), reason="架空の文書の書き出しが無い")
def test_counts_synth38():
    rows, links, ents, rule_std = _dump(SYNTH)
    p = plan_document_layer(rows, R.load_config(os.path.join(KG, "registry", "synth_numbers.json")), chunk_links=links, entities=ents, rule_standards=rule_std)
    st = p["standards"]
    assert st["KS-TC-P001"]["abbreviations"] == {"計測共通"} and st["QK-TC-01"]["abbreviations"] == {"帳票規定"}
    assert "過検基 12 号" in st["KS-TC-T001"]["former_numbers"] and "計管基 2 号" in st["KS-TC-C501"]["former_numbers"]
    assert p["summary"]["ledger_fields"]["former_numbers"] == 8
    assert p["summary"]["current_versions"] == 38
    assert dict(p["summary"]["resolution"]["unmatched_names_top"]).get("組付指示書", 0) >= 10
