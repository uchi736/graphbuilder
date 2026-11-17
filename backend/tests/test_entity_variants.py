# -*- coding: utf-8 -*-
"""表記ゆれのエンティティの統合（src/entity_variants.py）の組の見つけ方の試験。DB は使わない（偽の行で plan_groups を流す）。
DB に書く側（関係の付け替え・重複のまとめ・aliases・台帳と戻し・冪等）は tests/test_entity_variants_db.py。
実行: python -m pytest -q -p no:cacheprovider tests/test_entity_variants.py"""
from src.entity_variants import decorated_key, merged_aliases, plan_groups, variant_key

CFG = {"_series": [], "external_prefixes": ["V", "JIS"], "external_min_digits": 3}


def row(eid, name, labels, mentions=1, **kw):
    return {"eid": eid, "id": name, "labels": ["__Entity__"] + list(labels), "mentions": mentions, **kw}


def plan(rows, **kw):
    return plan_groups(rows, cfg=CFG, **kw)


def test_variant_key():
    assert variant_key("X -00123") == variant_key("X-00123") == variant_key("X00123") == variant_key("x００１２３")
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
            row("5", "X00123", ["Standard"], method="rule"), row("6", "X-00123", ["Standard"]),
            row("7", "ABCD 20559", ["Standard"], method="rule"), row("8", "ABCD20559", ["Standard"], method="rule"),
            row("9", "JIS 方式", ["Standard"]), row("10", "JIS方式", ["Standard"], 2)]
    out = plan(rows)
    # 文書番号でない Standard の型（EDC の抽出）は普通に統合する
    assert [(g["keep"]["id"], [m["id"] for m in g["merge"]]) for g in out["groups"]] == [("JIS方式", ["JIS 方式"])]


def test_keep_choice():
    # 言及の多いもの
    out = plan([row("1", "X -00123", ["DocumentRef"], 1), row("2", "X00123x", ["DocumentRef"], 9)])
    assert out["groups"] == []  # X00123x は別の正規形
    out = plan([row("1", "X -00123", ["DocumentRef"], 1), row("2", "X-00123", ["DocumentRef"], 3), row("3", "X00123", ["DocumentRef"], 2)])
    g = out["groups"][0]
    assert g["keep"]["id"] == "X-00123" and [m["id"] for m in g["merge"]] == ["X00123", "X -00123"]
    # 同数なら名前の短いもの
    g = plan([row("1", "X -00123", ["DocumentRef"], 2), row("2", "X-00123", ["DocumentRef"], 2), row("3", "X00123", ["DocumentRef"], 2)])["groups"][0]
    assert g["keep"]["id"] == "X00123"
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
    keep = {"id": "X-00123", "aliases": ["X－００１２３"]}
    others = [{"id": "X -00123", "aliases": None}, {"id": "X00123", "aliases": ["X-00123", "x00123"]}]
    # 既存の aliases と統合した側の名前・aliases。残す側の名前は入れない。並びは graph_edit と同じ（正規化キー、名前）
    assert merged_aliases(keep, others) == ["X -00123", "X－００１２３", "X00123", "x00123"]


def test_idempotent_plan():
    # 統合の後の状態（残す側だけ・aliases 付き）からは組が出ない
    rows = [row("1", "± 3 %以内", ["Criterion"], 1), row("2", "±3%以内", ["Criterion"], 3)]
    g = plan(rows)["groups"][0]
    after = [dict(g["keep"], aliases=merged_aliases(g["keep"], g["merge"]))]
    assert plan(after)["groups"] == []


# ---- 飾りの違いの段（level="decorated"、実験台帳 X35）----

def test_decorated_key_material_and_test_items():
    M, T = ["__Entity__", "Material"], ["__Entity__", "TestItem"]
    assert decorated_key("ZM440H相当材", M) == decorated_key("ZM 440H 相当材", M) == decorated_key("ZM440H", M)
    assert decorated_key("Inco.713C又は相当材", M) == decorated_key("Inco.713C", M)
    assert decorated_key("ZC4A材", M) == decorated_key("ZC4A", M)
    # 型番の後ろの英字が違えば別物。語の一部の「材」は外さない
    assert decorated_key("ZN630Q", M) != decorated_key("ZN630", M)
    assert decorated_key("ZM435", M) != decorated_key("ZM435H", M)
    assert decorated_key("供試材", M) != decorated_key("供試", M)
    assert decorated_key("鋼材", M) == variant_key("鋼材")
    # 試験と検査は同じ。語尾そのものは外さない
    assert decorated_key("硬さ試験", T) == decorated_key("硬さ検査", T)
    assert decorated_key("超音波探傷試験", T) == decorated_key("超音波探傷検査", T)
    assert decorated_key("硬さ", T) != decorated_key("硬さ試験", T)
    # かな・送り仮名のゆれ
    assert decorated_key("焼き入れ", ["__Entity__", "Process"]) == decorated_key("焼入れ", ["__Entity__", "Process"])
    assert decorated_key("焼もどし", ["__Entity__", "Process"]) == decorated_key("焼戻し", ["__Entity__", "Process"])
    assert decorated_key("溶射コーテイング", ["__Entity__", "Process"]) == decorated_key("溶射コーティング", ["__Entity__", "Process"])
    # 対象外の種類（値・規格・型式）は表記ゆれの正規形のまま
    assert decorated_key("引張強さ≧900N/mm2相当", ["__Entity__", "Criterion"]) == variant_key("引張強さ≧900N/mm2相当")
    assert decorated_key("JIS G 4053相当", ["__Entity__", "Standard"]) == variant_key("JIS G 4053相当")


def test_decorated_level_groups_and_keeps_the_plain_name():
    rows = [row("1", "ZM440H相当材", ["Material"], 4), row("2", "ZM 440H 相当材", ["Material"], 1), row("3", "ZM440H", ["Material"], 1),
            row("4", "ZN630Q", ["Material"], 2), row("5", "ZN630", ["Material"], 1),
            row("6", "硬さ試験", ["TestItem"], 3), row("7", "硬さ検査", ["TestItem"], 1), row("8", "硬さ", ["Property"], 5),
            row("9", "≧980相当", ["Criterion"]), row("10", "≧980", ["Criterion"])]
    out = plan(rows, level="decorated")
    got = {g["keep"]["id"]: sorted(m["id"] for m in g["merge"]) for g in out["groups"]}
    # 飾りの無い名前を残す。ZN630Q と ZN630、硬さ（Property）、値は統合しない
    assert got == {"ZM440H": ["ZM 440H 相当材", "ZM440H相当材"], "硬さ試験": ["硬さ検査"]}
    # 既定の段（variant）は表記ゆれだけ
    got = {g["keep"]["id"]: sorted(m["id"] for m in g["merge"]) for g in plan(rows)["groups"]}
    assert got == {"ZM440H相当材": ["ZM 440H 相当材"]}
