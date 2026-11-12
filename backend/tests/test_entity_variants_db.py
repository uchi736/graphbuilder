# -*- coding: utf-8 -*-
"""表記ゆれのエンティティの統合（src/entity_variants.py）を使い捨ての空の Neo4j（APOC 入り）で流す試験。LLM は使わない。

本番・試験用の共有の Neo4j では流さない（後処理はグラフ全体を見るので）。始める前に DB が空であることを確かめ、最後に全部消す。
仮データ: 文書 1・チャンク 3・エンティティ 9
  組 1 V-00862〔DocumentRef〕(言及 2)・V -00862〔DocumentRef〕(言及 1)・V00862〔DocumentRef〕(言及 1)
       → V-00862 を残す。同じチャンク c1 からの HAS_ENTITY は 1 本にまとまる。組の中どうしの関係は消える。外への関係は付け替える
  組 2 ±3%以内〔Criterion〕(言及 1)・± 3 %以内〔Criterion〕(言及 1) → 同数なので名前の短い ±3%以内
  別物 V (m3/s)〔Parameter〕・V(m3/s)〔Term〕 → 統合しない
  対象外 HZTL 20559・HZTL20559〔Standard, method=rule〕 → 統合しない
確かめること
  (1) dry_run は書き込まない (2) 残す側・aliases・_v・関係の付け替えと重複のまとめ・自己ループなし
  (3) 2 回目は何もしない（冪等） (4) 台帳から戻すと元の関係とノードに戻る
実行: 使い捨ての Neo4j を立て、環境変数 ENTITY_VARIANTS_TEST_URI・ENTITY_VARIANTS_TEST_PASSWORD で渡す（無ければ飛ばす）
"""
import os
import tempfile

import pytest

TEST_URI = os.environ.get("ENTITY_VARIANTS_TEST_URI")
TEST_PASSWORD = os.environ.get("ENTITY_VARIANTS_TEST_PASSWORD")

pytestmark = pytest.mark.skipif(not (TEST_URI and TEST_PASSWORD), reason="使い捨ての Neo4j が無い（ENTITY_VARIANTS_TEST_URI）")

SETUP = """
CREATE (d:Document {fileName: 'doc.pdf', status: 'Completed'})
CREATE (c1:Chunk {id: 'c1', fileName: 'doc.pdf', position: 1, text: 'V-00862 と V -00862 と V00862'})-[:PART_OF]->(d)
CREATE (c2:Chunk {id: 'c2', fileName: 'doc.pdf', position: 2, text: 'V-00862 ±3%以内'})-[:PART_OF]->(d)
CREATE (c3:Chunk {id: 'c3', fileName: 'doc.pdf', position: 3, text: '± 3 %以内 V (m3/s) V(m3/s)'})-[:PART_OF]->(d)
CREATE (a:__Entity__:DocumentRef {id: 'V-00862', description: '試験方案', embedding: [0.1, 0.2]})
CREATE (b:__Entity__:DocumentRef {id: 'V -00862', description: '試験方案（空白入り）', aliases: ['V－００８６２']})
CREATE (c:__Entity__:DocumentRef {id: 'V00862'})
CREATE (k1:__Entity__:Criterion {id: '±3%以内', embedding: [0.3, 0.4]})
CREATE (k2:__Entity__:Criterion {id: '± 3 %以内', description: '許容差'})
CREATE (p:__Entity__:Parameter {id: 'V (m3/s)'})
CREATE (t:__Entity__:Term {id: 'V(m3/s)'})
CREATE (s1:__Entity__:Standard {id: 'HZTL 20559', method: 'rule'})
CREATE (s2:__Entity__:Standard {id: 'HZTL20559', method: 'rule'})
CREATE (x:__Entity__:Equipment {id: '過給機'})
CREATE (c1)-[:HAS_ENTITY]->(a), (c2)-[:HAS_ENTITY]->(a)
CREATE (c1)-[:HAS_ENTITY {mention: 'V -00862'}]->(b)
CREATE (c1)-[:HAS_ENTITY]->(c)
CREATE (c2)-[:HAS_ENTITY]->(k1), (c3)-[:HAS_ENTITY]->(k2)
CREATE (c3)-[:HAS_ENTITY]->(p), (c3)-[:HAS_ENTITY]->(t)
CREATE (c1)-[:HAS_ENTITY]->(s1), (c2)-[:HAS_ENTITY]->(s2)
CREATE (a)-[:APPLIES_TO]->(x), (b)-[:APPLIES_TO]->(x), (c)-[:DEFINES]->(x)
CREATE (b)-[:SAME_AS]->(a)
CREATE (k2)-[:CRITERION_OF]->(x)
"""


