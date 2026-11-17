"""参照追跡の検索方式（graph_vector_fulltext_ref）と、方式ごとの検索設定の既定値（3 段の決め方）。DB・LLM・ネットワークなし。

決め方: backend の設定（環境変数） < 方式の既定値（CHAT_MODE_CONFIG_MAP の retrieval_defaults） < 画面の上書き（retrieval_settings）。
普通の検索（参照追跡以外のチャンクを検索する方式）は、backend の設定で CHAT_REFERENCE_MAX が 1 以上でも参照をたどらない（reference_max=0）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("VLLM_EMBEDDING_ENDPOINT", "http://127.0.0.1:9/v1,dummy")

from src import rerank_retriever as rr  # noqa: E402
from src.shared import constants as C  # noqa: E402


def test_ref_mode_is_defined_like_graph_vector_fulltext():
    base = C.CHAT_MODE_CONFIG_MAP[C.CHAT_VECTOR_GRAPH_FULLTEXT_MODE]
    ref = C.CHAT_MODE_CONFIG_MAP[C.CHAT_VECTOR_GRAPH_FULLTEXT_REF_MODE]
    for key in ("retrieval_query", "top_k", "index_name", "keyword_index", "document_filter", "node_label",
                "embedding_node_property", "text_node_properties"):
        assert ref[key] == base[key], key
    assert C.CHAT_VECTOR_GRAPH_FULLTEXT_REF_MODE == "graph_vector_fulltext_ref"
    assert C.CHAT_DEFAULT_MODE == C.CHAT_VECTOR_GRAPH_FULLTEXT_MODE, "既定の方式は変えない"


def test_mode_defaults_follow_references_only_in_ref_mode():
    ref = C.CHAT_MODE_CONFIG_MAP[C.CHAT_VECTOR_GRAPH_FULLTEXT_REF_MODE]["retrieval_defaults"]
    assert ref["reference_max"] >= 1 and ref["reference_sections"] is True and ref["reference_document_layer"] is True
    for mode in (C.CHAT_VECTOR_MODE, C.CHAT_FULLTEXT_MODE, C.CHAT_VECTOR_GRAPH_MODE, C.CHAT_VECTOR_GRAPH_FULLTEXT_MODE):
        assert C.CHAT_MODE_CONFIG_MAP[mode]["retrieval_defaults"]["reference_max"] == 0, mode
    # チャンクを検索しない方式には方式の既定値を置かない（参照の追跡は使われない）
    for mode in (C.CHAT_ENTITY_VECTOR_MODE, C.CHAT_GLOBAL_VECTOR_FULLTEXT_MODE):
        assert "retrieval_defaults" not in C.CHAT_MODE_CONFIG_MAP[mode], mode


def test_three_layer_resolution(monkeypatch):
    monkeypatch.setenv("CHAT_REFERENCE_MAX", "2")
    monkeypatch.setenv("CHAT_REFERENCE_SECTIONS", "false")
    # 方式の既定値なし（従来どおり backend の設定）
    s = rr.resolve_retrieval_settings(None)
    assert s["reference_max"] == 2 and s["reference_sections"] is False
    # 普通の検索: 方式の既定値で 0 になる
    plain = C.CHAT_MODE_CONFIG_MAP[C.CHAT_VECTOR_GRAPH_FULLTEXT_MODE]["retrieval_defaults"]
    s = rr.resolve_retrieval_settings(None, mode_defaults=plain)
    assert s["reference_max"] == 0
    # 参照追跡: 方式の既定値で全部有効
    ref = C.CHAT_MODE_CONFIG_MAP[C.CHAT_VECTOR_GRAPH_FULLTEXT_REF_MODE]["retrieval_defaults"]
    s = rr.resolve_retrieval_settings(None, mode_defaults=ref)
    assert s["reference_max"] == 2 and s["reference_sections"] is True and s["reference_document_layer"] is True
    assert s["reference_select"] == "both" and s["reference_depth"] == 2 and s["reference_presentation"] == "inline"
    # 画面の上書きは方式の既定値より優先（普通の検索でも 1 問だけたどれる、参照追跡でも止められる）
    s = rr.resolve_retrieval_settings({"reference_max": 3}, mode_defaults=plain)
    assert s["reference_max"] == 3
    s = rr.resolve_retrieval_settings({"reference_sections": False}, mode_defaults=ref)
    assert s["reference_sections"] is False and s["reference_document_layer"] is True
    # 画面の空の値は上書きにならない。方式の既定値の不正な値は無視される（型・範囲の検査は同じ）
    s = rr.resolve_retrieval_settings({"reference_max": ""}, mode_defaults=ref)
    assert s["reference_max"] == 2
    s = rr.resolve_retrieval_settings(None, mode_defaults={"reference_max": 999})
    assert s["reference_max"] == 2, "範囲外は前の段の値のまま"


def test_retrieve_only_and_chat_use_mode_defaults():
    import inspect
    from src import QA_integration, retrieve_only
    # チャットと検索だけの口は、同じ関数で検索設定を解決する（方式の既定値を重ねる）
    assert "mode_defaults=chat_mode_settings.get(\"retrieval_defaults\")" in inspect.getsource(QA_integration.chat_mode_settings_with_retrieval)
    assert "chat_mode_settings_with_retrieval(mode, retrieval_settings)" in inspect.getsource(QA_integration.QA_RAG)
    assert "chat_mode_settings_with_retrieval(mode, retrieval_settings)" in inspect.getsource(retrieve_only.retrieve_context)


def test_parse_retrieval_settings():
    assert rr.parse_retrieval_settings('{"reference_max": 1}') == {"reference_max": 1}
    assert rr.parse_retrieval_settings({"reference_max": 1}) == {"reference_max": 1}
    assert rr.parse_retrieval_settings("not json") is None and rr.parse_retrieval_settings("  ") is None
    assert rr.parse_retrieval_settings("[1]") is None and rr.parse_retrieval_settings(None) is None
