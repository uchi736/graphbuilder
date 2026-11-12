# -*- coding: utf-8 -*-
"""由来情報と安定キーの後処理（attach_provenance）を試験用 Neo4j（neo4j-gbdev）で流す試験。LLM は使わない。

試験用 Neo4j には他の試験のデータがあるので、ここで作るノードは fileName・id の接頭辞 night1109- で区別し、
attach_provenance も file_names でこの試験の文書だけに絞って流す。最後に作ったノードを消し、索引 chunk_stable_key も
この試験が作った場合は消す（試験の前と同じ状態に戻す）。

仮データ: 文書 1・チャンク 3・エンティティ 4・抽出したリレーション 3（同じ文 / 前後 1 文 / 範囲外）・節 2、
          除外の確認用に規則のリレーション 1（method='rule'）。
確かめること
  (1) dry_run は書き込まない（言及・根拠・安定キー・索引のいずれも付かない）が、付けたとしたらの集計は返す
  (2) HAS_ENTITY の mention・mention_found・mention_source（全角・空白の違い、aliases、見つからない場合）
  (3) リレーションの source_chunk_id・evidence・evidence_kind・provenance_method（relation_mode='true' で書かせる）。規則のリレーションには付けない
  (4) relation_mode='false' ではリレーションに書かない
  (5) Chunk.stable_key・doc_ordinal（primary でない IN_SECTION は使わない）と索引 chunk_stable_key
  (6) 2 回流しても同じ値（冪等）
実行: 試験用 Neo4j のパスワードを環境変数 GBDEV_TEST_PASSWORD で渡す（無ければ飛ばす）
    docker exec -e GBDEV_TEST_PASSWORD=... -w /code backend python -m pytest -q -p no:cacheprovider tests/test_provenance_db.py
"""
import os

import pytest

TEST_URI = os.environ.get("GBDEV_TEST_URI", "neo4j://database-gbdev:7687")
TEST_USER = "neo4j"
TEST_PASSWORD = os.environ.get("GBDEV_TEST_PASSWORD")
TEST_DATABASE = "neo4j"
PREFIX = "night1109-"
DOC = PREFIX + "試験文書.pdf"

if "gbdev" not in TEST_URI:
    raise RuntimeError(f"この試験は試験用 Neo4j（database-gbdev）でしか流さない: {TEST_URI}")

C1 = "[night1109-試験文書 > 1 適用]\n## 1 適用\nこの基準はＮＩＧＨＴ１１０９－甲形 過給機の単独試運転に適用し、判定はnight1109-検査部門が行う。詳細は別に定める。"
C2 = "[night1109-試験文書 > 2 要領 > 2-1 性能試験]\n性能試験はnight1109-検査部門が実施する。\n計測にはnight1109-圧力計を用いる。結果は記録する。"
C3 = "[night1109-試験文書 > 2 要領 > 2-2 記録]\n|項目|計器|\n|---|---|\n|吐出圧力|night1109-圧 力 計|\n記録は保管する。\n保管の責任はnight1109-品管課が負う。"

SETUP = """
CREATE (d:Document {fileName: $doc, status: 'Completed'})
CREATE (c1:Chunk {id: $p + 'c1', fileName: $doc, position: 1, text: $c1})-[:PART_OF]->(d)
CREATE (c2:Chunk {id: $p + 'c2', fileName: $doc, position: 2, text: $c2})-[:PART_OF]->(d)
CREATE (c3:Chunk {id: $p + 'c3', fileName: $doc, position: 3, text: $c3})-[:PART_OF]->(d)
CREATE (c1)-[:NEXT_CHUNK]->(c2), (c2)-[:NEXT_CHUNK]->(c3)
CREATE (e1:__Entity__:Equipment {id: $p + '甲形過給機'})
CREATE (e2:__Entity__:Organization {id: $p + '検査部門'})
CREATE (e3:__Entity__:Instrument {id: $p + '圧力計'})
CREATE (e4:__Entity__:Organization {id: $p + '品質管理課', aliases: [$p + '品管課']})
CREATE (c1)-[:HAS_ENTITY]->(e1), (c1)-[:HAS_ENTITY]->(e2)
CREATE (c2)-[:HAS_ENTITY]->(e2), (c2)-[:HAS_ENTITY]->(e3)
CREATE (c3)-[:HAS_ENTITY]->(e3), (c3)-[:HAS_ENTITY]->(e4)
CREATE (e2)-[:JUDGES]->(e1)
CREATE (e2)-[:USES]->(e3)
CREATE (e3)-[:STORED_BY]->(e4)
CREATE (e1)-[:APPLIES_TO {method: 'rule'}]->(e3)
CREATE (s1:Section {id: $p + 's1', fileName: $doc, number: '1', path: ['1 適用'], title: '適用', level: 1, first_position: 1})
CREATE (s2:Section {id: $p + 's2', fileName: $doc, number: '2-1', path: ['2 要領', '2-1 性能試験'], title: '性能試験', level: 2, first_position: 2})
CREATE (c1)-[:IN_SECTION {primary: true}]->(s1)
CREATE (c2)-[:IN_SECTION {primary: true}]->(s2)
CREATE (c3)-[:IN_SECTION {primary: false}]->(s2)
"""
CLEANUP = "MATCH (n) WHERE n.fileName STARTS WITH $p OR n.id STARTS WITH $p DETACH DELETE n"