@pytest.fixture(scope="module")
def graph():
    from langchain_neo4j import Neo4jGraph
    g = Neo4jGraph(url=TEST_URI, username="neo4j", password=TEST_PASSWORD, database="neo4j", refresh_schema=False, sanitize=True)
    if g.query("MATCH (n) RETURN count(n) AS n")[0]["n"] != 0:
        raise RuntimeError("この試験は空の使い捨ての Neo4j でしか流さない")
    g.query("CREATE CONSTRAINT ev_entity_id IF NOT EXISTS FOR (e:__Entity__) REQUIRE e.id IS UNIQUE")
    g.query(SETUP)
    yield g
    g.query("MATCH (n) DETACH DELETE n")


def rels(g):
    """(始点, 種類, 終点) の一覧（チャンク・文書・エンティティを名前で）。"""
    return sorted((r["a"], r["t"], r["b"]) for r in g.query(
        "MATCH (a)-[r]->(b) RETURN coalesce(a.id, a.fileName) AS a, type(r) AS t, coalesce(b.id, b.fileName) AS b"))


def entities(g):
    return {r["id"]: r for r in g.query(
        "MATCH (e:__Entity__) RETURN e.id AS id, apoc.coll.sort(labels(e)) AS labels, e.aliases AS aliases, e._v AS v, e.description AS description")}


def test_merge_restore_idempotent(graph):
    from src.entity_variants import merge_entity_variants, restore_entity_variants
    before_rels, before_ents = rels(graph), entities(graph)
    with tempfile.TemporaryDirectory() as tmp:
        # (1) dry_run
        out = merge_entity_variants(graph, dry_run=True, ledger_dir=tmp)
        assert out["ledger"] is None and rels(graph) == before_rels and entities(graph) == before_ents
        got = [(g["keep"]["id"], [m["id"] for m in g["merge"]]) for g in out["groups"]]
        assert got == [("V-00862", ["V00862", "V -00862"]), ("±3%以内", ["± 3 %以内"])], got
        assert [c["key"] for c in out["cross_type"]] == ["v(m3/s)"]

        # (2) 統合
        out = merge_entity_variants(graph, ledger_dir=tmp)
        assert out["summary"]["merged"] == 3 and os.path.exists(out["ledger"])
        ents = entities(graph)
        assert "V -00862" not in ents and "V00862" not in ents and "± 3 %以内" not in ents
        assert {"V (m3/s)", "V(m3/s)", "HZTL 20559", "HZTL20559"} <= set(ents)
        assert ents["V-00862"]["aliases"] == ["V -00862", "V－００８６２", "V00862"]
        assert ents["V-00862"]["v"] == 1 and ents["V-00862"]["description"] == "試験方案"
        assert ents["±3%以内"]["aliases"] == ["± 3 %以内"] and ents["±3%以内"]["description"] == "許容差"
        r = rels(graph)
        assert r.count(("c1", "HAS_ENTITY", "V-00862")) == 1          # 同じチャンクから 3 本 → 1 本
        assert r.count(("V-00862", "APPLIES_TO", "過給機")) == 1        # 同じ相手・同じ種類 → 1 本
        assert ("V-00862", "DEFINES", "過給機") in r                     # 付け替え
        assert ("c3", "HAS_ENTITY", "±3%以内") in r and ("±3%以内", "CRITERION_OF", "過給機") in r
        assert not [x for x in r if x[0] == x[2]]                       # 自己ループなし
        assert graph.query("MATCH (n)-[r]->(n) RETURN count(r) AS n")[0]["n"] == 0
        # 統合で説明が変わったノード（±3%以内 は説明が無く、統合した側の説明を引き継いだ）の埋め込みは消え、変わらないものは残る
        emb = {x["id"]: x["e"] for x in graph.query("MATCH (e:__Entity__) WHERE e.id IN ['V-00862', '±3%以内'] RETURN e.id AS id, e.embedding IS NOT NULL AS e")}
        assert emb == {"V-00862": True, "±3%以内": False}
        after_rels = r

        # (3) 2 回目は何もしない
        out2 = merge_entity_variants(graph, ledger_dir=tmp)
        assert out2["summary"]["groups"] == 0 and out2["ledger"] is None
        assert rels(graph) == after_rels and entities(graph) == ents

        # (4) 戻す
        res = restore_entity_variants(graph, out["ledger"])
        assert res["summary"]["restored"] == 3 and res["summary"]["missing_relationships"] == 0
        assert rels(graph) == before_rels
        back = entities(graph)
        assert set(back) == set(before_ents)
        for k, v in before_ents.items():
            assert (back[k]["labels"], back[k]["aliases"], back[k]["description"]) == (v["labels"], v["aliases"], v["description"]), k
        # 関係のプロパティも戻る
        assert graph.query("MATCH (:Chunk {id:'c1'})-[r:HAS_ENTITY]->(:__Entity__ {id:'V -00862'}) RETURN r.mention AS m")[0]["m"] == "V -00862"
