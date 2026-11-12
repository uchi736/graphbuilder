# -*- coding: utf-8 -*-
"""表記ゆれのエンティティの統合（src/entity_variants.py）の組の見つけ方の試験。DB は使わない（偽の行で plan_groups を流す）。
DB に書く側（関係の付け替え・重複のまとめ・aliases・台帳と戻し・冪等）は tests/test_entity_variants_db.py。
実行: python -m pytest -q -p no:cacheprovider tests/test_entity_variants.py"""
from src.entity_variants import merged_aliases, plan_groups, variant_key

CFG = {"_series": [], "external_prefixes": ["V", "JIS"], "external_min_digits": 3}


def row(eid, name, labels, mentions=1, **kw):
    return {"eid": eid, "id": name, "labels": ["__Entity__"] + list(labels), "mentions": mentions, **kw}


def plan(rows, **kw):
    return plan_groups(rows, cfg=CFG, **kw)


def test_variant_key():
    assert variant_key("V -00862") == variant_key("V-00862") == variant_key("V00862") == variant_key("v００８６２")
    assert variant_key("± 3 %以内") == variant_key("±3%以内") == variant_key("±３％以内")
    assert variant_key("V (m3/s)") == variant_key("V(m3/s)")
    assert variant_key("A‐1") == variant_key("A−1") == variant_key("A－1") == variant_key("A_1") == variant_key("a1")
    # 先頭の負号は残す。長音符・中黒・ピリオドは残す
    assert variant_key("-5℃") != variant_key("5℃")
    assert variant_key("コンプレッサー") != variant_key("コンプレッサ")
    assert variant_key("1.5") != variant_key("15")
    assert variant_key("圧力・温度") != variant_key("圧力温度")


def test_same_type_merged_different_type_kept_apart():
    rows = [row("1", "V (m3/s)", ["Parameter"]), row("2", "V(m3/s)", ["Term"]),
            row("3", "± 3 %以内", ["Criterion"], 1), row("4", "±3%以内", ["Criterion"], 3)]
    out = plan(rows)
    assert [(g["keep"]["id"], [m["id"] for m in g["merge"]]) for g in out["groups"]] == [("±3%以内", ["± 3 %以内"])]
    assert out["cross_type"] == [{"key": "v(m3/s)", "members": ["V (m3/s)〔Parameter〕", "V(m3/s)〔Term〕"]}]


def test_shared_label_counts_as_same_type_but_not_chains():
    # EDC の 2 つのラベル: 共通のラベルがあれば同じ種類
    out = plan([row("1", "吐出圧力(静圧)", ["Term", "MeasurementItem"], 2), row("2", "吐出圧力 (静圧)", ["MeasurementItem"], 1)])
    assert len(out["groups"]) == 1 and out["groups"][0]["type"] == ["MeasurementItem", "Term"]
    # 鎖状にだけ重なる 3 つは統合しない
    out = plan([row("1", "A B", ["X"]), row("2", "AB", ["X", "Y"]), row("3", "A-B", ["Y"])])
    assert out["groups"] == [] and len(out["skipped"]) == 1 and "鎖状" in out["skipped"][0]["reason"]


def test_management_labels_and_document_numbers_excluded():
    rows = [row("1", "4.1 適用", ["Section"]), row("2", "4. 1 適用", ["Section"]),
            row("3", "a.pdf", ["Document"]), row("4", "a .pdf", ["Document"]),
            row("5", "V00862", ["Standard"], method="rule"), row("6", "V-00862", ["Standard"]),
            row("7", "HZTL 20559", ["Standard"], method="rule"), row("8", "HZTL20559", ["Standard"], method="rule"),
            row("9", "JIS 方式", ["Standard"]), row("10", "JIS方式", ["Standard"], 2)]
    out = plan(rows)
    # 文書番号でない Standard の型（EDC の抽出）は普通に統合する
    assert [(g["keep"]["id"], [m["id"] for m in g["merge"]]) for g in out["groups"]] == [("JIS方式", ["JIS 方式"])]


def test_keep_choice():
    # 言及の多いもの
    out = plan([row("1", "V -00862", ["DocumentRef"], 1), row("2", "V00862x", ["DocumentRef"], 9)])
    assert out["groups"] == []  # V00862x は別の正規形
    out = plan([row("1", "V -00862", ["DocumentRef"], 1), row("2", "V-00862", ["DocumentRef"], 3), row("3", "V00862", ["DocumentRef"], 2)])
    g = out["groups"][0]
    assert g["keep"]["id"] == "V-00862" and [m["id"] for m in g["merge"]] == ["V00862", "V -00862"]
    # 同数なら名前の短いもの
    g = plan([row("1", "V -00862", ["DocumentRef"], 2), row("2", "V-00862", ["DocumentRef"], 2), row("3", "V00862", ["DocumentRef"], 2)])["groups"][0]
    assert g["keep"]["id"] == "V00862"
    # 手動で修正したノードを優先。2 つ以上なら統合しない
    g = plan([row("1", "ab c", ["T"], 1, curated=True), row("2", "abc", ["T"], 5)])["groups"][0]
    assert g["keep"]["id"] == "ab c"
    out = plan([row("1", "ab c", ["T"], 1, curated=True), row("2", "abc", ["T"], 5, curated=True)])
    assert out["groups"] == [] and "手動" in out["skipped"][0]["reason"]


def test_distinct_pairs_respected():
    from src.graph_edit.normalize import norm_key
    out = plan([row("1", "A-1", ["T"]), row("2", "A1", ["T"])], distinct_pairs={tuple(sorted([norm_key("A-1"), norm_key("A1")]))})
    assert out["groups"] == [] and "別物" in out["skipped"][0]["reason"]


def test_merged_aliases():
    keep = {"id": "V-00862", "aliases": ["V－００８６２"]}
    others = [{"id": "V -00862", "aliases": None}, {"id": "V00862", "aliases": ["V-00862", "v00862"]}]
    # 既存の aliases と統合した側の名前・aliases。残す側の名前は入れない。並びは graph_edit と同じ（正規化キー、名前）
    assert merged_aliases(keep, others) == ["V -00862", "V－００８６２", "V00862", "v00862"]


def test_idempotent_plan():
    # 統合の後の状態（残す側だけ・aliases 付き）からは組が出ない
    rows = [row("1", "± 3 %以内", ["Criterion"], 1), row("2", "±3%以内", ["Criterion"], 3)]
    g = plan(rows)["groups"][0]
    after = [dict(g["keep"], aliases=merged_aliases(g["keep"], g["merge"]))]
    assert plan(after)["groups"] == []