@pytest.fixture(scope="module")
def env():
    if not TEST_PASSWORD:
        pytest.skip("GBDEV_TEST_PASSWORD が無い（試験用 Neo4j のパスワードを渡して流す）")
    from langchain_neo4j import Neo4jGraph
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(TEST_URI, auth=(TEST_USER, TEST_PASSWORD))
    try:
        driver.verify_connectivity()
    except Exception as e:
        driver.close()
        pytest.skip(f"試験用 Neo4j に接続できない: {type(e).__name__}")
    with driver.session(database=TEST_DATABASE) as s:
        if s.run("MATCH (n) WHERE n.fileName STARTS WITH $p OR n.id STARTS WITH $p RETURN count(n) AS n", p=PREFIX).single()["n"]:
            s.run(CLEANUP, p=PREFIX).consume()   # 前回の試験の残り
        before = s.run("CALL () { MATCH (c:Chunk) RETURN count(c) AS chunks } CALL () { MATCH (e:__Entity__) RETURN count(e) AS entities } "
                       "RETURN chunks, entities").single().data()
        index_existed = bool(s.run("SHOW INDEXES YIELD name WHERE name = 'chunk_stable_key' RETURN count(*) AS n").single()["n"])
        s.run(SETUP, p=PREFIX, doc=DOC, c1=C1, c2=C2, c3=C3).consume()
    graph = Neo4jGraph(url=TEST_URI, username=TEST_USER, password=TEST_PASSWORD, database=TEST_DATABASE, refresh_schema=False, sanitize=True)
    try:
        yield {"driver": driver, "graph": graph, "index_existed": index_existed}
    finally:
        with driver.session(database=TEST_DATABASE) as s:
            s.run(CLEANUP, p=PREFIX).consume()
            if not index_existed:
                s.run("DROP INDEX chunk_stable_key IF EXISTS").consume()
            after = s.run("CALL () { MATCH (c:Chunk) RETURN count(c) AS chunks } CALL () { MATCH (e:__Entity__) RETURN count(e) AS entities } "
                          "RETURN chunks, entities").single().data()
        driver.close()
        assert after == before, (before, after)


def read_state(driver):
    with driver.session(database=TEST_DATABASE) as s:
        mentions = {(r["c"], r["e"]): r["props"] for r in s.run(
            "MATCH (c:Chunk)-[r:HAS_ENTITY]->(e) WHERE c.fileName = $doc RETURN c.id AS c, e.id AS e, properties(r) AS props", doc=DOC)}
        rels = {r["t"]: r["props"] for r in s.run(
            "MATCH (a:__Entity__)-[r]->(b:__Entity__) WHERE a.id STARTS WITH $p RETURN type(r) AS t, properties(r) AS props", p=PREFIX)}
        chunks = {r["id"]: (r["k"], r["o"]) for r in s.run(
            "MATCH (c:Chunk) WHERE c.fileName = $doc RETURN c.id AS id, c.stable_key AS k, c.doc_ordinal AS o", doc=DOC)}
        index = bool(s.run("SHOW INDEXES YIELD name WHERE name = 'chunk_stable_key' RETURN count(*) AS n").single()["n"])
    return mentions, rels, chunks, index


