# -*- coding: utf-8 -*-
"""由来情報と安定キー（src/provenance.py）の単体試験。Neo4j と LLM は使わない。

確かめること
  (1) 文の切り出し: 「。」「．」「！」「？」と改行で切る。小数点の「．」では切らない。閉じ括弧は前の文に含める。表の行は 1 行 1 文
  (2) 照合: NFKC・大文字小文字・空白を無視する。半角カナの濁点も合成して照合する。aliases でも見つける
  (3) 言及: 本文を先に、パンくずの行は後に探す。見つからなければ None。200 字以内
  (4) リレーションの根拠: same_sentence / window / chunk_only / none と、チャンクの間の優先の順
  (5) 安定キー: 節の番号・付け直しの番号の前置き・番号の無い節の題・重なりの「~2」・節の無いチャンク・文書内の順位
  (6) 除外するリレーションの種類と、リレーションへの書き込みの判断（既定の検索の書き出しが除外していなければ書かない）
  (7) 後処理への登録（score.py は貸し出しの中で流し、失敗は残りの段を止めない）と一括取り込みの既定・EDC の指定
実行: docker exec -w /code backend python -m pytest -q -p no:cacheprovider tests/test_provenance.py
"""
import ast
import os
import types

import pytest

from src import provenance as P

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
# コンテナの中で確かめるときは、写しの場所を INGEST_PY で渡す
INGEST = os.environ.get("INGEST_PY") or os.path.join(os.path.dirname(BACKEND), "tools", "ingest", "ingest.py")


def sentences(text):
    return [text[s:e] for s, e in P.split_sentences(text)]


# ---------------------------------------------------------------- (1) 文の切り出し

def test_split_on_japanese_sentence_ends_and_newlines():
    text = "検査部門が判定する。結果は記録する！本当か？はい．\n次の行\n\n  最後の文"
    assert sentences(text) == ["検査部門が判定する。", "結果は記録する！", "本当か？", "はい．", "次の行", "最後の文"]


def test_decimal_point_and_closing_brackets():
    assert sentences("圧力は１．５ＭＰａとする。次へ") == ["圧力は１．５ＭＰａとする。", "次へ"]
    assert sentences("「判定する。」と定める。") == ["「判定する。」", "と定める。"]
    assert sentences("終わり。。次") == ["終わり。。", "次"]


def test_table_rows_are_sentences():
    text = "|項目|計器|\n|---|---|\n|吐出圧力|圧力計|"
    assert sentences(text) == ["|項目|計器|", "|---|---|", "|吐出圧力|圧力計|"]


def test_header_line_is_separated():
    text = "[試験文書 > 2 要領]\n本文の文。"
    header, body_start = P.split_header(text)
    assert text[header[0]:header[1]] == "[試験文書 > 2 要領]"
    assert text[body_start:] == "本文の文。"
    assert P.split_header("本文だけ。") == (None, 0)
    assert P.split_header("[見出しだけ]") == (None, 0)   # 改行の無い 1 行は本文として扱う


# ---------------------------------------------------------------- (2)(3) 照合と言及

def test_normalize_ignores_width_case_and_spaces():
    assert P.normalize("ＶＴＲ 形　過給機") == P.normalize("vtr形過給機") == "vtr形過給機"
    assert P.normalize("ｶﾞｽ") == "ガス"            # 半角カナの濁点を合成する
    norm, pos = P.normalize_with_positions("A b")
    assert norm == "ab" and pos == [0, 2]


def test_mention_prefers_body_then_header_and_aliases():
    chunk = P.ChunkText("c1", "[文書 > 1 適用]\n## 1 適用\nこの基準はＶＴＲ形 過給機に適用する。判定は検査部門が行う。")
    m, src = chunk.mention(P.entity_names("Vtr形過給機"))
    assert (m, src) == ("この基準はＶＴＲ形 過給機に適用する。", "body")
    m, src = chunk.mention(P.entity_names("文書"))   # 本文に無く、パンくずの行にだけある
    assert (m, src) == ("[文書 > 1 適用]", "header")
    m, src = chunk.mention(P.entity_names("品質管理課", ["検査部門"]))   # id が無ければ aliases で探す
    assert (m, src) == ("判定は検査部門が行う。", "body")
    assert chunk.mention(P.entity_names("存在しない名前")) == (None, None)


