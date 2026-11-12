# -*- coding: utf-8 -*-
"""抽出方式（standard / edc）の単体試験。EDC には繋がない（HTTP は差し替える）。
実行: cd backend && python3 -m pytest tests/test_extraction_methods.py -q   （pytest が無ければ python3 tests/test_extraction_methods.py）"""
import asyncio
import json
import os
import sys
import tempfile
from types import SimpleNamespace

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from src import extraction_methods as X  # noqa: E402
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException  # noqa: E402

GEMMA = {"EDC_LLM_MODEL": "google/gemma-4-26B-A4B-it",
         "LLM_MODEL_CONFIG_GEMMA4": "google/gemma-4-26B-A4B-it,http://x/v1,k",
         "LLM_MODEL_CONFIG_GEMMA4CPU": "gemma4cpu,http://y/v1,k"}
# 画面に返してはならない値（URL・資格情報）を含む設定
EDC_CONFIG_WITH_SECRET = "http://edc-user:edc-secret@edc.test:8081,auto,recursive,2"
EDC_FLAGS_ON = {"EDC_ENRICH_SCHEMA": "true", "EDC_FOLD_UNKNOWN_RELATIONS": "true", "EDC_DROP_SENTENCE_NAMES": "true"}
EDC_FLAGS_UNSET = {"EDC_ENRICH_SCHEMA": None, "EDC_FOLD_UNKNOWN_RELATIONS": None, "EDC_DROP_SENTENCE_NAMES": None}
# 禁止語（画面に表示する文言に含めない語）
BANNED_WORDS = ("届か", "やり直", "使えません", "窓", "本家", "手元", "確かめ", "直す", "無い")


class Env:
    """環境変数を一時的に差し替える（None は消す）。"""

    def __init__(self, **kv):
        self.kv, self.old = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class FakeStatus:
    """fetch_edc_status の差し替え（EDC に繋がない）。"""

    def __init__(self, code=200, health=None, doctypes=None, reachable=True):
        self.result = {"configured": True, "reachable": reachable, "health_code": code if reachable else None,
                       "health": health or {}, "doctypes": doctypes or [],
                       "error": None if reachable else "EDC に接続できない",
                       "error_detail": None if reachable else "ConnectError"}

    def __enter__(self):
        self.orig = X.fetch_edc_status

        async def fake(include_doctypes=False, timeout=None, transport=None):
            return self.result
        X.fetch_edc_status = fake

    def __exit__(self, *exc):
        X.fetch_edc_status = self.orig


class Schemas:
    """EDC の定義ファイル（registry.json と語彙 CSV）を一時ディレクトリに作成し、EDC_SCHEMAS_DIR をそこへ向ける。
    registry.json のパスは EDC と同じく schemas/ 付きで書き、ファイル名のみで解決されることを確認する。"""

    BACKBONE_TYPES = 'Document,"文書"\nSection,"章・節"\n\nTerm,"用語"\n'
    BACKBONE_SCHEMA = 'REFERS_TO,"参照"\nPART_OF,"構成要素"\n'
    FILES = {
        "kg_backbone_types.csv": BACKBONE_TYPES,
        "kg_backbone_schema.csv": BACKBONE_SCHEMA,
        # Document は共通の語彙と重複する（和集合では 1 件）。1 列のみの行と空行は数えない
        "domain_a_types.csv": 'Equipment,"機器"\nDocument,"様式"\nOnlyName\n\nCriterion,"判定基準"\n',
        "domain_a_schema.csv": 'APPLIES_TO,"適用"\nJUDGED_BY,"判定"\nREFERS_TO,"参照"\n',
        "domain_b_types.csv": 'Concept,"概念"\n',
    }
    REGISTRY = {
        "backbone": {"schema": "schemas/kg_backbone_schema.csv", "types": "schemas/kg_backbone_types.csv"},
        "doctypes": [
            {"name": "種別A", "description": "説明A", "schema": "schemas/domain_a_schema.csv",
             "types": "schemas/domain_a_types.csv"},
            # schema の CSV が存在しない文書種別は件数を None とする
            {"name": "種別B", "description": "説明B", "schema": "schemas/domain_b_schema.csv",
             "types": "schemas/domain_b_types.csv"},
            # 名称のない項目は数えない
            {"description": "名称なし", "schema": "x.csv", "types": "y.csv"},
        ],
    }

    def __init__(self, registry=None, files=None, raw_registry=None):
        self.registry = registry if registry is not None else self.REGISTRY
        self.files = files if files is not None else self.FILES
        self.raw_registry = raw_registry

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        for name, text in self.files.items():
            with open(os.path.join(self.tmp.name, name), "w", encoding="utf-8") as f:
                f.write(text)
        with open(os.path.join(self.tmp.name, "registry.json"), "w", encoding="utf-8") as f:
            f.write(self.raw_registry if self.raw_registry is not None else json.dumps(self.registry, ensure_ascii=False))
        self.env = Env(EDC_SCHEMAS_DIR=self.tmp.name)
        self.env.__enter__()
        return self.tmp.name

    def __exit__(self, *exc):
        self.env.__exit__(*exc)
        self.tmp.cleanup()


