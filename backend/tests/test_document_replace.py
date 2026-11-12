# -*- coding: utf-8 -*-
"""文書の取り込み直しと削除の後始末（C8, src/document_replace.py）の試験。
試験用の Neo4j に小さな文書の組を作り、消える物・残る物を数えてから、作った物を消す。本番の Neo4j（database:7687）には書かない。
実行（本番の backend は再起動しない）:
  docker start neo4j-gbdev
  docker run --rm --memory=1g --network llm-graph-builder_net \
    -e C8_NEO4J_URI=neo4j://database-gbdev:7687 -e C8_NEO4J_PASSWORD=gbdev-test-only \
    -v ~/ai-stack/llm-graph-builder/backend:/code:ro -w /code llm-graph-builder-backend \
    python -m pytest -q -p no:cacheprovider tests/test_document_replace.py
"""
import os
import sys
import types
import uuid

import pytest

URI = os.environ.get("C8_NEO4J_URI", "")
pytestmark = pytest.mark.skipif(not URI or "//database:" in URI, reason="試験用の Neo4j（C8_NEO4J_URI）を指定する。本番には書かない")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# 重い依存（transformers など）を読まないよう、execute_graph_query だけの代わりを置く（この試験は単独で流す）
sys.modules.setdefault("src.shared.common_fn", types.SimpleNamespace(execute_graph_query=lambda g, q, params=None: g.query(q, params)))
from src import document_replace as DR  # noqa: E402

# 文書 A を取り込み直す・消す側、B を残る側とする小さな組
FIXTURE = """
CREATE (A:Document {fileName: $A, status: 'Completed', fileSource: 'local file'}),
       (B:Document {fileName: $B, status: 'Completed', fileSource: 'local file'}),
       (a1:Chunk {id: $T + '-a1', fileName: $A}), (a2:Chunk {id: $T + '-a2', fileName: $A}),
       (b1:Chunk {id: $T + '-b1', fileName: $B}), (s1:Chunk {id: $T + '-s1', fileName: $B}),
       (a1)-[:PART_OF]->(A), (a2)-[:PART_OF]->(A), (b1)-[:PART_OF]->(B), (s1)-[:PART_OF]->(A), (s1)-[:PART_OF]->(B),
       (A)-[:FIRST_CHUNK]->(s1),
       (eA:__Entity__:Thing {id: $T + '-eA'}), (eAB:__Entity__:Thing {id: $T + '-eAB'}), (eS:__Entity__:Thing {id: $T + '-eS'}),
       (std:__Entity__:Standard {id: $T + '-std', method: 'rule'}), (mdl:__Entity__:Model {id: $T + '-mdl'}),
       (cur:__Entity__:Thing {id: $T + '-cur', _curated: true}), (secE:__Entity__:Section {id: $T + '-secE', fileName: $A}),
       (a1)-[:HAS_ENTITY]->(eA), (a1)-[:HAS_ENTITY]->(eAB), (b1)-[:HAS_ENTITY]->(eAB), (s1)-[:HAS_ENTITY]->(eS),
       (a2)-[:HAS_ENTITY]->(std), (a2)-[:HAS_ENTITY]->(mdl), (a2)-[:HAS_ENTITY]->(cur),
       (a2)-[:HAS_ENTITY]->(secE), (b1)-[:HAS_ENTITY]->(secE),
       (r1:Section {id: $T + '-r1', fileName: $A, method: 'rule'}), (r2:Section {id: $T + '-r2', fileName: $A, method: 'rule'}),
       (rB:Section {id: $T + '-rB', fileName: $B, method: 'rule'}),
       (A)-[:HAS_SECTION]->(r1), (r1)-[:HAS_SECTION]->(r2), (a1)-[:IN_SECTION]->(r1), (B)-[:HAS_SECTION]->(rB),
       (A)-[:IS_VERSION_OF {method: 'rule'}]->(std), (A)-[:REFERS_TO {method: 'rule'}]->(std),
       (A)-[:APPLIES_TO {method: 'rule'}]->(mdl), (A)-[:REPLACES {method: 'rule'}]->(B), (B)-[:REPLACES {method: 'rule'}]->(A),
       (A)-[:REFERS_TO {method: 'llm'}]->(eAB),
       (B)-[:IS_VERSION_OF {method: 'rule'}]->(std), (B)-[:APPLIES_TO {method: 'rule'}]->(mdl)
"""