def test_dry_run_writes_nothing_but_reports(env):
    from src.provenance import attach_provenance
    result = attach_provenance(env["graph"], dry_run=True, file_names=[DOC], relation_mode="true")
    summary = result["summary"]
    assert summary["dry_run"] is True and summary["relation_properties_written"] is False
    assert summary["has_entity"] == 6 and summary["mention_found"] == 6
    assert summary["relations"] == 3   # 規則のリレーション（method='rule'）は数えない
    assert summary["evidence_kind"] == {"same_sentence": 1, "window": 1, "chunk_only": 1, "none": 0}
    assert summary["chunks"] == 3 and summary["chunks_with_section_key"] == 2 and summary["stable_key_duplicates"] == 0
    mentions, rels, chunks, index = read_state(env["driver"])
    assert all("mention" not in p and "mention_found" not in p for p in mentions.values())
    assert all("evidence" not in p for p in rels.values())
    assert all(k is None and o is None for k, o in chunks.values())
    assert index is env["index_existed"]


def test_attach_writes_mentions_evidence_and_stable_keys(env):
    from src.provenance import attach_provenance
    result = attach_provenance(env["graph"], file_names=[DOC], relation_mode="true", details=True)
    assert result["summary"]["relation_properties_written"] is True
    first = read_state(env["driver"])
    mentions, rels, chunks, index = first
    p = PREFIX

    # (2) 言及: 全角・空白の違いを無視して見つける。aliases（品管課）でも見つける
    m = mentions[(p + "c1", p + "甲形過給機")]
    assert m["mention_found"] is True and m["mention_source"] == "body"
    assert m["mention"] == "この基準はＮＩＧＨＴ１１０９－甲形 過給機の単独試運転に適用し、判定はnight1109-検査部門が行う。"
    assert mentions[(p + "c3", p + "圧力計")]["mention"] == "|吐出圧力|night1109-圧 力 計|"
    assert mentions[(p + "c3", p + "品質管理課")]["mention"] == "保管の責任はnight1109-品管課が負う。"
    assert mentions[(p + "c2", p + "検査部門")]["mention"] == "性能試験はnight1109-検査部門が実施する。"
    assert all(len(x["mention"]) <= 200 for x in mentions.values())

    # (3) リレーションの根拠
    assert rels["JUDGES"]["evidence_kind"] == "same_sentence" and rels["JUDGES"]["source_chunk_id"] == p + "c1"
    assert rels["JUDGES"]["evidence"] == m["mention"]
    assert rels["USES"]["evidence_kind"] == "window" and rels["USES"]["source_chunk_id"] == p + "c2"
    assert rels["USES"]["evidence"] == "性能試験はnight1109-検査部門が実施する。\n計測にはnight1109-圧力計を用いる。"
    assert rels["STORED_BY"]["evidence_kind"] == "chunk_only" and rels["STORED_BY"]["source_chunk_id"] == p + "c3"
    assert all(rels[t]["provenance_method"] == "rule" for t in ("JUDGES", "USES", "STORED_BY"))
    assert "evidence" not in rels["APPLIES_TO"] and "source_chunk_id" not in rels["APPLIES_TO"]   # 規則のリレーション

    # (5) 安定キー: c3 の IN_SECTION は primary でないので節なし
    assert chunks == {p + "c1": (DOC + "#1#1", 1), p + "c2": (DOC + "#2-1#1", 2), p + "c3": (DOC + "#-#3", 3)}
    assert index is True

    # (6) 冪等: もう一度流しても同じ
    attach_provenance(env["graph"], file_names=[DOC], relation_mode="true")
    assert read_state(env["driver"]) == first


def test_relation_mode_false_leaves_relations_alone(env):
    from src.provenance import attach_provenance
    with env["driver"].session(database=TEST_DATABASE) as s:
        s.run("MATCH (a:__Entity__)-[r]->(:__Entity__) WHERE a.id STARTS WITH $p "
              "REMOVE r.source_chunk_id, r.evidence, r.evidence_kind, r.provenance_method", p=PREFIX).consume()
    summary = attach_provenance(env["graph"], file_names=[DOC], relation_mode="false")["summary"]
    assert summary["relation_properties_written"] is False and summary["evidence_kind"]["same_sentence"] == 1
    _, rels, chunks, _ = read_state(env["driver"])
    assert all("evidence" not in p for p in rels.values())
    assert chunks[PREFIX + "c2"] == (DOC + "#2-1#1", 2)   # 言及と安定キーは付ける