def raises(fn, *args):
    try:
        fn(*args)
    except LLMGraphBuilderException as e:
        return str(e)
    raise AssertionError(f"{fn.__name__}{args} が通ってしまった")


def no_banned_words(text):
    found = [w for w in BANNED_WORDS if w in text]
    assert not found, (found, text)


def test_normalize():
    assert X.normalize_extraction_method("gemma4", None) == "standard"
    assert X.normalize_extraction_method("gemma4", "") == "standard"
    assert X.normalize_extraction_method("gemma4", " EDC ") == "edc"
    assert X.normalize_extraction_method("edc", None) == "edc"          # 旧来の model=edc
    assert X.normalize_extraction_method("EDC", "standard") == "edc"    # model=edc は LLM ではない
    message = raises(X.normalize_extraction_method, "gemma4", "graphrag")
    assert "graphrag" in message and "使用できません" in message
    no_banned_words(message)


def test_resolve_edc_llm():
    with Env(**GEMMA):
        # /health の llm は到達の結果で、名前としては使わない
        assert X.resolve_edc_llm({"status": "ok", "llm": "ok"}) == ("gemma4", "google/gemma-4-26B-A4B-it")
        assert X.resolve_edc_llm({"status": "degraded", "llm": "down: refused"}) == ("gemma4", "google/gemma-4-26B-A4B-it")
        assert X.resolve_edc_llm(None) == ("gemma4", "google/gemma-4-26B-A4B-it")
        # EDC が名前を返すようになったらそちらを優先する
        assert X.resolve_edc_llm({"llm_model": "other/model"}) == ("other/model", "other/model")
    with Env(EDC_LLM_MODEL=None):
        assert X.resolve_edc_llm({"llm": "ok"}) == (None, None)


def test_fetch_parses_edc_responses():
    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(503, json={"status": "degraded", "llm": "down: refused", "embedding": "ok"})
        return httpx.Response(200, json=[{"name": "社内規程_就業規則", "description": "規程"}, {"x": 1}])
    with Env(LLM_MODEL_CONFIG_EDC="http://edc.test:8081,auto,recursive,2"):
        s = asyncio.run(X.fetch_edc_status(include_doctypes=True, transport=httpx.MockTransport(handler)))
    assert s["reachable"] and s["health_code"] == 503 and s["health"]["llm"].startswith("down")
    assert s["doctypes"] == [{"name": "社内規程_就業規則", "description": "規程"}]
    reason = X._unavailable_reason(s)
    # 画面に表示する理由は依存サービスの名称のみとし、原文（down: ...）は reason_detail で返す
    assert reason == "EDC の依存サービス（LLM）が停止している" and "down" not in reason
    assert X._unavailable_detail(s).startswith("llm: down")
    with Env(LLM_MODEL_CONFIG_EDC=None):
        s = asyncio.run(X.fetch_edc_status())
    assert not s["configured"] and "未設定" in X._unavailable_reason(s)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)
    with Env(LLM_MODEL_CONFIG_EDC="http://edc.test:8081"):
        s = asyncio.run(X.fetch_edc_status(include_doctypes=True, transport=httpx.MockTransport(refused)))
    reason = X._unavailable_reason(s)
    assert not s["reachable"] and reason == "EDC に接続できない" and X._unavailable_detail(s) == "ConnectError"
    assert "edc.test" not in reason
    no_banned_words(reason)
    # 依存サービスの記載がない 503 は応答コードを示す
    assert X._unavailable_reason({"configured": True, "reachable": True, "health_code": 500, "health": {},
                                  "error": None}) == "EDC の /health の応答が HTTP 500"