class _Graph:
    """Neo4jGraph.query と同じ形（辞書の list）を返す最小の代わり"""

    def __init__(self):
        from neo4j import GraphDatabase
        self.db = os.environ.get("C8_NEO4J_DATABASE", "neo4j")
        self.driver = GraphDatabase.driver(URI, auth=(os.environ.get("C8_NEO4J_USER", "neo4j"), os.environ.get("C8_NEO4J_PASSWORD", "")))

    def query(self, q, params=None):
        records, _, _ = self.driver.execute_query(q, params or {}, database_=self.db)
        return [r.data() for r in records]


@pytest.fixture
def g():
    graph = _Graph()
    graph.t = "c8test-" + uuid.uuid4().hex[:8]
    graph.A, graph.B = graph.t + "-A.pdf", graph.t + "-B.pdf"
    graph.query(FIXTURE, {"T": graph.t, "A": graph.A, "B": graph.B})
    yield graph
    graph.query("MATCH (n) WHERE n.fileName STARTS WITH $t OR n.id STARTS WITH $t DETACH DELETE n", {"t": graph.t})
    graph.driver.close()


def one(g, q, **kw):
    return g.query(q, {"t": g.t, "A": g.A, "B": g.B, **kw})[0]


def left_ids(g):
    return set(one(g, "MATCH (n) WHERE n.id STARTS WITH $t RETURN collect(n.id) AS ids")["ids"])


def test_clear_previous_chunks(g):
    assert DR.clear_previous_chunks(g, g.A) == {"chunks": 2, "entities": 1, "sections": 2, "rule_edges": 5}
    t, left = g.t, left_ids(g)
    assert {t + s for s in ["-a1", "-a2", "-eA", "-r1", "-r2"]}.isdisjoint(left)
    assert {t + s for s in ["-s1", "-b1", "-eAB", "-eS", "-std", "-mdl", "-cur", "-secE", "-rB"]} <= left
    assert one(g, "MATCH (d:Document {fileName: $A}) RETURN count(d) AS n")["n"] == 1
    assert one(g, "MATCH (:Chunk {id: $t + '-s1'})-[:PART_OF]->(d) RETURN collect(d.fileName) AS f")["f"] == [g.B]
    assert one(g, "MATCH (:Document {fileName: $A})-[r]->() RETURN collect(type(r) + ':' + coalesce(r.method, '')) AS r")["r"] == ["REFERS_TO:llm"]
    assert one(g, "MATCH (:Document {fileName: $B})-[r:IS_VERSION_OF|APPLIES_TO]->() RETURN count(r) AS n")["n"] == 2


def test_processing_document_is_not_cleared(g):
    g.query("MATCH (d:Document {fileName: $A}) SET d.status = 'Processing'", {"A": g.A})
    assert DR.clear_previous_chunks(g, g.A) == {"chunks": 0, "entities": 0, "sections": 0, "rule_edges": 0}
    assert one(g, "MATCH (:Document {fileName: $A})<-[:PART_OF]-(c) RETURN count(c) AS n")["n"] == 3


def test_delete_rule_layer_and_sweep(g):
    assert DR.delete_rule_layer(g, [g.A]) == {"rule_edges": 5, "sections": 2}
    assert {g.t + "-secE", g.t + "-rB"} <= left_ids(g)
    # 本家の削除が文書ノードだけを消し、チャンクを残した状態
    g.query("MATCH (d:Document {fileName: $A}) DETACH DELETE d", {"A": g.A})
    assert DR.sweep_deleted_documents(g, [g.A], ["local file"]) == {"documents": 0, "chunks": 2}
    # 文書ノードごと残った状態（B のチャンク b1 と、A が消えて B だけのものになった s1）
    assert DR.sweep_deleted_documents(g, [g.B], ["local file"]) == {"documents": 1, "chunks": 2}
