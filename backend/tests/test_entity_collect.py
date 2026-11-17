# -*- coding: utf-8 -*-
"""エンティティ起点の収集（src/entity_collect.py）の、グラフも LLM も使わない部分の試験。"""
from src import entity_collect as ec


def test_fold_ignores_kana_variants():
    assert ec.fold("ザイグロ検査") == ec.fold("ザイクロ検査")
    assert ec.fold("クロムカーバイト") == ec.fold("クローム カーバイド")
    assert ec.fold("コーテイング") == ec.fold("コーティング")
    assert ec.fold("ＳＡＣＭ 645") == "sacm645"


def test_fold_keeps_different_codes_apart():
    assert ec.fold("ZM440H") != ec.fold("ZM435H")
    assert ec.fold("JIS G 5501") != ec.fold("JIS G 5502")


def test_term_key_strips_general_suffix_only_when_enough_remains():
    assert ec.term_key("窒化処理") == "窒化"
    assert ec.term_key("蛍光浸透探傷検査") == "蛍光浸透探傷"
    assert ec.term_key("JIS Z 2201") == "jisz2201"
    assert ec.term_key("4号試験片") == ec.fold("4号試験片")


def test_parse_terms():
    assert ec.parse_terms('["ZM440H"]') == ["ZM440H"]
    assert ec.parse_terms('答え: ["クロムカーバイド", "Cr3C2", "クロムカーバイド"]\n') == ["クロムカーバイド", "Cr3C2"]
    assert ec.parse_terms("語はありません") == []
    assert len(ec.parse_terms('["a1","b2","c3","d4","e5"]')) == ec.TERM_MAX


def test_question_terms_uses_the_given_llm():
    seen = []

    def ask(messages):
        seen.append(messages)
        return '["ZA645"]'

    assert ec.question_terms("ZA645 系の材料を使う部品をすべて挙げよ。", ask) == ["ZA645"]
    assert "ZA645 系の材料" in seen[0][0]["content"]


def test_match_entities_contains_and_alias():
    names = ["蛍光浸透探傷検査", "蛍光浸透 探傷試験", "蛍光浸透探傷検査(ザイクロ検査)", "ザイクロ検査", "ザイグロ", "蛍光探傷(ザイクロ)検査",
             "表面検査(目視、ザイクロ)", "表面検査(蛍光浸透探傷検査)", "表面検査", "染色浸透探傷検査", "超音波探傷検査"]
    found = ec.match_entities(names, [ec.term_key("蛍光浸透探傷")])
    assert set(found["matched"]) == {"蛍光浸透探傷検査", "蛍光浸透 探傷試験", "蛍光浸透探傷検査(ザイクロ検査)", "表面検査(蛍光浸透探傷検査)"}
    assert found["aliases"] == [ec.fold("ザイクロ")]
    assert set(found["aliased"]) == {"ザイクロ検査", "ザイグロ", "蛍光探傷(ザイクロ)検査", "表面検査(目視、ザイクロ)"}
    # 括弧の外側が上位の語のとき（表面検査(蛍光浸透探傷検査)）は、外側を別名にしない
    assert "表面検査" not in found["matched"] + found["aliased"]
    assert "染色浸透探傷検査" not in found["matched"] + found["aliased"]


def test_match_entities_material_code():
    names = ["ZA645", "ZA645相当材", "ZA 645 相当材", "JIS G 4202 ZA645 材", "ZN630", "ZM435H"]
    found = ec.match_entities(names, [ec.term_key("ZA645")])
    assert set(found["matched"]) == {"ZA645", "ZA645相当材", "ZA 645 相当材", "JIS G 4202 ZA645 材"}
    assert found["aliased"] == []


def test_alias_is_not_taken_from_lists_or_years():
    assert ec.alias_keys("表面検査(目視、ザイクロ)", [ec.fold("表面検査")]) == []
    assert ec.alias_keys("JIS H 5202(1999)", [ec.fold("JIS H 5202")]) == []
    # 別名が質問の語と重なるときは足さない
    assert ec.alias_keys("Ni・Cr基のCr3C2(クロムカーバイト)", [ec.fold("Cr3C2"), ec.fold("クロムカーバイド")]) == []


def test_snippet_keeps_the_term_and_the_heading():
    text = "[TS-B016A > 3. 材料]\n" + "あ" * 600 + "材質は ZA 645 相当材とする。" + "い" * 600
    cut = ec.snippet(text, [ec.fold("ZA645")], 300)
    assert "ZA 645 相当材" in cut and cut.startswith("[TS-B016A > 3. 材料]") and len(cut) < 400
    assert ec.snippet("短い本文", ["x"], 300) == "短い本文"


def test_mode_defaults_do_not_change_other_modes():
    from src import rerank_retriever as rr
    from src.shared import constants as c
    assert rr.resolve_retrieval_settings()["entity_collect"] is False
    assert rr.resolve_retrieval_settings()["collect_prompt"] is False
    mode = c.CHAT_MODE_CONFIG_MAP[c.CHAT_ENTITY_COLLECT_MODE]
    s = rr.resolve_retrieval_settings(None, mode_defaults=mode["retrieval_defaults"])
    assert s["entity_collect"] is True and s["collect_prompt"] is True
    assert rr.resolve_retrieval_settings({"collect_prompt": False}, mode_defaults=mode["retrieval_defaults"])["collect_prompt"] is False
    for name, cfg in c.CHAT_MODE_CONFIG_MAP.items():
        if name != c.CHAT_ENTITY_COLLECT_MODE:
            assert not (cfg.get("retrieval_defaults") or {}).get("entity_collect")


def test_system_template_adds_the_guideline_only_when_asked():
    from src.shared import constants as c
    assert c.chat_system_template() == c.CHAT_SYSTEM_TEMPLATE
    assert c.CHAT_COLLECT_GUIDELINE not in c.chat_system_template(True)
    both = c.chat_system_template(True, True)
    assert c.CHAT_REFERENCE_GUIDELINE in both and c.CHAT_COLLECT_GUIDELINE in both
    assert both.index(c.CHAT_COLLECT_GUIDELINE) < both.index("**IMPORTANT**")
    assert "{" not in c.CHAT_COLLECT_GUIDELINE