def test_apply_records_edc_llm():
    with Env(**GEMMA), FakeStatus(200, {"status": "ok", "llm": "ok"}):
        for model, method, want in (("edc", None, ("edc", "gemma4")),
                                    ("gemma4cpu", "edc", ("edc", "gemma4")),
                                    ("gemma4", None, ("standard", "gemma4")),
                                    ("gemma4cpu", "standard", ("standard", "gemma4cpu"))):
            p = SimpleNamespace(model=model, extraction_method=method)
            asyncio.run(X.apply_extraction_method(p))
            assert (p.extraction_method, p.model) == want, (model, method, p)


def test_apply_does_not_stop_when_edc_down():
    # 止めるのは edc_extraction の /health の確認（チャンクが残り、再処理できる今までの扱いを保つ）
    with Env(**GEMMA), FakeStatus(reachable=False):
        p = SimpleNamespace(model="gemma4", extraction_method="edc")
        asyncio.run(X.apply_extraction_method(p))
        assert (p.extraction_method, p.model) == ("edc", "gemma4")
    with Env(EDC_LLM_MODEL=None), FakeStatus(reachable=False):
        p = SimpleNamespace(model="gemma4", extraction_method="edc")
        asyncio.run(X.apply_extraction_method(p))
        assert p.model == "edc-default"


def test_list_methods():
    with Env(**GEMMA), FakeStatus(200, {"status": "ok", "llm": "ok"}, [{"name": "社内規程_就業規則", "description": ""}]):
        r = asyncio.run(X.list_extraction_methods())
    assert r["status"] == "Success"
    std, edc = r["data"]
    assert std["id"] == "standard" and std["available"] is True
    assert edc["available"] is True and edc["llm"] == "gemma4" and edc["llm_id"] == "google/gemma-4-26B-A4B-it"
    assert edc["doctypes"][0]["name"] == "社内規程_就業規則"
    with Env(**GEMMA), FakeStatus(503, {"status": "degraded", "llm": "down: refused", "embedding": "ok"}):
        edc = asyncio.run(X.list_extraction_methods())["data"][1]
    assert edc["available"] is False and "依存サービス（LLM）" in edc["reason"] and edc["llm"] == "gemma4"
    assert edc["reason_detail"].startswith("llm: down")
    with Env(**GEMMA), FakeStatus(reachable=False):
        edc = asyncio.run(X.list_extraction_methods())["data"][1]
    assert edc["available"] is False and "接続できない" in edc["reason"]
    no_banned_words(edc["reason"])


def test_list_methods_keeps_existing_fields():
    # 既存の項目（画面・取り込みツールが参照する）は名前と型を変えない。追加の項目は EDC の項目にのみ付与する
    with Env(**GEMMA), FakeStatus(200, {"status": "ok", "llm": "ok"}):
        std, edc = asyncio.run(X.list_extraction_methods())["data"]
    base = {"id", "label", "available", "llm", "llm_id", "llm_status", "reason", "doctypes"}
    assert set(std) == base
    assert base | {"settings", "vocabulary", "doctypes_source", "reason_detail"} == set(edc)
    assert edc["reason_detail"] is None
    assert (std["label"], edc["label"]) == ("標準", "EDC（スキーマ統制）")