def test_mention_is_cut_to_200_chars_around_the_name():
    long_sentence = "あ" * 300 + "検査部門" + "い" * 300 + "。"
    m, _ = P.ChunkText("c", long_sentence).mention(P.entity_names("検査部門"))
    assert len(m) <= P.MAX_TEXT and "検査部門" in m and m.startswith("…") and m.endswith("…")


# ---------------------------------------------------------------- (4) リレーションの根拠

def names(x):
    return P.entity_names(x)


def test_evidence_kinds_in_one_chunk():
    chunk = P.ChunkText("c", "[文書 > 2 要領]\n検査部門が圧力計を用いる。\n記録は品管課が保管する。\n余談。\n圧力計を校正する。")
    kind, ev, _ = P.evidence_in_chunk(chunk, names("検査部門"), names("圧力計"))
    assert (kind, ev) == ("same_sentence", "検査部門が圧力計を用いる。")
    kind, ev, _ = P.evidence_in_chunk(chunk, names("品管課"), names("検査部門"))
    assert (kind, ev) == ("window", "検査部門が圧力計を用いる。\n記録は品管課が保管する。")
    kind, ev, _ = P.evidence_in_chunk(chunk, names("品管課"), names("校正"))   # 2 文離れている
    assert kind == "chunk_only" and ev == "記録は品管課が保管する。"
    kind, ev, _ = P.evidence_in_chunk(chunk, names("無い名前"), names("別の無い名前"))
    assert (kind, ev) == ("chunk_only", None)


def test_window_does_not_join_header_and_body():
    chunk = P.ChunkText("c", "[検査部門の文書]\n圧力計を用いる。")
    kind, _, _ = P.evidence_in_chunk(chunk, names("検査部門"), names("圧力計"))
    assert kind == "chunk_only"


def test_relation_evidence_prefers_better_kind_then_chunk_order():
    c1 = P.ChunkText("c1", "検査部門が判定する。\n圧力計は別に定める。")
    c2 = P.ChunkText("c2", "検査部門は圧力計を用いる。")
    c3 = P.ChunkText("c3", "検査部門が圧力計を校正する。")
    ev = P.relation_evidence([c1, c2, c3], names("検査部門"), names("圧力計"))
    assert ev == {"kind": "same_sentence", "chunk_id": "c2", "evidence": "検査部門は圧力計を用いる。"}
    ev = P.relation_evidence([c1], names("検査部門"), names("圧力計"))
    assert ev["kind"] == "window" and ev["chunk_id"] == "c1"
    assert P.relation_evidence([], names("検査部門"), names("圧力計")) == {"kind": "none", "chunk_id": None, "evidence": None}


def test_relation_evidence_keeps_both_names_in_long_sentence():
    text = "前置き" * 60 + "検査部門が" + "中身" * 20 + "圧力計を用いる" + "後書き" * 60 + "。"
    ev = P.relation_evidence([P.ChunkText("c", text)], names("検査部門"), names("圧力計"))
    assert ev["kind"] == "same_sentence" and len(ev["evidence"]) <= P.MAX_TEXT
    assert "検査部門" in ev["evidence"] and "圧力計" in ev["evidence"]


# ---------------------------------------------------------------- (5) 安定キー

