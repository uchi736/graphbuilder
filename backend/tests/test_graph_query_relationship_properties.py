# -*- coding: utf-8 -*-
"""全文書のグラフの応答（src/graph_query.extract_relationships）で、文書層のリレーションのプロパティを返すことの単体試験。DB には接続しない。
文書層のリレーション（IS_VERSION_OF・REFERS_TO・LISTS のうち始点が文書のもの）のみ properties を付与し、他のリレーションの応答は従来どおりとする。
プロパティを変換できない場合も、リレーション自体は応答に含める。
"""
import json
import os
import sys

from neo4j import time as neo4j_time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from src import graph_query as gq  # noqa: E402


class FakeNode(dict):
    """process_node が参照する element_id・labels とプロパティのみを持つ試験用のノード。"""

    def __init__(self, element_id, labels, **properties):
        super().__init__(properties)
        self.element_id = element_id
        self.labels = labels


class FakeRelationship(dict):
    """extract_relationships が参照する element_id・type・nodes とプロパティのみを持つ試験用のリレーション。"""

    def __init__(self, element_id, rel_type, start, end, **properties):
        super().__init__(properties)
        self.element_id = element_id
        self.type = rel_type
        self.nodes = (start, end)


class BrokenRelationship(FakeRelationship):
    """プロパティの読み取りで例外を送出するリレーション。"""

    def get(self, key, default=None):
        raise RuntimeError("broken property")


DOC_A = FakeNode("4:doc:1", ["Document"], fileName="A.pdf")
DOC_B = FakeNode("4:doc:2", ["Document"], fileName="B.pdf")
NUMBER = FakeNode("4:std:1", ["Standard", "__Entity__"], id="HS-VTR-T001", method="rule")
CHUNK = FakeNode("4:chunk:1", ["Chunk"], position=1)


def _by_id(relationships):
    return {rel["element_id"]: rel for rel in relationships}


def test_document_layer_relationships_include_properties():
    records = [{"rels": [
        FakeRelationship("5:r:1", "IS_VERSION_OF", DOC_B, NUMBER, rev="E", method="rule"),
        FakeRelationship("5:r:2", "REFERS_TO", DOC_A, NUMBER, method="rule", chunks=3, quotes=["HS-VTR-T001 による"]),
        FakeRelationship("5:r:3", "LISTS", DOC_A, NUMBER, rev="E", date=neo4j_time.Date(2026, 10, 1), method="rule"),
    ]}]
    result = _by_id(gq.extract_relationships(records))
    assert result["5:r:1"]["properties"] == {"rev": "E", "method": "rule"}
    assert result["5:r:2"]["properties"] == {"method": "rule", "chunks": 3, "quotes": ["HS-VTR-T001 による"]}
    assert result["5:r:3"]["properties"]["date"] == "2026-10-01"
    assert result["5:r:3"]["start_node_element_id"] == "4:doc:1"
    assert result["5:r:3"]["end_node_element_id"] == "4:std:1"
    # 応答は JSON に変換できる
    json.dumps(list(result.values()), ensure_ascii=False)


def test_other_relationship_types_are_unchanged():
    records = [{"rels": [FakeRelationship("5:r:9", "PART_OF", CHUNK, DOC_A, note="x")]}]
    (rel,) = gq.extract_relationships(records)
    assert rel == {"element_id": "5:r:9", "type": "PART_OF", "start_node_element_id": "4:chunk:1",
                   "end_node_element_id": "4:doc:1"}


def test_unconvertible_properties_keep_relationship():
    records = [{"rels": [BrokenRelationship("5:r:7", "REFERS_TO", DOC_A, NUMBER, method="rule")]}]
    (rel,) = gq.extract_relationships(records)
    assert rel["element_id"] == "5:r:7"
    assert "properties" not in rel


def test_json_value_converts_neo4j_temporal_and_unknown_types():
    assert gq._json_value(neo4j_time.DateTime(2026, 11, 8, 9, 30, 0)).startswith("2026-11-08T09:30:00")
    assert gq._json_value(neo4j_time.Duration(days=2)) == "P2D"
    assert gq._json_value([neo4j_time.Date(2026, 1, 2), 1, None]) == ["2026-01-02", 1, None]
    assert gq._json_value(object()).startswith("<object object")


def test_chunk_level_refers_to_has_no_properties():
    # チャンク間・チャンクから節・文書への REFERS_TO（参照リンク）は画面で使用しないため、プロパティを返さない
    chunk_b = FakeNode("4:chunk:2", ["Chunk"], position=2)
    section = FakeNode("4:sec:1", ["Section"], title="2. 試験条件")
    records = [{"rels": [
        FakeRelationship("5:r:8", "REFERS_TO", CHUNK, chunk_b, quote="別表2による", method="llm"),
        FakeRelationship("5:r:10", "REFERS_TO", CHUNK, section, quote="2. による", method="rule"),
        FakeRelationship("5:r:11", "REFERS_TO", CHUNK, DOC_B, quote="B による", method="llm"),
    ]}]
    result = _by_id(gq.extract_relationships(records))
    assert set(result) == {"5:r:8", "5:r:10", "5:r:11"}
    assert all("properties" not in rel for rel in result.values())
    assert result["5:r:8"] == {"element_id": "5:r:8", "type": "REFERS_TO", "start_node_element_id": "4:chunk:1",
                               "end_node_element_id": "4:chunk:2"}