def test_edc_settings_from_env():
    with Env(LLM_MODEL_CONFIG_EDC=EDC_CONFIG_WITH_SECRET, **EDC_FLAGS_ON):
        s = X.edc_settings()
    assert s == {"doctype": "auto", "doctype_mode": "auto", "enrich_schema": True, "schema_mode": "fixed_extended",
                 "fold_unknown_relations": True, "unknown_relations": "fold", "drop_sentence_names": True}
    # 固定の文書種別（2 番目の値に名称を指定）。フラグの未設定は false とみなす（edc_extraction の既定と同じ）
    with Env(LLM_MODEL_CONFIG_EDC="http://edc.test:8081, 社内規程_就業規則 ,recursive", **EDC_FLAGS_UNSET):
        s = X.edc_settings()
    assert (s["doctype"], s["doctype_mode"]) == ("社内規程_就業規則", "fixed")
    assert (s["enrich_schema"], s["schema_mode"], s["fold_unknown_relations"], s["unknown_relations"],
            s["drop_sentence_names"]) == (False, "fixed", False, "keep", False)
    # none は大文字小文字を問わず判定なし。2 番目の値が空または省略の場合は auto
    for config, want in (("http://e,NONE", ("NONE", "none")), ("http://e,,recursive", ("auto", "auto")),
                         ("http://e", ("auto", "auto")), ("http://e,AUTO", ("AUTO", "fixed"))):
        with Env(LLM_MODEL_CONFIG_EDC=config):
            s = X.edc_settings()
        assert (s["doctype"], s["doctype_mode"]) == want, (config, s)
    # 空のフラグは false、yes・1・on は true（edc_extraction の _flag_enabled と同じ）
    with Env(EDC_ENRICH_SCHEMA="", EDC_FOLD_UNKNOWN_RELATIONS="Yes", EDC_DROP_SENTENCE_NAMES=" 1 "):
        s = X.edc_settings()
    assert (s["enrich_schema"], s["fold_unknown_relations"], s["drop_sentence_names"]) == (False, True, True)
    # LLM_MODEL_CONFIG_EDC が未設定の場合、文書種別は判定できない（null）。フラグは環境変数から返す
    with Env(LLM_MODEL_CONFIG_EDC=None, **EDC_FLAGS_ON):
        s = X.edc_settings()
    assert (s["doctype"], s["doctype_mode"], s["schema_mode"]) == (None, None, "fixed_extended")


def test_vocabulary_counts_union_backbone_and_domain():
    with Schemas():
        summary = X.edc_vocabulary_summary()
    assert summary["available"] is True and summary["error"] is None
    assert (summary["backbone_node_label_count"], summary["backbone_relationship_type_count"]) == (3, 2)
    by_name = {d["name"]: d for d in summary["doctypes"]}
    assert list(by_name) == ["種別A", "種別B"]
    # 種別A: ラベルは {Document, Section, Term} ∪ {Equipment, Document, Criterion} = 5、リレーションタイプは {REFERS_TO, PART_OF} ∪ {APPLIES_TO, JUDGED_BY, REFERS_TO} = 4
    assert (by_name["種別A"]["node_label_count"], by_name["種別A"]["relationship_type_count"]) == (5, 4)
    assert by_name["種別A"]["description"] == "説明A"
    # 種別B は schema の CSV がないため件数を null とし、他の文書種別の集計は継続する
    assert (by_name["種別B"]["node_label_count"], by_name["種別B"]["relationship_type_count"]) == (None, None)


def test_vocabulary_summary_never_raises():
    # 定義ファイルのディレクトリがない
    with Env(EDC_SCHEMAS_DIR="/nonexistent-edc-schemas"):
        summary = X.edc_vocabulary_summary()
    assert summary["available"] is False and summary["doctypes"] == []
    assert summary["error"] == "EDC の定義ファイルを読み込めない（FileNotFoundError）"
    assert "/nonexistent" not in summary["error"]
    # registry.json が JSON として不正・backbone がない・doctypes が配列でない
    for kwargs in ({"raw_registry": "{not json"}, {"registry": {"doctypes": []}},
                   {"registry": {**Schemas.REGISTRY, "doctypes": {"name": "x"}}}):
        with Schemas(**kwargs):
            summary = X.edc_vocabulary_summary()
        assert summary["doctypes"] == [], kwargs
    # 想定外の例外も送出しない（文書種別ごとの読み込みで発生した場合は件数のみ null）
    orig = X._vocabulary_names
    calls = {"n": 0}

    def flaky(schemas_dir, path):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("unexpected")
        return orig(schemas_dir, path)
    X._vocabulary_names = flaky
    try:
        with Schemas():
            summary = X.edc_vocabulary_summary()
    finally:
        X._vocabulary_names = orig
    assert summary["available"] is True
    assert all(d["node_label_count"] is None and d["relationship_type_count"] is None for d in summary["doctypes"])