def test_section_label_rules():
    assert P.section_label({"number": "2-1", "path": ["2 試運転確認項目及び要領", "2-1 性能試験"]}) == "2-1"
    assert P.section_label({"number": "1", "path": ["1 適 用"]}) == "1"
    assert P.section_label({"number": "1-1", "path": ["番号の付け直し1", "1-1 項と別表の摘要欄"]}) == "番号の付け直し1/1-1"
    assert P.section_label({"number": "1", "path": ["別表2 過給機性能試験及び性能換算法", "1 基本的考え方"]}) == "別表2過給機性能試験及び性能換算法/1"
    assert P.section_label({"number": "2", "path": ["1. 適用", "(2) 計算"]}) == "1/2"   # 章の下の項の番号
    assert P.section_label({"number": None, "path": ["協議・通知票"], "title": "協議・通知票"}) == "協議・通知票"
    assert P.section_label({"number": None, "path": ["見出し #3 の 節"]}) == "見出し＃3の節"   # 「#」は区切りなので置き換える
    assert P.section_label({"number": None, "path": [], "title": "-", "id": "abcdef0123"}) == "(-)"


def test_section_labels_disambiguate_duplicates_in_a_document():
    sections = [
        {"id": "s1", "fileName": "A.pdf", "number": "1", "path": ["1 適用"], "first_position": 1},
        {"id": "s2", "fileName": "A.pdf", "number": "1", "path": ["1 目的"], "first_position": 5},
        {"id": "s3", "fileName": "B.pdf", "number": "1", "path": ["1 適用"], "first_position": 1},
    ]
    assert P.section_labels(sections) == {"s1": "1", "s2": "1~2", "s3": "1"}


def test_stable_keys_rank_within_primary_section_and_document():
    labels = {"s1": "1", "s2": "2-1"}
    chunks = [
        {"id": "c3", "fileName": "A.pdf", "position": 3, "section": "s2"},
        {"id": "c1", "fileName": "A.pdf", "position": 1, "section": "s1"},
        {"id": "c2", "fileName": "A.pdf", "position": 2, "section": "s2"},
        {"id": "c4", "fileName": "A.pdf", "position": 4, "section": None},
        {"id": "c5", "fileName": "A#B.pdf", "position": 1, "section": "unknown"},
    ]
    keys = P.stable_keys(chunks, labels)
    assert keys == {"c1": ("A.pdf#1#1", 1), "c2": ("A.pdf#2-1#1", 2), "c3": ("A.pdf#2-1#2", 3),
                    "c4": ("A.pdf#-#4", 4), "c5": ("A#B.pdf#-#1", 1)}
    assert P.parse_stable_key("A#B.pdf#-#1") == ("A#B.pdf", "-", 1)
    assert P.parse_stable_key("A.pdf#番号の付け直し1/1-1#2") == ("A.pdf", "番号の付け直し1/1-1", 2)


# ---------------------------------------------------------------- (6) 除外の種類と書き込みの判断

def test_excluded_relationship_types():
    for t in ["HAS_ENTITY", "PART_OF", "NEXT_CHUNK", "FIRST_CHUNK", "SIMILAR", "IN_SECTION", "HAS_SECTION", "REFERS_TO",
              "IS_VERSION_OF", "LISTS", "ABOLISHES", "REPLACES", "IN_COMMUNITY", "PARENT_COMMUNITY"]:
        assert t in P.EXCLUDED_RELATIONSHIP_TYPES, t
    # 抽出した APPLIES_TO は残す（規則の APPLIES_TO は Document から張られ method='rule' を持つので、問い合わせの条件で外れる）
    assert "APPLIES_TO" not in P.EXCLUDED_RELATIONSHIP_TYPES


