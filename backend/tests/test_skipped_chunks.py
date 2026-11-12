"""抽出に失敗したチャンクの記録（T16）の試験。DB にも LLM にもつながない。

確かめること
  (1) 入れ物: 重複を足さない、None を飛ばす、begin() の前の note() と forget() は何もしない
  (2) 続きから: 前回の一覧で始め、処理し直すバッチの分だけ forget() で外す。previous_ids は 1 行に 1 件で読む
  (3) 同時に動く 2 つのタスク（2 つの文書の取り込み）で一覧が混ざらない
  (4) LLM の抽出（llm.get_graph_document_list）が失敗したチャンクの ID を記録する。結合したチャンクなら全部の ID
  (5) 全部のチャンクが失敗したときも、例外の前に ID を記録する
  (6) EDC の抽出（edc_extraction.get_graph_from_edc）も同じく記録する
  (7) save() が件数と ID を文書に書く。書けなくても例外にしない

実行: 本番の backend を読み取り専用で載せた一時コンテナで（--network none でよい）
    python -m pytest -q tests/test_skipped_chunks.py
"""
import asyncio
import contextvars
import types

import pytest
from langchain_core.documents import Document

from src import edc_extraction as edc_module
from src import llm as llm_module
from src import skipped_chunks
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException


def fresh(fn):
    """入れ物の無い新しい文脈で fn を動かす（ほかの試験の begin() の影響を受けない）"""
    return contextvars.Context().run(fn)


def chunk_list(texts):
    return [{"chunk_id": f"c{i}", "chunk_doc": Document(page_content=t)} for i, t in enumerate(texts, 1)]


# ---------- (1)(2) 入れ物 ----------

def test_note_without_begin_does_nothing():
    def run():
        skipped_chunks.note(["c1"])
        return skipped_chunks.current()
    assert fresh(run) == []


def test_note_skips_duplicates_and_none():
    def run():
        skipped_chunks.begin()
        skipped_chunks.note(["c1", None, "c2"])
        skipped_chunks.note(["c2", "", "c3"])
        skipped_chunks.note(None)
        return skipped_chunks.current()
    assert fresh(run) == ["c1", "c2", "c3"]


def test_begin_keeps_previous_and_forget_removes_only_the_batch():
    def run():
        skipped_chunks.begin(["a", "b", "a", None, "c"])
        first = skipped_chunks.current()
        removed = skipped_chunks.forget(["b", "x"])
        after = skipped_chunks.current()
        skipped_chunks.note(["c", "d"])
        return first, removed, after, skipped_chunks.current()
    assert fresh(run) == (["a", "b", "c"], ["b"], ["a", "c"], ["a", "c", "d"])


def test_forget_without_begin_does_nothing():
    assert fresh(lambda: skipped_chunks.forget(["c1"])) == []


def test_previous_ids_reads_one_row_per_id():
    class RowsGraph:
        _database = "neo4j"

        def __init__(self):
            self.calls = []

        def query(self, query, params=None, session_params=None):
            self.calls.append((query, params, session_params))
            return [{"id": "c3"}, {"id": "c1"}, {"id": None}]

    graph = RowsGraph()
    assert skipped_chunks.previous_ids(graph, "doc.pdf") == ["c3", "c1"]
    query, params, session_params = graph.calls[0]
    assert "UNWIND" in query and "RETURN ids[i] AS id ORDER BY i" in query  # リストのまま返さない（sanitize が 128 件以上を落とす）
    assert params == {"f": "doc.pdf"} and session_params == {"database": "neo4j"}


def test_begin_again_starts_empty():
    def run():
        skipped_chunks.begin()
        skipped_chunks.note(["c1"])
        skipped_chunks.begin()
        return skipped_chunks.current()
    assert fresh(run) == []


# ---------- (3) 同時に動く 2 つの取り込み ----------

def test_two_tasks_do_not_mix():
    async def one(ids):
        skipped_chunks.begin()
        await asyncio.sleep(0)
        skipped_chunks.note(ids)
        await asyncio.sleep(0)
        return skipped_chunks.current()

    async def run():
        return await asyncio.gather(one(["a1"]), one(["b1", "b2"]))

    assert fresh(lambda: asyncio.run(run())) == [["a1"], ["b1", "b2"]]


# ---------- (4)(5) LLM の抽出 ----------

class FakeLLM:
    """構造化出力に対応する LLM のふり（with_structured_output が例外を出さない）"""

    def with_structured_output(self, schema):
        return self


class FakeTransformer:
    """LLMGraphTransformer の代わり。本文に「壊れ」を含むチャンクで例外を出す"""

    def __init__(self, **kwargs):
        pass

    async def aconvert_to_graph_documents(self, docs):
        doc = docs[0]
        if "壊れ" in doc.page_content:
            raise ValueError("出力を解釈できない")
        return [f"gd:{'+'.join(doc.metadata['combined_chunk_ids'])}"]