def test_list_methods_offline_uses_registry_and_env():
    # EDC に接続できない場合: 設定は環境変数から、文書種別の一覧と件数は定義ファイルから返す
    with Env(LLM_MODEL_CONFIG_EDC=EDC_CONFIG_WITH_SECRET, **GEMMA, **EDC_FLAGS_ON), Schemas(), FakeStatus(reachable=False):
        r = asyncio.run(X.list_extraction_methods())
    assert r["status"] == "Success"
    edc = r["data"][1]
    assert edc["available"] is False and edc["doctypes_source"] == "registry"
    assert [(d["name"], d["node_label_count"], d["relationship_type_count"]) for d in edc["doctypes"]] == \
        [("種別A", 5, 4), ("種別B", None, None)]
    assert edc["settings"]["doctype_mode"] == "auto" and edc["settings"]["schema_mode"] == "fixed_extended"
    assert edc["settings"]["unknown_relations"] == "fold" and edc["settings"]["drop_sentence_names"] is True
    assert edc["vocabulary"] == {"available": True, "error": None, "backbone_node_label_count": 3,
                                 "backbone_relationship_type_count": 2}
    # URL と資格情報は応答のどこにも含めない
    text = json.dumps(r, ensure_ascii=False)
    for secret in ("edc.test", "edc-secret", "edc-user", "8081", "http://"):
        assert secret not in text, secret


def test_list_methods_attaches_counts_to_edc_doctypes():
    # EDC から一覧を取得できた場合は EDC の一覧を正とし、定義ファイルにない文書種別の件数は null とする
    doctypes = [{"name": "種別A", "description": "EDC の説明"}, {"name": "未登録", "description": ""}]
    with Env(**GEMMA), Schemas(), FakeStatus(200, {"status": "ok", "llm": "ok"}, doctypes):
        edc = asyncio.run(X.list_extraction_methods())["data"][1]
    assert edc["doctypes_source"] == "edc"
    assert edc["doctypes"] == [
        {"name": "種別A", "description": "EDC の説明", "node_label_count": 5, "relationship_type_count": 4},
        {"name": "未登録", "description": "", "node_label_count": None, "relationship_type_count": None}]
    # 定義ファイルも EDC も利用できない場合は空の一覧（出所は null）とし、失敗にはしない
    with Env(EDC_SCHEMAS_DIR="/nonexistent-edc-schemas", **GEMMA), FakeStatus(reachable=False):
        r = asyncio.run(X.list_extraction_methods())
    edc = r["data"][1]
    assert r["status"] == "Success" and edc["doctypes"] == [] and edc["doctypes_source"] is None
    assert edc["vocabulary"]["available"] is False and edc["settings"]["schema_mode"] in ("fixed", "fixed_extended")


def test_real_registry_when_mounted():
    # 試験用コンテナに EDC の定義ファイルが mount されている場合のみ、実物で集計できることを確認する（件数の値は定義ファイルの更新で変わるため固定しない）
    schemas_dir = "/edc_schemas"
    if not os.path.exists(os.path.join(schemas_dir, "registry.json")):
        return
    with Env(EDC_SCHEMAS_DIR=schemas_dir):
        summary = X.edc_vocabulary_summary()
    assert summary["available"] is True and summary["doctypes"], summary
    for d in summary["doctypes"]:
        assert isinstance(d["node_label_count"], int) and d["node_label_count"] >= summary["backbone_node_label_count"], d
        assert isinstance(d["relationship_type_count"], int), d
        assert d["relationship_type_count"] >= summary["backbone_relationship_type_count"], d


def test_resume_guard():
    X.check_resume_method({"extraction_method": "edc", "model": "gemma4"}, "edc")
    X.check_resume_method({"extraction_method": None, "model": "gemma4"}, "standard")  # 記録前の文書は標準
    X.check_resume_method({"model": "edc"}, "edc")                                     # 記録前の EDC の文書
    X.check_resume_method({"extraction_method": "edc"}, None)                          # 方式を渡さない呼び出しは見ない
    message = raises(X.check_resume_method, {"extraction_method": "edc"}, "standard")
    assert "前回は「EDC（スキーマ統制）」、今回は「標準」" in message
    assert "再処理オプションのダイアログで Delete Entities And Start From Beginning を選択" in message
    no_banned_words(message)
    assert "前回は「EDC（スキーマ統制）」" in raises(X.check_resume_method, {"model": "edc"}, "standard")
    assert "前回は「標準」" in raises(X.check_resume_method, {"extraction_method": "standard"}, "edc")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)