def test_relation_write_decision(monkeypatch):
    assert P.relation_write_decision("true")[0] is True
    assert P.relation_write_decision("false")[0] is False
    monkeypatch.setenv("PROVENANCE_RELATION_PROPERTIES", "false")
    assert P.relation_write_decision()[0] is False
    monkeypatch.delenv("PROVENANCE_RELATION_PROPERTIES")
    # auto: 既定の検索の書き出しが 4 つを除外していなければ書かない
    fake = types.SimpleNamespace(
        VECTOR_GRAPH_SEARCH_QUERY_SUFFIX="[k IN keys(r) WHERE k <> 'embedding']",
        LOCAL_COMMUNITY_SEARCH_QUERY_SUFFIX="[k IN keys(r) WHERE NOT k IN ['embedding', 'source_chunk_id', 'evidence', 'evidence_kind', 'provenance_method']]")
    import src.shared as shared_pkg
    monkeypatch.setattr(shared_pkg, "constants", fake, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "src.shared.constants", fake)
    ok, reason = P.relation_write_decision("auto")
    assert ok is False and "VECTOR_GRAPH_SEARCH_QUERY_SUFFIX" in reason and "LOCAL_COMMUNITY" not in reason
    fake.VECTOR_GRAPH_SEARCH_QUERY_SUFFIX = fake.LOCAL_COMMUNITY_SEARCH_QUERY_SUFFIX
    assert P.relation_write_decision("auto")[0] is True


def test_current_retrieval_rendering_decides_auto():
    """今の constants の書き出しで auto が何を選ぶか。除外していないのに書く判断をしていないこと"""
    from src.shared import constants as C
    ok, reason = P.relation_write_decision("auto")
    hidden = all(f"'{k}'" in C.VECTOR_GRAPH_SEARCH_QUERY_SUFFIX and f"'{k}'" in C.LOCAL_COMMUNITY_SEARCH_QUERY_SUFFIX
                 for k in P.RELATION_PROPERTY_KEYS)
    assert ok is hidden, reason


# ---------------------------------------------------------------- (7) 後処理への登録と一括取り込み

def _post_processing_fn():
    tree = ast.parse(open(os.path.join(BACKEND, "score.py"), encoding="utf-8").read())
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "post_processing")


def test_score_runs_provenance_with_lease_and_defers_errors():
    fn = _post_processing_fn()
    block = next(n for n in ast.walk(fn) if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                 and isinstance(n.test.left, ast.Constant) and n.test.left.value == "attach_provenance")
    src = ast.unparse(block)
    assert "run_with_lease, graph, 'provenance', attach_provenance, graph" in src
    assert "deferred_errors.append" in src   # 失敗しても残りの段（類似リンク・索引）は流す


def test_ingest_default_tasks_and_edc_options():
    if not os.path.exists(INGEST):   # バックエンドのコンテナの中（/code）には tools が無い
        pytest.skip("tools/ingest/ingest.py が無い（コンテナの中）")
    text = open(INGEST, encoding="utf-8").read()
    tree = ast.parse(text)
    default = next(n.value.value for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "DEFAULT_TASKS")
    tasks = default.split(",")
    assert tasks.index("attach_provenance") == tasks.index("build_section_layer") + 1
    assert 'f"edc_doctype={a.edc_doctype}"' in text and 'f"edc_schema_mode={a.edc_schema_mode}"' in text
    from src.entities.source_extract_params import SourceScanExtractParams
    assert {"edc_doctype", "edc_schema_mode"} <= set(SourceScanExtractParams.model_fields)


def test_doc_clause_name_matches_no_form():
    """EDC が文書番号と項番を 1 つにした名前（例「HS-VTR-T001 3-5項」）は、本文の「HS-VTR-T001の3-5項」でも見つかる（11/9）。"""
    from src import provenance as P

    names = P.entity_names("HS-VTR-T001 3-5項")
    assert names == ["hs-vtr-t0013-5項", "hs-vtr-t001の3-5項"]
    chunk = P.ChunkText("c1", "### 2-6 運転条件\n性能計測時の過給機運転条件は、HS-VTR-T001の3-5項に依る。")
    assert chunk.mention(names) == ("性能計測時の過給機運転条件は、HS-VTR-T001の3-5項に依る。", "body")
    # 先頭の語に数字が無い名前（規格の略号と番号の間の空白）には「の」の形を足さない
    assert P.entity_names("JIS B 8340") == ["jisb8340"]