def run_llm(docs):
    async def run():
        skipped_chunks.begin()
        try:
            out, _usage = await llm_module.get_graph_document_list(FakeLLM(), docs, [], [], None)
            return out, skipped_chunks.current(), None
        except LLMGraphBuilderException as e:
            return None, skipped_chunks.current(), e
    return fresh(lambda: asyncio.run(run()))


def test_llm_records_failed_chunk(monkeypatch):
    monkeypatch.setattr(llm_module, "LLMGraphTransformer", FakeTransformer)
    docs = llm_module.get_combined_chunks(chunk_list(["正常", "壊れ", "正常"]), 1)
    out, ids, err = run_llm(docs)
    assert err is None
    assert out == ["gd:c1", "gd:c3"]
    assert ids == ["c2"]


def test_llm_records_all_ids_of_combined_chunk(monkeypatch):
    monkeypatch.setattr(llm_module, "LLMGraphTransformer", FakeTransformer)
    docs = llm_module.get_combined_chunks(chunk_list(["正常", "壊れ", "正常"]), 2)  # [c1+c2] と [c3]
    out, ids, err = run_llm(docs)
    assert err is None
    assert out == ["gd:c3"]
    assert ids == ["c1", "c2"]


def test_llm_records_before_raising_when_all_fail(monkeypatch):
    monkeypatch.setattr(llm_module, "LLMGraphTransformer", FakeTransformer)
    docs = llm_module.get_combined_chunks(chunk_list(["壊れ", "壊れ"]), 1)
    out, ids, err = run_llm(docs)
    assert isinstance(err, LLMGraphBuilderException)
    assert ids == ["c1", "c2"]


def test_llm_without_failures_records_nothing(monkeypatch):
    monkeypatch.setattr(llm_module, "LLMGraphTransformer", FakeTransformer)
    docs = llm_module.get_combined_chunks(chunk_list(["正常", "正常"]), 1)
    out, ids, err = run_llm(docs)
    assert err is None and ids == [] and out == ["gd:c1", "gd:c2"]


# ---------- (6) EDC の抽出 ----------

class FakeResponse:
    status_code = 200
    text = "ok"


class FakeClient:
    """httpx.AsyncClient の代わり。/health に 200 を返すだけ"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, timeout=None):
        return FakeResponse()


async def fake_extract_chunk(client, semaphore, base_url, doc, doctype, chunk_method, options=None):
    if "壊れ" in doc.page_content:
        raise RuntimeError("EDC が 500 を返した")
    return types.SimpleNamespace(relationships=[]), {"truncated": 0, "noise": 0, "dropped": 0}


def test_edc_records_failed_chunk(monkeypatch):
    monkeypatch.setattr(edc_module, "_get_edc_config", lambda: ("http://edc.invalid", "standard", "recursive", 2))
    monkeypatch.setattr(edc_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(edc_module, "_extract_chunk", fake_extract_chunk)
    docs = llm_module.get_combined_chunks(chunk_list(["正常", "壊れ", "壊れ", "正常"]), 1)

    async def run():
        skipped_chunks.begin()
        out, _usage = await edc_module.get_graph_from_edc(docs)
        return len(out), skipped_chunks.current()

    assert fresh(lambda: asyncio.run(run())) == (2, ["c2", "c3"])


# ---------- (7) 文書への書き込み ----------

class FakeGraph:
    _database = "neo4j"

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def query(self, query, params=None, session_params=None):
        self.calls.append((query, params, session_params))
        if self.fail:
            raise RuntimeError("DB に届かない")
        return []


def test_save_writes_count_and_ids():
    graph = FakeGraph()

    def run():
        skipped_chunks.begin()
        skipped_chunks.note(["c2", "c5"])
        return skipped_chunks.save(graph, "doc.pdf")

    assert fresh(run) == 2
    query, params, session_params = graph.calls[0]
    assert "SET d.skipped_chunks = $n, d.skipped_chunk_ids = $ids" in query
    assert params == {"f": "doc.pdf", "n": 2, "ids": ["c2", "c5"]}
    assert session_params == {"database": "neo4j"}


def test_save_writes_zero_to_reset():
    graph = FakeGraph()

    def run():
        skipped_chunks.begin()
        return skipped_chunks.save(graph, "doc.pdf")

    assert fresh(run) == 0
    assert graph.calls[0][1] == {"f": "doc.pdf", "n": 0, "ids": []}


def test_save_does_not_raise_when_db_fails(caplog):
    graph = FakeGraph(fail=True)

    def run():
        skipped_chunks.begin()
        skipped_chunks.note(["c1"])
        return skipped_chunks.save(graph, "doc.pdf")

    with caplog.at_level("WARNING"):
        assert fresh(run) == 1
    assert any("書けなかった" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("module", [llm_module, edc_module])
def test_modules_import_the_recorder(module):
    assert module.skipped_chunks is skipped_chunks