def test_edc_options_of():
    # EDC 以外の方式では None。EDC で指定が無ければ None（backend の設定に従う）
    assert X.edc_options_of(SimpleNamespace(extraction_method="standard", edc_doctype="auto", edc_schema_mode="fixed")) is None
    assert X.edc_options_of(SimpleNamespace(extraction_method="edc", edc_doctype=None, edc_schema_mode=None)) is None
    assert X.edc_options_of(SimpleNamespace(extraction_method="edc", edc_doctype=" ", edc_schema_mode="")) is None
    # 指定があればその組を返す（片方だけでもよい）
    assert X.edc_options_of(SimpleNamespace(extraction_method="edc", edc_doctype="none", edc_schema_mode=None)) == {"doctype": "none", "schema_mode": None}
    assert X.edc_options_of(SimpleNamespace(extraction_method="edc", edc_doctype=" 試運転_検査基準 ", edc_schema_mode="fixed")) == {"doctype": "試運転_検査基準", "schema_mode": "fixed"}
    # 運用モードの不正な値は無視する。文書種別に区切り文字や改行があれば無視する
    assert X.edc_options_of(SimpleNamespace(extraction_method="edc", edc_doctype="auto", edc_schema_mode="open")) == {"doctype": "auto", "schema_mode": None}
    assert X.edc_options_of(SimpleNamespace(extraction_method="edc", edc_doctype="a,b", edc_schema_mode="fixed_extended")) == {"doctype": None, "schema_mode": "fixed_extended"}
    # 引数に項目が無い旧い呼び出し方でも落ちない
    assert X.edc_options_of(SimpleNamespace(extraction_method="edc")) is None


def test_edc_extraction_applies_per_request_options(monkeypatch):
    """取り込みごとの指定が EDC への要求（doctype・enrich_schema）に反映される。指定が無ければ backend の設定どおり。"""
    import asyncio as _asyncio
    from src import edc_extraction as E

    class FakeResponse:
        status_code = 200
        text = "ok"
        def json(self):
            return {"triplets": [], "dropped": [], "n_truncated": 0, "n_noise_dropped": 0}
        def raise_for_status(self):
            return None

    sent = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, timeout=None):
            return FakeResponse()
        async def post(self, url, json=None, **k):
            sent.append((url.rsplit("/", 1)[1], json))
            return FakeResponse()

    monkeypatch.setattr(E, "_get_edc_config", lambda: ("http://edc.invalid", "auto", "recursive", 2))
    monkeypatch.setattr(E.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(E, "_flag_enabled", lambda name, default=False: False)
    monkeypatch.setenv("EDC_ENRICH_SCHEMA", "true")
    from langchain_core.documents import Document
    docs = [Document(page_content="本文", metadata={"combined_chunk_ids": ["c1"]})]

    def run(options):
        sent.clear()
        _asyncio.run(E.get_graph_from_edc(docs, options=options))
        posts = [(path, body) for path, body in sent if path == "extract"]
        classified = any(path == "classify" for path, _ in sent)
        return classified, posts[0][1]["doctype"], posts[0][1]["enrich_schema"]

    # 指定なし: backend の設定（auto → 判定 1 回、固定＋拡張）
    classified, doctype, enrich = run(None)
    assert classified and doctype == "UNKNOWN" and enrich is True
    # 文書種別を固定し、運用モードを固定にする: 判定せず、その名前で、enrich_schema=False
    classified, doctype, enrich = run({"doctype": "試運転_検査基準", "schema_mode": "fixed"})
    assert not classified and doctype == "試運転_検査基準" and enrich is False
    # 文書種別なし（語彙を使わず発見）
    classified, doctype, enrich = run({"doctype": "none", "schema_mode": None})
    assert not classified and doctype is None and enrich is True
    # 運用モードだけ指定
    classified, doctype, enrich = run({"doctype": None, "schema_mode": "fixed"})
    assert classified and enrich is False


def test_effective_edc_options_records_request_or_backend_default(monkeypatch):
    """文書ノードに記録する EDC の初期スキーマ（11/9）: 取り込みごとの指定が優先、無ければ backend の設定。"""
    from types import SimpleNamespace
    from src import extraction_methods as M

    p = SimpleNamespace(extraction_method="edc", edc_doctype="試運転_検査基準", edc_schema_mode="fixed")
    assert M.effective_edc_options(p) == ("試運転_検査基準", "fixed")
    monkeypatch.setenv("LLM_MODEL_CONFIG_EDC", "http://edc:8080,auto,recursive,2")
    monkeypatch.setenv("EDC_ENRICH_SCHEMA", "true")
    p = SimpleNamespace(extraction_method="edc", edc_doctype=None, edc_schema_mode=None)
    assert M.effective_edc_options(p) == ("auto", "fixed_extended")
    assert M.effective_edc_options(SimpleNamespace(extraction_method="standard", edc_doctype="x", edc_schema_mode="fixed")) == (None, None)
