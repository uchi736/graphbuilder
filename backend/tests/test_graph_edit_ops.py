"""工程 S2 の試験 V1: 編集の API（設計メモ 3 章の M1〜M10）。

試験用 Neo4j（neo4j-gbdev）に、試験ごとに合成の文書・チャンク・エンティティを作って操作する。
エンティティの名前は「Zzge」で始め（全角の「ＺＺＧＥ」も正規化キーは zzge で始まる）、写しの中身（文書 7・チャンク 184・
エンティティ 671）には触れない。試験の後で、合成のノードと、試験の間に作られた管理用ノード（修正台帳・操作の履歴）を消す。
始める前と終わった後に写しの件数を確かめる。

操作ごとに、成功、名前の衝突（正規化で同じになる場合を含む）、予約の名前の拒否、古い要素 ID（expected_id の不一致）の拒否、
台帳への書き込み、文書の件数の更新、取り消し（成功と、後で変わったときの拒否）を確かめる。

実行: 本番の backend を読み取り専用で載せた一時コンテナから、試験用 Neo4j（neo4j-gbdev）に向けて流す
（docs/graph-edit/DESIGN.md の 7 章）。本番の backend コンテナでは流さない
"""
import json
import math
import os
import threading
import time

import pytest
from neo4j import GraphDatabase

from src.entities.user_credential import Neo4jCredentials
from src.graph_edit import embedding as embedding_module
from src.graph_edit import ops, read
from src.graph_edit.ledger import alias_uid, cur_uid, dist_uid, mrel_uid, tnode_uid, trel_uid, type_uid
from src.graph_edit.normalize import norm_key
from src.graph_edit.validate import (
    RESERVED_LABELS, RESERVED_RELATIONSHIP_TYPES, EditError,
)
from src.graphDB_dataAccess import RESERVED_RELATIONSHIP_TYPES as DATA_ACCESS_RESERVED_TYPES, graphDBdataAccess
from src.shared.common_fn import create_graph_database_connection
from src.shared.constants import NODEREL_COUNT_QUERY_WITHOUT_COMMUNITY

TEST_URI = os.environ.get("GBDEV_TEST_URI", "neo4j://database-gbdev:7687")
TEST_USER = "neo4j"
TEST_PASSWORD = os.environ.get("GBDEV_TEST_PASSWORD", "gbdev-test-only")
TEST_DATABASE = "neo4j"
# 写しの件数。既定は 10/31 の写し。別の写しで流すときは GBDEV_PRISTINE='{"documents": 6, "chunks": 83, "entities": 492}' のように渡す
PRISTINE = json.loads(os.environ.get("GBDEV_PRISTINE", '{"documents": 7, "chunks": 184, "entities": 671}'))
USER = "pytest-s2"

if "gbdev" not in TEST_URI:
    raise RuntimeError(f"この試験は試験用 Neo4j（database-gbdev）でしか流さない: {TEST_URI}")

# 設計メモ 2.2 の予約の名前
# 本番の規則で作る層（章・節）の Section・HAS_SECTION・IN_SECTION と、取り込みが型の名前を付け替える予約の __KGBuilder__ を含む
DESIGN_RESERVED_LABELS = ["Chunk", "__Community__", "Session", "Message", "SchemaMeta", "ProcessedChunk", "GraphProvenance",
                          "CondFact", "Cond", "__GraphEdit__", "__GraphEditOp__", "__GraphLock__", "Section", "Evidence", "__KGBuilder__"]  # Evidence は 11/9（graph-evidence の根拠の記録）
DESIGN_RESERVED_TYPES = ["HAS_ENTITY", "PART_OF", "NEXT_CHUNK", "FIRST_CHUNK", "SIMILAR", "IN_COMMUNITY", "PARENT_COMMUNITY",
                         "NEXT", "LAST_MESSAGE", "REFERS_TO", "ALIAS_OF", "WHEN", "MENTIONS", "HAS_SECTION", "IN_SECTION"]


# ---- 試験の場 ----

@pytest.fixture(scope="module")
def driver():
    drv = GraphDatabase.driver(TEST_URI, auth=(TEST_USER, TEST_PASSWORD))
    yield drv
    drv.close()


@pytest.fixture(scope="module")
def graph():
    g = create_graph_database_connection(Neo4jCredentials(uri=TEST_URI, userName=TEST_USER, password=TEST_PASSWORD,
                                                          database=TEST_DATABASE))
    yield g
    g._driver.close()


def make_run(driver):
    def run(query, **params):
        with driver.session(database=TEST_DATABASE) as session:
            return [record.data() for record in session.run(query, params)]
    return run


def real_counts(run):
    return run("""
        CALL () { MATCH (d:Document) WHERE d.fileName IS NOT NULL AND NOT d.fileName STARTS WITH 'zzge' RETURN count(d) AS documents }
        CALL () { MATCH (c:Chunk) WHERE NOT c.id STARTS WITH 'zzge' RETURN count(c) AS chunks }
        CALL () { MATCH (e:__Entity__) WHERE NOT toLower(e.id) STARTS WITH 'zzge' AND NOT e.id STARTS WITH 'ＺＺＧＥ'
                  RETURN count(e) AS entities }
        CALL () { MATCH (e:__Entity__) WHERE (e._v IS NOT NULL OR e._curated IS NOT NULL OR e.aliases IS NOT NULL)
                    AND NOT toLower(e.id) STARTS WITH 'zzge' AND NOT e.id STARTS WITH 'ＺＺＧＥ' RETURN count(e) AS marked }
        CALL () { MATCH ()-[r]->() RETURN count(r) AS relationships }
        RETURN documents, chunks, entities, marked, relationships""")[0]


def management_ids(run):
    return {r["eid"] for r in run("MATCH (n) WHERE n:__GraphEdit__ OR n:__GraphEditOp__ OR n:__GraphLock__ "
                                  "RETURN elementId(n) AS eid")}


def cleanup_synthetic(run, keep_management):
    ids = [r["id"] for r in run("MATCH (e:__Entity__) RETURN e.id AS id") if "zzge" in norm_key(r["id"])]
    run("UNWIND $ids AS id MATCH (e:__Entity__ {id: id}) DETACH DELETE e", ids=ids)
    run("MATCH (n) WHERE (n:Chunk AND n.id STARTS WITH 'zzge') OR (n:Document AND n.fileName STARTS WITH 'zzge') "
        "DETACH DELETE n")
    run("MATCH (n) WHERE (n:__GraphEdit__ OR n:__GraphEditOp__) AND NOT elementId(n) IN $keep DETACH DELETE n",
        keep=list(keep_management))
    run("MATCH (l:__GraphLock__ {name: 'graph'}) SET l.lease_until = 0, l.lease_owner = null")


@pytest.fixture(scope="module", autouse=True)
def pristine(driver):
    """写しの件数を確かめ、試験の後に合成のノードと試験で作った管理用ノードを消して、件数が戻ったことを確かめる。"""
    run = make_run(driver)
    keep = management_ids(run)
    cleanup_synthetic(run, keep)
    before = real_counts(run)
    for key, value in PRISTINE.items():
        if before[key] != value:
            pytest.fail(f"試験用 DB が写しの状態でない（{key}={before[key]}、期待 {value}）。restore_gbdev.sh で戻してから流す")
    if before["marked"]:
        pytest.fail(f"写しのエンティティに _v・_curated・aliases が付いている（{before['marked']} 件）。restore_gbdev.sh で戻す")
    had_lock = bool(run("MATCH (l:__GraphLock__) RETURN l LIMIT 1"))
    yield keep
    cleanup_synthetic(run, keep)
    if not had_lock:
        run("MATCH (l:__GraphLock__) WHERE NOT elementId(l) IN $keep DELETE l", keep=list(keep))
    after = real_counts(run)
    assert after == before, f"試験の後で写しが変わった: {before} -> {after}"


@pytest.fixture
def run(driver, pristine):
    run = make_run(driver)
    yield run
    cleanup_synthetic(run, pristine)


class World:
    """1 つの試験の合成の文書・チャンク・エンティティ。"""

    def __init__(self, run, tag, n_chunks=3):
        self.run = run
        self.doc = f"zzge_{tag}.pdf"
        self.chunks = [f"zzge-{tag}-c{i}" for i in range(1, n_chunks + 1)]
        run("""CREATE (d:Document {fileName: $doc, status: 'Completed', fileSource: 'local file'})
               WITH d UNWIND range(0, size($chunks) - 1) AS i
               CREATE (c:Chunk {id: $chunks[i], text: 'zzge chunk ' + toString(i), position: i + 1, fileName: $doc})
               CREATE (c)-[:PART_OF]->(d)""", doc=self.doc, chunks=self.chunks)

    def entity(self, name, labels=("Concept",), chunks=(0,), **props):
        labels = ["__Entity__"] + list(labels)
        record = self.run("""CREATE (e) SET e = $props WITH e CALL apoc.create.addLabels(e, $labels) YIELD node
                             WITH node UNWIND $chunks AS cid MATCH (c:Chunk {id: cid}) CREATE (c)-[:HAS_ENTITY]->(node)
                             RETURN DISTINCT elementId(node) AS eid""",
                          props={"id": name, **props}, labels=labels, chunks=[self.chunks[i] for i in chunks])
        if not record:
            record = self.run("MATCH (e:__Entity__ {id: $id}) RETURN elementId(e) AS eid", id=name)
        return record[0]["eid"]

    def rel(self, a, rel_type, b, **props):
        return self.run("""MATCH (a:__Entity__ {id: $a}) MATCH (b:__Entity__ {id: $b})
                           CALL apoc.create.relationship(a, $t, $p, b) YIELD rel RETURN elementId(rel) AS rid""",
                        a=a, b=b, t=rel_type, p=props)[0]["rid"]

    def eid(self, name):
        rows = self.run("MATCH (e:__Entity__ {id: $id}) RETURN elementId(e) AS eid", id=name)
        return rows[0]["eid"] if rows else None

    def chunk_eid(self, index):
        return self.run("MATCH (c:Chunk {id: $id}) RETURN elementId(c) AS eid", id=self.chunks[index])[0]["eid"]

    def doc_eid(self):
        return self.run("MATCH (d:Document {fileName: $f}) RETURN elementId(d) AS eid", f=self.doc)[0]["eid"]

    def node(self, name):
        rows = self.run("""MATCH (e:__Entity__ {id: $id})
                           RETURN e.id AS id, elementId(e) AS eid, labels(e) AS labels,
                                  apoc.map.removeKey(properties(e), 'embedding') AS props,
                                  CASE WHEN e.embedding IS NULL THEN null ELSE size(e.embedding) END AS dim,
                                  apoc.meta.cypher.type(e._curated) AS curated_type,
                                  [(c:Chunk)-[:HAS_ENTITY]->(e) | c.id] AS chunks""", id=name)
        return rows[0] if rows else None

    def rels(self):
        """合成のエンティティに触れるリレーションの一覧（始点・種類・終点・プロパティ）。"""
        rows = self.run("""MATCH (s)-[r]->(e)
                           WHERE (s:__Entity__ AND toLower(s.id) STARTS WITH 'zzge') OR (e:__Entity__ AND toLower(e.id) STARTS WITH 'zzge')
                              OR s.id STARTS WITH 'ＺＺＧＥ' OR e.id STARTS WITH 'ＺＺＧＥ'
                           RETURN coalesce(s.id, s.fileName) AS s, type(r) AS t, coalesce(e.id, e.fileName) AS e,
                                  properties(r) AS p""")
        return sorted((r["s"], r["t"], r["e"], json.dumps(r["p"], sort_keys=True)) for r in rows)

    def ledger(self, uid):
        rows = self.run("MATCH (g:__GraphEdit__ {uid: $uid}) RETURN properties(g) AS p", uid=uid)
        return rows[0]["p"] if rows else None

    def op(self, op_id):
        rows = self.run("MATCH (o:__GraphEditOp__ {op_id: $id}) RETURN properties(o) AS p", id=op_id)
        return rows[0]["p"] if rows else None

    def stored_counts(self):
        return self.run("MATCH (d:Document {fileName: $f}) RETURN d.entityNodeCount AS entities, "
                        "d.entityEntityRelCount AS rels, d.chunkRelCount AS chunk_rels", f=self.doc)[0]

    def fresh_counts(self):
        row = self.run(NODEREL_COUNT_QUERY_WITHOUT_COMMUNITY, document_name=self.doc)[0]
        return {"entities": row["entityNodeCount"], "rels": row["entityEntityRelCount"], "chunk_rels": row["chunkRelCount"]}


def expect_error(code, fn, *args, **kwargs):
    with pytest.raises(EditError) as info:
        fn(*args, **kwargs)
    assert info.value.code == code, f"期待 {code}、実際 {info.value.error_text()}"
    return info.value


# ---- 予約の名前 ----

def test_reserved_names_match_design():
    assert RESERVED_LABELS == DESIGN_RESERVED_LABELS
    assert RESERVED_RELATIONSHIP_TYPES == DESIGN_RESERVED_TYPES
    assert DATA_ACCESS_RESERVED_TYPES == DESIGN_RESERVED_TYPES


# ---- M1 改名 ----

def test_m1_rename_success(graph, run):
    w = World(run, "m1a")
    eid = w.entity("Zzge M1 Cc By 4.0", ["License"], chunks=(0, 1), description="licence")
    other = w.entity("Zzge M1 Work", ["Work"])
    w.rel("Zzge M1 Work", "LICENSED_UNDER", "Zzge M1 Cc By 4.0")
    result = ops.rename(graph, eid, "Zzge M1 Cc By 4.0", "Zzge M1 CC BY 4.0", USER)
    node = w.node("Zzge M1 CC BY 4.0")
    assert node["eid"] == eid and result["node"]["element_id"] == eid
    assert result["node"]["properties"]["id"] == "Zzge M1 CC BY 4.0"
    assert "__Entity__" not in result["node"]["labels"] and result["node"]["labels"] == ["License"]
    assert "embedding" not in result["node"]["properties"]
    assert node["props"]["aliases"] == ["Zzge M1 Cc By 4.0"]
    assert node["props"]["_v"] == 1 and node["props"]["_curated"] is True and node["curated_type"] == "BOOLEAN"
    assert node["dim"] == 768, "埋め込みが 768 次元で書かれていない"
    # 表記だけが違う別名は残す（抽出が「Cc By 4.0」を出したときに読み替えるため）
    assert w.ledger(alias_uid("Zzge M1 CC BY 4.0")) == w.ledger(alias_uid("Zzge M1 Cc By 4.0"))
    alias = w.ledger(alias_uid("Zzge M1 Cc By 4.0"))
    assert alias["kind"] == "alias" and alias["alias"] == "Zzge M1 Cc By 4.0" and alias["target"] == "Zzge M1 CC BY 4.0"
    assert alias["op_id"] == result["op_id"] and alias["created_at"]
    op = w.op(result["op_id"])
    assert op["op"] == "rename" and op["user"] == USER and op["undone"] is False and op["seq"] >= 1
    assert "「Zzge M1 Cc By 4.0」を「Zzge M1 CC BY 4.0」に改名" == op["summary"]
    assert json.loads(op["versions_after_json"]) == {"Zzge M1 CC BY 4.0": 1}
    snapshot = json.loads(op["snapshot_json"])
    assert snapshot[0]["id"] == "Zzge M1 Cc By 4.0" and sorted(snapshot[0]["chunk_ids"]) == sorted(w.chunks[:2])
    assert [(r["dir"], r["type"], r["other_id"], r["other_is_chunk"], r["props"]) for r in snapshot[0]["rels"]] == \
        [("in", "LICENSED_UNDER", "Zzge M1 Work", False, {})]
    assert w.stored_counts() == w.fresh_counts(), "文書の件数が数え直されていない"
    assert other


def test_m1_rename_conflicts(graph, run):
    w = World(run, "m1b")
    eid = w.entity("Zzge M1 Pump")
    w.entity("Zzge M1 Valve")
    error = expect_error("CONFLICT", ops.rename, graph, eid, "Zzge M1 Pump", "Zzge M1 Valve", USER)
    assert [c["node"]["properties"]["id"] for c in error.data["conflicts"]] == ["Zzge M1 Valve"]
    conflict = error.data["conflicts"][0]
    assert set(conflict) == {"node", "chunk_count", "documents"} and conflict["documents"] == [w.doc]
    # 全角・空白・大小だけが違う名前
    for variant in ("ＺＺＧＥ Ｍ1 ＶＡＬＶＥ", "zzge m1  valve", "ZzgeM1Valve"):
        error = expect_error("CONFLICT", ops.rename, graph, eid, "Zzge M1 Pump", variant, USER)
        assert error.data["conflicts"][0]["node"]["properties"]["id"] == "Zzge M1 Valve"
    expect_error("INVALID", ops.rename, graph, eid, "Zzge M1 Pump", "Zzge M1 Pump", USER)
    expect_error("INVALID", ops.rename, graph, eid, "Zzge M1 Pump", "   ", USER)
    assert w.node("Zzge M1 Pump")["props"].get("_v") is None, "失敗した操作で版が変わった"


def test_m1_rename_reserved_stale_not_found(graph, run):
    w = World(run, "m1c")
    eid = w.entity("Zzge M1 Gear")
    expect_error("RESERVED", ops.rename, graph, w.chunk_eid(0), w.chunks[0], "Zzge chunk", USER)
    expect_error("RESERVED", ops.rename, graph, w.doc_eid(), w.doc, "Zzge doc", USER)
    expect_error("STALE", ops.rename, graph, eid, "Zzge M1 Gear (old)", "Zzge M1 Gear 2", USER)
    expect_error("NOT_FOUND", ops.rename, graph, "4:00000000-0000-0000-0000-000000000000:999999999", "x", "y", USER)
    # 予約ラベルを持つエンティティ
    session_like = w.entity("Zzge M1 Session", ["Session"])
    expect_error("RESERVED", ops.rename, graph, session_like, "Zzge M1 Session", "Zzge M1 S2", USER)
    assert not run("MATCH (o:__GraphEditOp__) WHERE o.summary CONTAINS 'Zzge M1 Gear' RETURN o")


def test_m1_rename_undo(graph, run):
    w = World(run, "m1d")
    eid = w.entity("Zzge M1 Old", ["Concept"], description="d")
    w.entity("Zzge M1 Peer")
    w.rel("Zzge M1 Old", "USES", "Zzge M1 Peer", note="n")
    before_rels = w.rels()
    result = ops.rename(graph, eid, "Zzge M1 Old", "Zzge M1 New", USER)
    history = {row["op_id"]: row for row in ops.history_rows(graph)}
    assert history[result["op_id"]]["undoable"] is True and history[result["op_id"]]["reason"] == ""
    undo = ops.undo(graph, result["op_id"], USER)
    assert undo["restored_ids"] == ["Zzge M1 Old"]
    node = w.node("Zzge M1 Old")
    assert node["eid"] == eid, "取り消しで要素 ID が変わった（その場で戻すはず）"
    assert "aliases" not in node["props"] and node["props"]["_v"] == 2 and "_curated" not in node["props"]
    assert node["dim"] == 768
    assert w.node("Zzge M1 New") is None
    assert w.ledger(alias_uid("Zzge M1 Old")) is None
    assert w.rels() == before_rels
    assert w.op(result["op_id"])["undone"] is True
    undo_op = w.op(undo["op_id"])
    assert undo_op["op"] == "undo" and undo_op["undo_of"] == result["op_id"]
    error = expect_error("NOT_UNDOABLE", ops.undo, graph, result["op_id"], USER)
    assert error.data["reason"] == "取り消し済みです。"
    error = expect_error("NOT_UNDOABLE", ops.undo, graph, undo["op_id"], USER)
    assert "取り消しの操作は取り消せません" in error.data["reason"]
    # 後で同じエンティティが変わったら取り消せない
    second = ops.rename(graph, eid, "Zzge M1 Old", "Zzge M1 Newer", USER)
    ops.set_labels(graph, eid, "Zzge M1 Newer", json.dumps(["Term"]), USER)
    error = expect_error("NOT_UNDOABLE", ops.undo, graph, second["op_id"], USER)
    assert "後の操作" in error.data["reason"]
    rows = {row["op_id"]: row for row in ops.history_rows(graph)}
    assert rows[second["op_id"]]["undoable"] is False and "後の操作" in rows[second["op_id"]]["reason"]
    # 取り込みの保存などで版だけが変わったときも取り消せない
    third = ops.rename(graph, w.eid("Zzge M1 Peer"), "Zzge M1 Peer", "Zzge M1 Peer2", USER)
    run("MATCH (e:__Entity__ {id: 'Zzge M1 Peer2'}) SET e._v = e._v + 1")
    error = expect_error("NOT_UNDOABLE", ops.undo, graph, third["op_id"], USER)
    assert "版が" in error.data["reason"]


def test_m1_rename_rewrites_ledger(graph, run):
    """不変条件 2: 改名で、別名の代表・種類の固定・修正した項目・手動のリレーション・削除の記録・別物の組を書き換える。"""
    w = World(run, "m1e")
    x = w.entity("Zzge M1 X", ["Part"], description="x")
    y = w.entity("Zzge M1 Y", ["Part"])
    w.entity("Zzge M1 Z", ["Part"])
    zrel = w.rel("Zzge M1 X", "FEEDS", "Zzge M1 Z")
    w.entity("Zzge M1 W", ["Part"])
    ops.rename(graph, w.eid("Zzge M1 W"), "Zzge M1 W", "Zzge M1 W2", USER)
    first = w.entity("Zzge M1 X0", ["Part"])
    ops.merge(graph, x, "Zzge M1 X", json.dumps([{"element_id": first, "expected_id": "Zzge M1 X0"}]), "Zzge M1 X",
              json.dumps(["Part"]), "target", None, USER)
    ops.set_labels(graph, x, "Zzge M1 X", json.dumps(["Part", "Component"]), USER)
    ops.update_props(graph, x, "Zzge M1 X", json.dumps({"unit": "mm"}), USER)
    ops.create_relationship(graph, x, "Zzge M1 X", y, "Zzge M1 Y", "CONNECTS", json.dumps({}), USER)
    ops.delete_relationship(graph, zrel, json.dumps({"src": "Zzge M1 X", "type": "FEEDS", "tgt": "Zzge M1 Z"}), "true", USER)
    ops.mark_distinct(graph, x, w.eid("Zzge M1 W2"), USER)
    ops.rename(graph, x, "Zzge M1 X", "Zzge M1 X2", USER)
    assert w.ledger(alias_uid("Zzge M1 X0"))["target"] == "Zzge M1 X2"
    assert w.ledger(alias_uid("Zzge M1 X"))["target"] == "Zzge M1 X2"
    assert w.ledger(type_uid("Zzge M1 X")) is None and w.ledger(type_uid("Zzge M1 X2"))["labels"] == ["Part", "Component"]
    assert w.ledger(cur_uid("Zzge M1 X")) is None and w.ledger(cur_uid("Zzge M1 X2"))["keys"] == ["unit"]
    assert w.ledger(mrel_uid("Zzge M1 X", "CONNECTS", "Zzge M1 Y")) is None
    assert w.ledger(mrel_uid("Zzge M1 X2", "CONNECTS", "Zzge M1 Y"))["src"] == "Zzge M1 X2"
    assert w.ledger(trel_uid("Zzge M1 X", "FEEDS", "Zzge M1 Z")) is None
    assert w.ledger(trel_uid("Zzge M1 X2", "FEEDS", "Zzge M1 Z"))["src"] == "Zzge M1 X2"
    assert w.ledger(dist_uid("Zzge M1 X", "Zzge M1 W2")) is None
    assert w.ledger(dist_uid("Zzge M1 X2", "Zzge M1 W2")) is not None
    assert w.node("Zzge M1 X2")["props"]["aliases"] == ["Zzge M1 X", "Zzge M1 X0"]


def test_m1_rename_to_tombstoned_name_resurrects_it(graph, run):
    w = World(run, "m1f")
    gone = w.entity("Zzge M1 Gone")
    ops.delete_nodes(graph, json.dumps([{"element_id": gone, "expected_id": "Zzge M1 Gone"}]), "true", USER)
    assert w.ledger(tnode_uid("Zzge M1 Gone")) is not None
    eid = w.entity("Zzge M1 Here")
    ops.rename(graph, eid, "Zzge M1 Here", "zzge m1 gone", USER)
    assert w.ledger(tnode_uid("Zzge M1 Gone")) is None, "残す名前の削除の記録が残っている（抽出で落ちる）"


# ---- M2 種類の変更 ----

def test_m2_set_labels(graph, run):
    w = World(run, "m2")
    eid = w.entity("Zzge M2 Item", ["Concept", "Entity"], description="d")
    result = ops.set_labels(graph, eid, "Zzge M2 Item", json.dumps(["Component", "Part"]), USER)
    node = w.node("Zzge M2 Item")
    assert sorted(node["labels"]) == ["Component", "Part", "__Entity__"] and node["eid"] == eid
    assert result["node"]["labels"] == ["Component", "Part"]
    row = w.ledger(type_uid("Zzge M2 Item"))
    assert row["kind"] == "type_override" and row["entity"] == "Zzge M2 Item" and row["labels"] == ["Component", "Part"]
    assert node["props"]["_v"] == 1 and node["props"]["_curated"] is True
    for labels, code in ((["Chunk"], "RESERVED"), (["__GraphEdit__"], "RESERVED"), (["Document"], "RESERVED"),
                         (["bad-name"], "INVALID"), (["1abc"], "INVALID"), ([], "INVALID")):
        expect_error(code, ops.set_labels, graph, eid, "Zzge M2 Item", json.dumps(labels), USER)
    expect_error("INVALID", ops.set_labels, graph, eid, "Zzge M2 Item", "not json", USER)
    expect_error("STALE", ops.set_labels, graph, eid, "Zzge M2 Other", json.dumps(["Part"]), USER)
    # __Entity__ は外せない（指定しても残る）。Document は今持っていれば残せる
    doc_like = w.entity("Zzge M2 Doc", ["Document", "Entity"])
    ops.set_labels(graph, doc_like, "Zzge M2 Doc", json.dumps(["Document", "__Entity__"]), USER)
    assert sorted(w.node("Zzge M2 Doc")["labels"]) == ["Document", "__Entity__"]
    undo = ops.undo(graph, result["op_id"], USER)
    assert undo["restored_ids"] == ["Zzge M2 Item"]
    assert sorted(w.node("Zzge M2 Item")["labels"]) == ["Concept", "Entity", "__Entity__"]
    assert w.ledger(type_uid("Zzge M2 Item")) is None


# ---- M3 プロパティの修正 ----

def test_m3_update_props(graph, run):
    w = World(run, "m3")
    eid = w.entity("Zzge M3 Bolt", ["Part"], description="old", value="5", unit="mm")
    result = ops.update_props(graph, eid, "Zzge M3 Bolt", json.dumps({"description": "new description", "unit": "cm",
                                                                      "value": None, "count": 3, "ok": True}), USER)
    node = w.node("Zzge M3 Bolt")
    assert node["props"]["description"] == "new description" and node["props"]["unit"] == "cm"
    assert "value" not in node["props"] and node["props"]["count"] == 3 and node["props"]["ok"] is True
    assert node["dim"] == 768
    row = w.ledger(cur_uid("Zzge M3 Bolt"))
    assert row["keys"] == ["count", "description", "ok", "unit", "value"]
    assert json.loads(row["values_json"]) == {"count": 3, "description": "new description", "ok": True, "unit": "cm",
                                              "value": None}
    assert result["node"]["properties"]["unit"] == "cm"
    for props in ({"id": "x"}, {"embedding": "x"}, {"aliases": "x"}, {"_v": 3}, {"fileName": "x"}, {"list": [1, 2]},
                  {"map": {"a": 1}}, {}, {" padded": "x"}):
        expect_error("INVALID", ops.update_props, graph, eid, "Zzge M3 Bolt", json.dumps(props), USER)
    expect_error("INVALID", ops.update_props, graph, eid, "Zzge M3 Bolt", '{"x": NaN}', USER)
    expect_error("STALE", ops.update_props, graph, eid, "Zzge M3 Nut", json.dumps({"unit": "m"}), USER)
    expect_error("RESERVED", ops.update_props, graph, w.chunk_eid(0), w.chunks[0], json.dumps({"unit": "m"}), USER)
    second = ops.update_props(graph, eid, "Zzge M3 Bolt", json.dumps({"grade": "8.8"}), USER)
    assert w.ledger(cur_uid("Zzge M3 Bolt"))["keys"] == ["count", "description", "grade", "ok", "unit", "value"]
    error = expect_error("NOT_UNDOABLE", ops.undo, graph, result["op_id"], USER)
    assert "後の操作" in error.data["reason"]
    ops.undo(graph, second["op_id"], USER)
    node = w.node("Zzge M3 Bolt")
    assert "grade" not in node["props"] and node["props"]["unit"] == "cm"
    assert w.ledger(cur_uid("Zzge M3 Bolt"))["keys"] == ["count", "description", "ok", "unit", "value"]
    # 後の操作を取り消しても、エンティティは操作の後に変わったので取り消せない（設計メモ: 取り消すと _v を 1 増やす）
    expect_error("NOT_UNDOABLE", ops.undo, graph, result["op_id"], USER)
    # 取り消しの成功
    other = w.entity("Zzge M3 Nut", ["Part"], description="old", value="5")
    changed = ops.update_props(graph, other, "Zzge M3 Nut", json.dumps({"description": "new", "value": None}), USER)
    ops.undo(graph, changed["op_id"], USER)
    node = w.node("Zzge M3 Nut")
    assert node["props"]["description"] == "old" and node["props"]["value"] == "5" and node["dim"] == 768
    assert w.ledger(cur_uid("Zzge M3 Nut")) is None


# ---- M4 統合 ----

def _merge_world(run, tag):
    w = World(run, tag)
    w.entity(f"Zzge {tag} SACM645", ["Material"], chunks=(0,), description="target")
    w.entity(f"Zzge {tag} SACM645 相当材", ["Material"], chunks=(1,), description="source one", unit="mm")
    w.entity(f"ＺＺＧＥ {tag} ＳＡＣＭ645材", ["Part"], chunks=(1, 2))
    w.entity(f"Zzge {tag} Shaft", ["Part"], chunks=(0,))
    w.entity(f"Zzge {tag} Spec", ["Standard"], chunks=(2,))
    t, s1, s2 = f"Zzge {tag} SACM645", f"Zzge {tag} SACM645 相当材", f"ＺＺＧＥ {tag} ＳＡＣＭ645材"
    w.rel(t, "USED_IN", f"Zzge {tag} Shaft", p=1)
    w.rel(s1, "USED_IN", f"Zzge {tag} Shaft", p=2)
    w.rel(s2, "DEFINED_BY", f"Zzge {tag} Spec", section="3.1")
    w.rel(f"Zzge {tag} Spec", "SPECIFIES", s1)
    w.rel(s1, "SAME_AS", t)
    w.rel(t, "SELF_NOTE", t)
    return w, t, s1, s2


def test_m4_merge_and_undo_restores_everything(graph, run):
    w, t, s1, s2 = _merge_world(run, "m4a")
    entity_count = len(run("MATCH (e:__Entity__) RETURN e"))
    before_rels = w.rels()
    before_nodes = {name: w.node(name) for name in (t, s1, s2)}
    ops.rename(graph, w.eid(f"Zzge m4a Shaft"), "Zzge m4a Shaft", "Zzge m4a Main Shaft", USER)  # 先に別の操作（相手の改名）
    before_rels = w.rels()
    result = ops.merge(graph, w.eid(t), t, json.dumps([{"element_id": w.eid(s1), "expected_id": s1},
                                                       {"element_id": w.eid(s2), "expected_id": s2}]),
                       t, json.dumps(["Material"]), "concat", None, USER)
    assert result["merged_ids"] == [s1, s2]
    assert w.node(s1) is None and w.node(s2) is None
    node = w.node(t)
    assert sorted(node["labels"]) == ["Material", "__Entity__"]
    assert node["props"]["description"] == "target\nsource one"
    assert node["props"]["unit"] == "mm", "統合で source にしかないプロパティが残っていない"
    assert sorted(node["props"]["aliases"]) == sorted([s1, s2])
    assert sorted(node["chunks"]) == sorted(w.chunks), "HAS_ENTITY が統合先に集まっていない"
    assert node["dim"] == 768 and node["props"]["_curated"] is True
    rels = w.rels()
    assert not [r for r in rels if r[0] == t and r[2] == t], "自分自身へのリレーションが残った"
    assert [r for r in rels if r[1] == "USED_IN"] == [(t, "USED_IN", "Zzge m4a Main Shaft", json.dumps({"p": 2}))]
    assert (t, "DEFINED_BY", "Zzge m4a Spec", json.dumps({"section": "3.1"})) in rels
    assert ("Zzge m4a Spec", "SPECIFIES", t, "{}") in rels
    for alias in (s1, s2):
        row = w.ledger(alias_uid(alias))
        assert row["alias"] == alias and row["target"] == t
    assert w.ledger(cur_uid(t))["keys"] == ["description"]
    assert w.stored_counts() == w.fresh_counts() and w.stored_counts()["entities"] == 3
    assert len(run("MATCH (e:__Entity__) RETURN e")) == entity_count - 2
    # 取り消し: 消えたノードを写しから作り直し、リレーションと HAS_ENTITY を名前とチャンクの id で戻し、統合で付いた別名を消す
    undo = ops.undo(graph, result["op_id"], USER)
    assert sorted(undo["restored_ids"]) == sorted([t, s1, s2]) and undo["skipped_relationships"] == 0
    assert len(run("MATCH (e:__Entity__) RETURN e")) == entity_count
    assert w.rels() == before_rels, "統合の前とリレーション・HAS_ENTITY が一致しない"
    for name in (t, s1, s2):
        node = w.node(name)
        expected = before_nodes[name]
        assert sorted(node["labels"]) == sorted(expected["labels"])
        assert sorted(node["chunks"]) == sorted(expected["chunks"])
        props = {k: v for k, v in node["props"].items() if k != "_v"}
        assert props == {k: v for k, v in expected["props"].items() if k != "_v"}
        assert node["props"]["_v"] >= 1
    for alias in (s1, s2):
        assert w.ledger(alias_uid(alias)) is None
    assert w.ledger(cur_uid(t)) is None
    assert w.stored_counts() == w.fresh_counts() and w.stored_counts()["entities"] == 5
    has_entity = run("MATCH (c:Chunk)-[r:HAS_ENTITY]->(e) WHERE c.id STARTS WITH 'zzge-m4a' RETURN count(r) AS n")[0]["n"]
    assert has_entity == 6


def test_m4_merge_keep_source_name_and_undo(graph, run):
    w, t, s1, s2 = _merge_world(run, "m4b")
    before_rels = w.rels()
    result = ops.merge(graph, w.eid(t), t, json.dumps([{"element_id": w.eid(s1), "expected_id": s1}]), s1,
                       json.dumps(["Material"]), "custom", "  書き直した説明  ", USER)
    node = w.node(s1)
    assert node is not None and w.node(t) is None
    assert node["props"]["description"] == "書き直した説明" and node["props"]["aliases"] == [t]
    assert w.ledger(alias_uid(t))["target"] == s1 and w.ledger(alias_uid(s1)) is None
    ops.undo(graph, result["op_id"], USER)
    assert w.node(t) is not None and w.node(s1) is not None
    assert w.rels() == before_rels


def test_m4_merge_rejections(graph, run):
    w, t, s1, s2 = _merge_world(run, "m4c")
    w.entity("Zzge m4c Outside")
    sources = json.dumps([{"element_id": w.eid(s1), "expected_id": s1}])
    error = expect_error("CONFLICT", ops.merge, graph, w.eid(t), t, sources, "ＺＺＧＥ ｍ4ｃ ＯＵＴＳＩＤＥ",
                         json.dumps(["Material"]), "target", None, USER)
    assert [c["node"]["properties"]["id"] for c in error.data["conflicts"]] == ["Zzge m4c Outside"]
    expect_error("RESERVED", ops.merge, graph, w.eid(t), t,
                 json.dumps([{"element_id": w.chunk_eid(0), "expected_id": w.chunks[0]}]), t, json.dumps(["Material"]),
                 "target", None, USER)
    expect_error("RESERVED", ops.merge, graph, w.eid(t), t, sources, t, json.dumps(["__Community__"]), "target", None, USER)
    expect_error("STALE", ops.merge, graph, w.eid(t), t, json.dumps([{"element_id": w.eid(s1), "expected_id": "old"}]), t,
                 json.dumps(["Material"]), "target", None, USER)
    expect_error("INVALID", ops.merge, graph, w.eid(t), t, json.dumps([{"element_id": w.eid(t), "expected_id": t}]), t,
                 json.dumps(["Material"]), "target", None, USER)
    expect_error("INVALID", ops.merge, graph, w.eid(t), t, sources, t, json.dumps(["Material"]), "custom", " ", USER)
    expect_error("INVALID", ops.merge, graph, w.eid(t), t, sources, t, json.dumps(["Material"]), "other", None, USER)
    expect_error("INVALID", ops.merge, graph, w.eid(t), t, "[]", t, json.dumps(["Material"]), "target", None, USER)
    expect_error("NOT_FOUND", ops.merge, graph, w.eid(t), t,
                 json.dumps([{"element_id": "4:00000000-0000-0000-0000-000000000000:999999999", "expected_id": "x"}]), t,
                 json.dumps(["Material"]), "target", None, USER)
    assert w.node(s1) is not None and not run("MATCH (g:__GraphEdit__ {kind: 'alias'}) WHERE g.target = $t RETURN g", t=t)


# ---- M5 エンティティの削除 ----

def test_m5_delete_nodes_suppress_and_undo(graph, run):
    w = World(run, "m5a")
    eid = w.entity("Zzge M5 Noise", ["Concept"], chunks=(0, 1), description="noise")
    w.entity("Zzge M5 Keep", ["Concept"])
    w.rel("Zzge M5 Noise", "NEAR", "Zzge M5 Keep", d=1)
    ops.rename(graph, eid, "Zzge M5 Noise", "Zzge M5 Noise2", USER)  # 別名を作る
    ops.set_labels(graph, eid, "Zzge M5 Noise2", json.dumps(["Term"]), USER)
    ops.update_props(graph, eid, "Zzge M5 Noise2", json.dumps({"unit": "x"}), USER)
    ops.create_relationship(graph, eid, "Zzge M5 Noise2", w.eid("Zzge M5 Keep"), "Zzge M5 Keep", "LINKS", "{}", USER)
    before_rels = w.rels()
    counts_before = w.fresh_counts()
    result = ops.delete_nodes(graph, json.dumps([{"element_id": eid, "expected_id": "Zzge M5 Noise2"}]), "true", USER)
    assert result["deleted_ids"] == ["Zzge M5 Noise2"] and w.node("Zzge M5 Noise2") is None
    assert w.ledger(tnode_uid("Zzge M5 Noise2"))["name"] == "Zzge M5 Noise2"
    assert w.ledger(tnode_uid("Zzge M5 Noise"))["name"] == "Zzge M5 Noise", "別名が削除の記録に置き換わっていない"
    assert w.ledger(alias_uid("Zzge M5 Noise")) is None
    assert w.ledger(type_uid("Zzge M5 Noise2")) is None and w.ledger(cur_uid("Zzge M5 Noise2")) is None
    assert w.ledger(mrel_uid("Zzge M5 Noise2", "LINKS", "Zzge M5 Keep")) is None
    assert w.stored_counts() == w.fresh_counts() and w.stored_counts()["entities"] == counts_before["entities"] - 1
    undo = ops.undo(graph, result["op_id"], USER)
    assert undo["restored_ids"] == ["Zzge M5 Noise2"]
    node = w.node("Zzge M5 Noise2")
    assert node is not None and sorted(node["chunks"]) == sorted(w.chunks[:2]) and node["dim"] == 768
    assert sorted(node["labels"]) == ["Term", "__Entity__"] and node["props"]["unit"] == "x"
    assert w.rels() == before_rels
    assert w.ledger(tnode_uid("Zzge M5 Noise2")) is None and w.ledger(tnode_uid("Zzge M5 Noise")) is None
    assert w.ledger(alias_uid("Zzge M5 Noise"))["target"] == "Zzge M5 Noise2"
    assert w.ledger(mrel_uid("Zzge M5 Noise2", "LINKS", "Zzge M5 Keep")) is not None
    assert w.stored_counts() == w.fresh_counts() and w.stored_counts()["entities"] == counts_before["entities"]


def test_m5_delete_without_suppress_and_rejections(graph, run):
    w = World(run, "m5b")
    a = w.entity("Zzge M5 A")
    b = w.entity("Zzge M5 B")
    ops.rename(graph, a, "Zzge M5 A", "Zzge M5 A2", USER)
    result = ops.delete_nodes(graph, json.dumps([{"element_id": a, "expected_id": "Zzge M5 A2"}]), "false", USER)
    assert result["deleted_ids"] == ["Zzge M5 A2"]
    assert w.ledger(tnode_uid("Zzge M5 A2")) is None and w.ledger(tnode_uid("Zzge M5 A")) is None
    assert w.ledger(alias_uid("Zzge M5 A")) is None, "代表が無くなった別名が残っている"
    expect_error("STALE", ops.delete_nodes, graph, json.dumps([{"element_id": b, "expected_id": "Zzge M5 X"}]), "true", USER)
    expect_error("RESERVED", ops.delete_nodes, graph, json.dumps([{"element_id": w.doc_eid(), "expected_id": w.doc}]),
                 "true", USER)
    expect_error("NOT_FOUND", ops.delete_nodes, graph, json.dumps([{"element_id": a, "expected_id": "Zzge M5 A2"}]),
                 "true", USER)
    expect_error("INVALID", ops.delete_nodes, graph, json.dumps([{"element_id": b, "expected_id": "Zzge M5 B"}] * 2),
                 "true", USER)
    expect_error("INVALID", ops.delete_nodes, graph, json.dumps([{"element_id": b}]), "true", USER)
    assert w.node("Zzge M5 B") is not None


# ---- M6〜M8 リレーション ----

def test_m6_create_relationship(graph, run):
    w = World(run, "m6")
    a = w.entity("Zzge M6 A", ["Part"])
    b = w.entity("Zzge M6 B", ["Part"])
    old = w.rel("Zzge M6 A", "CONNECTS", "Zzge M6 B")
    ops.delete_relationship(graph, old, json.dumps({"src": "Zzge M6 A", "type": "CONNECTS", "tgt": "Zzge M6 B"}), "true", USER)
    assert w.ledger(trel_uid("Zzge M6 A", "CONNECTS", "Zzge M6 B")) is not None
    result = ops.create_relationship(graph, a, "Zzge M6 A", b, "Zzge M6 B", "CONNECTS", json.dumps({"note": "手で追加"}), USER)
    rel = result["relationship"]
    assert rel["type"] == "CONNECTS" and rel["start_node_element_id"] == a and rel["end_node_element_id"] == b
    assert rel["properties"] == {"note": "手で追加"}
    assert w.ledger(trel_uid("Zzge M6 A", "CONNECTS", "Zzge M6 B")) is None, "同じ組の削除の記録が残っている"
    row = w.ledger(mrel_uid("Zzge M6 A", "CONNECTS", "Zzge M6 B"))
    assert row["kind"] == "manual_rel" and json.loads(row["props_json"]) == {"note": "手で追加"}
    for name in ("Zzge M6 A", "Zzge M6 B"):
        assert w.node(name)["props"]["_curated"] is True
    assert w.stored_counts() == w.fresh_counts() and w.stored_counts()["rels"] == 1
    for rel_type, code in (("HAS_ENTITY", "RESERVED"), ("MENTIONS", "RESERVED"), ("connects", "INVALID"),
                           ("1X", "INVALID"), ("A-B", "INVALID")):
        expect_error(code, ops.create_relationship, graph, a, "Zzge M6 A", b, "Zzge M6 B", rel_type, "{}", USER)
    expect_error("INVALID", ops.create_relationship, graph, a, "Zzge M6 A", a, "Zzge M6 A", "SELF", "{}", USER)
    expect_error("STALE", ops.create_relationship, graph, a, "Zzge M6 A", b, "Zzge M6 Q", "CONNECTS", "{}", USER)
    expect_error("RESERVED", ops.create_relationship, graph, a, "Zzge M6 A", w.chunk_eid(0), w.chunks[0], "CONNECTS", "{}", USER)
    expect_error("INVALID", ops.create_relationship, graph, a, "Zzge M6 A", b, "Zzge M6 B", "CONNECTS",
                 json.dumps({"_x": 1}), USER)
    ops.undo(graph, result["op_id"], USER)
    assert not run("MATCH (:__Entity__ {id: 'Zzge M6 A'})-[r:CONNECTS]->() RETURN r")
    assert w.ledger(mrel_uid("Zzge M6 A", "CONNECTS", "Zzge M6 B")) is None
    assert w.ledger(trel_uid("Zzge M6 A", "CONNECTS", "Zzge M6 B")) is not None, "取り消しで削除の記録が戻っていない"


def test_m7_delete_relationship(graph, run):
    w = World(run, "m7")
    w.entity("Zzge M7 A", ["Part"])
    b = w.entity("Zzge M7 B", ["Part"])
    rid = w.rel("Zzge M7 A", "GOVERNED_BY", "Zzge M7 B", context="c1")
    before = w.rels()
    expected = json.dumps({"src": "Zzge M7 A", "type": "GOVERNED_BY", "tgt": "Zzge M7 B"})
    expect_error("STALE", ops.delete_relationship, graph, rid,
                 json.dumps({"src": "Zzge M7 A", "type": "GOVERNED_BY", "tgt": "Zzge M7 C"}), "true", USER)
    has_entity = run("MATCH (c:Chunk {id: $c})-[r:HAS_ENTITY]->() RETURN elementId(r) AS rid", c=w.chunks[0])[0]["rid"]
    expect_error("RESERVED", ops.delete_relationship, graph, has_entity,
                 json.dumps({"src": w.chunks[0], "type": "HAS_ENTITY", "tgt": "Zzge M7 A"}), "true", USER)
    result = ops.delete_relationship(graph, rid, expected, "true", USER)
    assert not run("MATCH ()-[r]->() WHERE elementId(r) = $rid RETURN r", rid=rid)
    row = w.ledger(trel_uid("Zzge M7 A", "GOVERNED_BY", "Zzge M7 B"))
    assert row["kind"] == "tombstone_rel" and (row["src"], row["type"], row["tgt"]) == ("Zzge M7 A", "GOVERNED_BY", "Zzge M7 B")
    expect_error("NOT_FOUND", ops.delete_relationship, graph, rid, expected, "true", USER)
    ops.undo(graph, result["op_id"], USER)
    assert w.rels() == before, "取り消しでリレーション（プロパティ付き）が戻っていない"
    assert w.ledger(trel_uid("Zzge M7 A", "GOVERNED_BY", "Zzge M7 B")) is None
    rid2 = run("MATCH (:__Entity__ {id: 'Zzge M7 A'})-[r:GOVERNED_BY]->() RETURN elementId(r) AS rid")[0]["rid"]
    ops.delete_relationship(graph, rid2, expected, "false", USER)
    assert w.ledger(trel_uid("Zzge M7 A", "GOVERNED_BY", "Zzge M7 B")) is None
    assert b


def test_m8_update_relationship(graph, run):
    w = World(run, "m8")
    a = w.entity("Zzge M8 A", ["Part"])
    b = w.entity("Zzge M8 B", ["Part"])
    rid = w.rel("Zzge M8 A", "PART_OF_X", "Zzge M8 B", context="c", section="2")
    expected = {"src": "Zzge M8 A", "type": "PART_OF_X", "tgt": "Zzge M8 B"}
    result = ops.update_relationship(graph, rid, json.dumps(expected), "CONTAINED_IN", "false", None, USER)
    rel = result["relationship"]
    assert rel["element_id"] != rid and rel["type"] == "CONTAINED_IN"
    assert rel["start_node_element_id"] == a and rel["end_node_element_id"] == b
    assert rel["properties"] == {"context": "c", "section": "2"}, "付け替えでプロパティが写されていない"
    assert not run("MATCH ()-[r]->() WHERE elementId(r) = $rid RETURN r", rid=rid)
    assert w.ledger(trel_uid("Zzge M8 A", "PART_OF_X", "Zzge M8 B")) is not None
    assert json.loads(w.ledger(mrel_uid("Zzge M8 A", "CONTAINED_IN", "Zzge M8 B"))["props_json"]) == {"context": "c", "section": "2"}
    # 向きの反転（要素 ID が変わる。旧い組は削除の記録、新しい組は手動のリレーション）
    flipped = ops.update_relationship(graph, rel["element_id"], json.dumps({"src": "Zzge M8 A", "type": "CONTAINED_IN",
                                                                           "tgt": "Zzge M8 B"}), None, "true", None, USER)
    assert flipped["relationship"]["start_node_element_id"] == b and flipped["relationship"]["end_node_element_id"] == a
    assert flipped["relationship"]["properties"] == {"context": "c", "section": "2"}
    assert w.ledger(mrel_uid("Zzge M8 A", "CONTAINED_IN", "Zzge M8 B")) is None
    assert w.ledger(trel_uid("Zzge M8 A", "CONTAINED_IN", "Zzge M8 B")) is not None
    assert w.ledger(mrel_uid("Zzge M8 B", "CONTAINED_IN", "Zzge M8 A")) is not None
    # プロパティだけ（その場で SET。要素 ID は変わらない。手動のリレーションなら記録のプロパティも合わせる）
    flipped_expected = json.dumps({"src": "Zzge M8 B", "type": "CONTAINED_IN", "tgt": "Zzge M8 A"})
    same = ops.update_relationship(graph, flipped["relationship"]["element_id"], flipped_expected, None, "false",
                                   json.dumps({"section": None, "note": "n"}), USER)
    assert same["relationship"]["element_id"] == flipped["relationship"]["element_id"]
    assert same["relationship"]["properties"] == {"context": "c", "note": "n"}
    assert json.loads(w.ledger(mrel_uid("Zzge M8 B", "CONTAINED_IN", "Zzge M8 A"))["props_json"]) == {"context": "c", "note": "n"}
    expect_error("INVALID", ops.update_relationship, graph, same["relationship"]["element_id"], flipped_expected,
                 "CONTAINED_IN", "false", None, USER)
    expect_error("RESERVED", ops.update_relationship, graph, same["relationship"]["element_id"], flipped_expected,
                 "PART_OF", "false", None, USER)
    expect_error("INVALID", ops.update_relationship, graph, same["relationship"]["element_id"], flipped_expected,
                 "lower_case", "false", None, USER)
    expect_error("STALE", ops.update_relationship, graph, same["relationship"]["element_id"], json.dumps(expected),
                 "LINKS", "false", None, USER)
    expect_error("NOT_FOUND", ops.update_relationship, graph, rid, json.dumps(expected), "LINKS", "false", None, USER)
    # 取り消し（最後の操作）。前の操作は、後の操作が同じエンティティに触れたので取り消せない
    ops.undo(graph, same["op_id"], USER)
    assert run("MATCH ()-[r]->() WHERE elementId(r) = $rid RETURN properties(r) AS p",
               rid=flipped["relationship"]["element_id"])[0]["p"] == {"context": "c", "section": "2"}
    expect_error("NOT_UNDOABLE", ops.undo, graph, flipped["op_id"], USER)


@pytest.mark.parametrize("change", ["type", "reverse", "props"])
def test_m8_undo_restores_relationship_and_ledger(graph, run, change):
    w = World(run, f"m8{change}")
    w.entity("Zzge M8 C", ["Part"])
    w.entity("Zzge M8 D", ["Part"])
    rid = w.rel("Zzge M8 C", "LINKS", "Zzge M8 D", context="k")
    before = w.rels()
    expected = json.dumps({"src": "Zzge M8 C", "type": "LINKS", "tgt": "Zzge M8 D"})
    if change == "type":
        result = ops.update_relationship(graph, rid, expected, "CONNECTS", "false", None, USER)
    elif change == "reverse":
        result = ops.update_relationship(graph, rid, expected, None, "true", None, USER)
    else:
        result = ops.update_relationship(graph, rid, expected, None, "false", json.dumps({"context": "k2"}), USER)
    assert w.rels() != before
    ops.undo(graph, result["op_id"], USER)
    assert w.rels() == before
    assert not run("MATCH (g:__GraphEdit__) WHERE g.uid STARTS WITH 'mrel:Zzge M8 C' OR g.uid STARTS WITH 'mrel:Zzge M8 D' "
                   "OR g.uid STARTS WITH 'trel:zzgem8' RETURN g")


# ---- M9 操作の履歴 ----

def test_m9_history_paging(graph, run):
    w = World(run, "m9")
    eid = w.entity("Zzge M9 A")
    first = ops.rename(graph, eid, "Zzge M9 A", "Zzge M9 B", USER)
    second = ops.rename(graph, eid, "Zzge M9 B", "Zzge M9 C", USER)
    third = ops.update_props(graph, eid, "Zzge M9 C", json.dumps({"unit": "u"}), USER)
    rows = ops.history_rows(graph, "2")
    assert [row["op_id"] for row in rows] == [third["op_id"], second["op_id"]]
    assert set(rows[0]) >= {"op_id", "seq", "op", "user", "at", "summary", "undone", "undoable", "reason"}
    assert rows[0]["undoable"] is True and rows[1]["undoable"] is False and rows[1]["reason"]
    older = ops.history_rows(graph, 10, rows[1]["seq"])
    assert older[0]["op_id"] == first["op_id"]
    assert all(row["seq"] < rows[1]["seq"] for row in older)
    expect_error("INVALID", ops.history_rows, graph, "x")


# ---- M10 修正台帳・別物・既存の入口の置き換え ----

def test_m10_ledger_rows_delete_and_undo(graph, run):
    w = World(run, "m10a")
    eid = w.entity("Zzge M10 A")
    ops.rename(graph, eid, "Zzge M10 A", "Zzge M10 B", USER)
    rows = read.ledger(graph, "alias", 1000)
    mine = [row for row in rows if row["uid"] == alias_uid("Zzge M10 A")]
    assert mine and mine[0]["kind"] == "alias" and mine[0]["target"] == "Zzge M10 B"
    assert all(row["kind"] == "alias" for row in rows)
    expect_error("INVALID", read.ledger, graph, "unknown")
    result = ops.ledger_delete(graph, alias_uid("Zzge M10 A"), USER)
    assert result["uid"] == alias_uid("Zzge M10 A") and w.ledger(alias_uid("Zzge M10 A")) is None
    expect_error("NOT_FOUND", ops.ledger_delete, graph, alias_uid("Zzge M10 A"), USER)
    ops.undo(graph, result["op_id"], USER)
    assert w.ledger(alias_uid("Zzge M10 A"))["target"] == "Zzge M10 B"


def _set_vector(run, name, seed):
    vector = [math.sin(seed + i) for i in range(768)]
    run("MATCH (e:__Entity__ {id: $id}) CALL db.create.setNodeVectorProperty(e, 'embedding', $v)", id=name, v=vector)


def test_m10_mark_distinct_excludes_duplicates(graph, run):
    w = World(run, "m10b")
    a = w.entity("!zzge Qxv Tqz", ["Part"])
    b = w.entity("!zzge Qxv Tqz 2", ["Part"])
    _set_vector(run, "!zzge Qxv Tqz", 1.0)
    _set_vector(run, "!zzge Qxv Tqz 2", 1.0)
    access = graphDBdataAccess(graph)

    # 重複の候補は名前の順に 100 行で切られるので、合成の組は名前が先頭に来るようにする（「!」は英数字より前）
    def pairs():
        nodes, _ = access.get_duplicate_nodes_list(read.distinct_element_pairs(graph))
        return {(row["e"]["elementId"], s["elementId"]) for row in nodes for s in row["similar"]}

    assert (a, b) in pairs() or (b, a) in pairs()
    result = ops.mark_distinct(graph, a, b, USER)
    assert result["uid"] == dist_uid("!zzge Qxv Tqz", "!zzge Qxv Tqz 2")
    row = w.ledger(result["uid"])
    assert (row["a"], row["b"]) == ("!zzge Qxv Tqz", "!zzge Qxv Tqz 2")
    assert (a, b) not in pairs() and (b, a) not in pairs(), "別物の組が候補から外れていない"
    expect_error("INVALID", ops.mark_distinct, graph, a, a, USER)
    expect_error("RESERVED", ops.mark_distinct, graph, a, w.chunk_eid(0), USER)


def test_m10_merge_duplicate_rows_replaces_old_merge(graph, run):
    w = World(run, "m10c")
    first = w.entity("Zzge M10 Pump", ["Part"], chunks=(0,), description="p")
    second = w.entity("Zzge M10 Pump 1", ["Part"], chunks=(1,))
    third = w.entity("Zzge M10 Pump 2", ["Part"], chunks=(2,))
    data = ops.merge_duplicate_rows(graph, json.dumps([{"firstElementId": first, "similarElementIds": [second, third]}]), USER)
    assert data == [{"totalMerged": 3}]
    node = w.node("Zzge M10 Pump")
    assert sorted(node["chunks"]) == sorted(w.chunks) and node["props"]["description"] == "p"
    assert w.ledger(alias_uid("Zzge M10 Pump 1"))["target"] == "Zzge M10 Pump"
    ops_rows = run("MATCH (o:__GraphEditOp__ {op: 'merge'}) WHERE o.summary CONTAINS 'Zzge M10 Pump 1' RETURN o")
    assert len(ops_rows) == 1
    # 統合先が前の統合で消えた行は飛ばす（点検の指摘 medium 1。以前は NOT_FOUND で止めていた）
    data, skipped = ops.merge_duplicate_rows_report(graph, json.dumps([{"firstElementId": second, "similarElementIds": [first]}]),
                                                    USER)
    assert data == [{"totalMerged": 0}] and [(s["row"], s["first_element_id"]) for s in skipped] == [(1, second)]


def test_m10_delete_unconnected_uses_delete_nodes(graph, run):
    w = World(run, "m10d")
    lonely = w.entity("Zzge M10 Lonely")
    data = ops.delete_unconnected(graph, json.dumps([lonely, w.chunk_eid(0), "4:00000000-0000-0000-0000-000000000000:1"]), USER)
    assert data["deleted_ids"] == ["Zzge M10 Lonely"] and data["op_id"]
    assert sorted(s["element_id"] for s in data["skipped"]) == sorted([w.chunk_eid(0), "4:00000000-0000-0000-0000-000000000000:1"])
    assert w.node("Zzge M10 Lonely") is None and w.ledger(tnode_uid("Zzge M10 Lonely")) is None
    assert w.op(data["op_id"])["op"] == "delete_nodes"


# ---- 取り消しの拒否（後で変わったとき）----

def test_undo_is_refused_after_later_changes(graph, run):
    """統合・削除・リレーションの追加と削除・台帳の行の削除の取り消しが、後で変わったときに理由付きで断られること。"""
    w = World(run, "undo")
    t = w.entity("Zzge Undo T", ["Part"])
    s = w.entity("Zzge Undo S", ["Part"])
    x = w.entity("Zzge Undo X", ["Part"])
    # 統合の後に統合先が変わった
    merged = ops.merge(graph, t, "Zzge Undo T", json.dumps([{"element_id": s, "expected_id": "Zzge Undo S"}]), "Zzge Undo T",
                       json.dumps(["Part"]), "target", None, USER)
    ops.update_props(graph, t, "Zzge Undo T", json.dumps({"unit": "u"}), USER)
    error = expect_error("NOT_UNDOABLE", ops.undo, graph, merged["op_id"], USER)
    assert "後の操作" in error.data["reason"]
    # 削除した名前のエンティティが後で作られた（抽出で再び作られた場合）
    gone = w.entity("Zzge Undo Gone")
    deleted = ops.delete_nodes(graph, json.dumps([{"element_id": gone, "expected_id": "Zzge Undo Gone"}]), "false", USER)
    w.entity("Zzge Undo Gone")
    error = expect_error("NOT_UNDOABLE", ops.undo, graph, deleted["op_id"], USER)
    assert error.data["reason"] == "同じ名前「Zzge Undo Gone」のエンティティがその後に作成されました。"
    # リレーションの追加の後に端点が改名された
    created = ops.create_relationship(graph, t, "Zzge Undo T", x, "Zzge Undo X", "LINKS", "{}", USER)
    ops.rename(graph, x, "Zzge Undo X", "Zzge Undo X2", USER)
    expect_error("NOT_UNDOABLE", ops.undo, graph, created["op_id"], USER)
    # リレーションの削除の後に、取り込みの保存で端点の版が変わった
    y = w.entity("Zzge Undo Y", ["Part"])
    rid = w.rel("Zzge Undo X2", "NEAR", "Zzge Undo Y")
    removed = ops.delete_relationship(graph, rid, json.dumps({"src": "Zzge Undo X2", "type": "NEAR", "tgt": "Zzge Undo Y"}), "true", USER)
    run("MATCH (e:__Entity__ {id: 'Zzge Undo Y'}) SET e._v = e._v + 1")
    error = expect_error("NOT_UNDOABLE", ops.undo, graph, removed["op_id"], USER)
    assert "版が" in error.data["reason"]
    # 台帳の行の削除の後に、同じ行が作り直された（別名の行の削除は代表の版を上げるので、エンティティに触れない別物の組で見る）
    z = w.entity("Zzge Undo Z", ["Part"])
    pair = ops.mark_distinct(graph, z, x, USER)
    dropped = ops.ledger_delete(graph, pair["uid"], USER)
    ops.mark_distinct(graph, z, x, USER)
    error = expect_error("NOT_UNDOABLE", ops.undo, graph, dropped["op_id"], USER)
    assert "修正台帳の行" in error.data["reason"]
    # 別名の行の削除の後に代表が変わった
    ops.rename(graph, y, "Zzge Undo Y", "Zzge Undo Y2", USER)
    dropped_alias = ops.ledger_delete(graph, alias_uid("Zzge Undo Y"), USER)
    ops.rename(graph, y, "Zzge Undo Y2", "Zzge Undo Y3", USER)
    error = expect_error("NOT_UNDOABLE", ops.undo, graph, dropped_alias["op_id"], USER)
    assert "後の操作" in error.data["reason"]
    # 別物の登録は取り消せる
    distinct = ops.mark_distinct(graph, t, w.eid("Zzge Undo X2"), USER)
    ops.undo(graph, distinct["op_id"], USER)
    assert w.ledger(distinct["uid"]) is None
    rows = {row["op_id"]: row for row in ops.history_rows(graph, 100)}
    for op_id in (merged["op_id"], deleted["op_id"], created["op_id"], removed["op_id"], dropped["op_id"], dropped_alias["op_id"]):
        assert rows[op_id]["undoable"] is False and rows[op_id]["reason"], rows[op_id]
    assert rows[distinct["op_id"]]["undone"] is True


# ---- 排他・埋め込み ----

def test_busy_while_lease_is_held(graph, run):
    w = World(run, "busy")
    eid = w.entity("Zzge Busy A")
    ops.rename(graph, eid, "Zzge Busy A", "Zzge Busy B", USER)  # 排他のノードを作る
    run("MATCH (l:__GraphLock__ {name: 'graph'}) SET l.lease_until = timestamp() + 60000, l.lease_owner = 'extract:test#1'")
    try:
        error = expect_error("BUSY", ops.rename, graph, eid, "Zzge Busy B", "Zzge Busy C", USER)
        assert "extract:test#1" in error.detail
    finally:
        run("MATCH (l:__GraphLock__ {name: 'graph'}) SET l.lease_until = 0, l.lease_owner = null")
    ops.rename(graph, eid, "Zzge Busy B", "Zzge Busy C", USER)


def test_embedding_is_removed_when_ruri_is_down_and_search_still_works(graph, run, monkeypatch):
    w = World(run, "emb")
    eid = w.entity("Zzge Emb Pump", ["Part"], description="pump")
    ops.update_props(graph, eid, "Zzge Emb Pump", json.dumps({"description": "centrifugal pump"}), USER)
    assert w.node("Zzge Emb Pump")["dim"] == 768
    monkeypatch.setenv("VLLM_EMBEDDING_ENDPOINT", "http://127.0.0.1:9/v1,dummy")
    ops.rename(graph, eid, "Zzge Emb Pump", "Zzge Emb Pump 2", USER)
    assert w.node("Zzge Emb Pump 2")["dim"] is None, "埋め込みを作れないのに古い埋め込みが残った"
    monkeypatch.undo()
    from src.retrieve_only import retrieve_context
    for mode in ("entity_vector", "graph_vector"):
        result = retrieve_context(graph=graph, question="Zzge Emb Pump 2 centrifugal pump", document_names="[]", mode=mode,
                                  embedding_provider="vllm", embedding_model="cl-nagoya/ruri-v3-310m",
                                  retrieval_settings=None, model=None)
        assert result is not None
    # 索引の次元と違うベクトルは書かない
    monkeypatch.setattr(embedding_module, "index_dimension", lambda run_: 1024)
    monkeypatch.setattr(ops, "index_dimension", lambda run_: 1024)
    ops.update_props(graph, eid, "Zzge Emb Pump 2", json.dumps({"description": "pump with seal"}), USER)
    assert w.node("Zzge Emb Pump 2")["dim"] is None


# ---- 読み取りの入口 ----

def test_read_endpoints(graph, run):
    w = World(run, "read")
    eid = w.entity("Zzge Read Valve", ["Component"], chunks=(0, 1), description="v")
    w.entity("Zzge Read Seat", ["Part"])
    w.rel("Zzge Read Valve", "HAS_PART", "Zzge Read Seat", note="x")
    ops.rename(graph, eid, "Zzge Read Valve", "Zzge Read Valve 2", USER)
    ops.set_labels(graph, eid, "Zzge Read Valve 2", json.dumps(["Component"]), USER)
    detail = read.node_detail(graph, eid)
    assert set(detail) >= {"node", "relationships", "neighbors", "chunk_count", "documents", "ledger", "editable", "reason"}
    assert detail["editable"] is True and detail["reason"] == "" and detail["chunk_count"] == 2 and detail["documents"] == [w.doc]
    assert detail["ledger"] == {"aliases": ["Zzge Read Valve"], "type_override": ["Component"], "curated_keys": []}
    assert [r["type"] for r in detail["relationships"]] == ["HAS_PART"]
    assert detail["relationships"][0]["properties"] == {"note": "x"}
    assert [n["properties"]["id"] for n in detail["neighbors"]] == ["Zzge Read Seat"]
    chunk = read.node_detail(graph, w.chunk_eid(0))
    assert chunk["editable"] is False and chunk["reason"]
    hits = read.search_entities(graph, "ｚｚｇｅ ｒｅａｄ")
    assert [h["node"]["properties"]["id"] for h in hits] == ["Zzge Read Valve 2", "Zzge Read Seat"]  # チャンクの多い順
    assert set(hits[0]) == {"node", "chunk_count", "documents"} and hits[0]["documents"] == [w.doc]
    assert read.search_entities(graph, w.chunks[0]) == [] and read.search_entities(graph, w.doc) == []
    exact = read.search_entities(graph, "zzge read valve 2")
    assert exact[0]["node"]["properties"]["id"] == "Zzge Read Valve 2" and exact[0]["chunk_count"] == 2
    hood = read.neighborhood(graph, eid)
    assert sorted(n["properties"]["id"] for n in hood["nodes"]) == ["Zzge Read Seat", "Zzge Read Valve 2"]
    assert len(hood["relationships"]) == 1 and hood["relationships"][0]["type"] == "HAS_PART"
    expect_error("RESERVED", read.neighborhood, graph, w.chunk_eid(0))
    vocabulary = read.vocabulary(graph)
    assert "Component" in vocabulary["labels"] and "Document" not in vocabulary["labels"]
    assert not set(vocabulary["labels"]) & set(RESERVED_LABELS) and "__Entity__" not in vocabulary["labels"]
    assert "HAS_PART" in vocabulary["relationship_types"]
    assert not set(vocabulary["relationship_types"]) & set(RESERVED_RELATIONSHIP_TYPES)
    assert "MEASURED_AT" in vocabulary["edc_relationship_types"] and "TestItem" in vocabulary["edc_labels"]
    assert "PART_OF" not in vocabulary["edc_relationship_types"] and "Document" not in vocabulary["edc_labels"]
    assert vocabulary["reserved_labels"] == DESIGN_RESERVED_LABELS
    assert vocabulary["reserved_relationship_types"] == DESIGN_RESERVED_TYPES


# ---- 工程 S2 の点検の指摘（再現と修正の確認）----

def lock_state(run):
    rows = run("MATCH (l:__GraphLock__ {name: 'graph'}) RETURN l.lease_owner AS owner, l.lease_until AS until, timestamp() AS now")
    return rows[0] if rows else {"owner": None, "until": 0, "now": 0}


def lease_holder(run):
    """今、貸し出し（lease）を持っている者（期限切れなら None）。"""
    state = lock_state(run)
    return state["owner"] if (state["until"] or 0) > state["now"] else None


def conflict_ids(error):
    return sorted(c["node"]["properties"]["id"] for c in error.data["conflicts"])


def test_review_same_key_entities_are_not_taken_over(graph, run):
    """[high 1] 正規化キーが同じ別のエンティティがいるとき、片方の改名・統合・削除の記録がもう片方の抽出を奪わない。"""
    from src.graph_edit.ledger import load_ledger
    w = World(run, "rk")
    upper = w.entity("Zzge RK N=1台目", ["Part"])
    lower = w.entity("zzge rk n=1台目", ["Part"], chunks=(1,))
    target = w.entity("Zzge RK T", ["Part"])
    upper_ref = json.dumps([{"element_id": upper, "expected_id": "Zzge RK N=1台目"}])
    error = expect_error("CONFLICT", ops.rename, graph, upper, "Zzge RK N=1台目", "Zzge RK N1", USER)
    assert conflict_ids(error) == ["zzge rk n=1台目"] and error.data.get("reason")
    error = expect_error("CONFLICT", ops.merge, graph, target, "Zzge RK T", upper_ref, "Zzge RK T", json.dumps(["Part"]),
                         "target", None, USER)
    assert conflict_ids(error) == ["zzge rk n=1台目"]
    # 統合先の名前を替えるとき（統合先の旧い名前が別名になる）
    expect_error("CONFLICT", ops.merge, graph, upper, "Zzge RK N=1台目",
                 json.dumps([{"element_id": target, "expected_id": "Zzge RK T"}]), "Zzge RK T", json.dumps(["Part"]),
                 "target", None, USER)
    error = expect_error("CONFLICT", ops.delete_nodes, graph, upper_ref, "true", USER)
    assert conflict_ids(error) == ["zzge rk n=1台目"]
    assert w.ledger(alias_uid("Zzge RK N=1台目")) is None and w.ledger(tnode_uid("Zzge RK N=1台目")) is None
    assert load_ledger(graph).map_id("zzge rk n=1台目") == ("zzge rk n=1台目", False)
    # 正規化キーが同じものどうしの統合は通る（もう片方も統合の対象なので）
    merged = ops.merge(graph, upper, "Zzge RK N=1台目", json.dumps([{"element_id": lower, "expected_id": "zzge rk n=1台目"}]),
                       "Zzge RK N=1台目", json.dumps(["Part"]), "target", None, USER)
    assert w.ledger(alias_uid("zzge rk n=1台目"))["target"] == "Zzge RK N=1台目"
    ops.undo(graph, merged["op_id"], USER)
    upper, lower = w.eid("Zzge RK N=1台目"), w.eid("zzge rk n=1台目")
    assert w.ledger(alias_uid("zzge rk n=1台目")) is None
    # 削除の記録を残さない削除は通る。まとめて削除すれば記録も残せる
    lone = w.entity("Zzge RK M=2台目", ["Part"])
    w.entity("zzge rk m=2台目", ["Part"])
    ops.delete_nodes(graph, json.dumps([{"element_id": lone, "expected_id": "Zzge RK M=2台目"}]), "false", USER)
    assert w.eid("zzge rk m=2台目") and w.ledger(tnode_uid("zzge rk m=2台目")) is None
    ops.delete_nodes(graph, json.dumps([{"element_id": upper, "expected_id": "Zzge RK N=1台目"},
                                        {"element_id": lower, "expected_id": "zzge rk n=1台目"}]), "true", USER)
    assert w.ledger(tnode_uid("zzge rk n=1台目"))


def test_review_ledger_rewrite_matches_exact_names(graph, run):
    """[high 1] 改名で書き換える削除したリレーションの記録と別物の組は、正規化キーでなく元の名前の完全一致で照合する。"""
    w = World(run, "rx")
    foo = w.entity("Zzge RX Foo")
    low = w.entity("zzge rx foo")
    w.entity("Zzge RX Bar")
    rid = w.rel("zzge rx foo", "LINKS", "Zzge RX Bar")
    ops.delete_relationship(graph, rid, json.dumps({"src": "zzge rx foo", "type": "LINKS", "tgt": "Zzge RX Bar"}), "true", USER)
    ops.mark_distinct(graph, foo, low, USER)
    ops.delete_nodes(graph, json.dumps([{"element_id": low, "expected_id": "zzge rx foo"}]), "false", USER)
    ops.rename(graph, foo, "Zzge RX Foo", "Zzge RX Qux", USER)
    row = w.ledger(trel_uid("zzge rx foo", "LINKS", "Zzge RX Bar"))
    assert row is not None and row["src"] == "zzge rx foo", "別のエンティティの削除したリレーションの記録が改名で書き換えられた"
    assert w.ledger(trel_uid("Zzge RX Qux", "LINKS", "Zzge RX Bar")) is None
    pair = w.ledger(dist_uid("Zzge RX Qux", "zzge rx foo"))
    assert pair is not None and {pair["a"], pair["b"]} == {"Zzge RX Qux", "zzge rx foo"}, "別物の組が消えた"


def test_review_merge_duplicate_rows_skips_overlapping_rows(graph, run):
    """[medium 1] 重なった行（A←B、B←C、D←E）でも途中で止まらず、消えた行を飛ばして続ける。"""
    w = World(run, "md")
    eids = {name: w.entity(f"Zzge MD {name}", ["Part"]) for name in "ABCDEFGHIJ"}

    def rows(*pairs):
        return json.dumps([{"firstElementId": eids[a], "similarElementIds": [eids[b] for b in bs]} for a, bs in pairs])

    assert ops.merge_duplicate_rows(graph, rows(("A", "B"), ("B", "C"), ("D", "E")), USER) == [{"totalMerged": 4}]
    alive = {name for name in "ABCDE" if w.eid(f"Zzge MD {name}")}
    assert alive == {"A", "C", "D"}
    data, skipped = ops.merge_duplicate_rows_report(graph, rows(("F", "G"), ("G", "H"), ("I", "GJ")), USER)
    assert data == [{"totalMerged": 2}]
    assert [(s["row"], s["first_element_id"]) for s in skipped] == [(2, eids["G"]), (3, eids["I"])]
    assert all(s["reason"] for s in skipped)
    assert w.eid("Zzge MD I") and w.eid("Zzge MD J"), "source が消えた行の残りを統合した"


def test_review_document_delete_blocks_undo(graph, run, tmp_path):
    """[medium 2] 文書の削除（B2）で消えたリレーションを、前の操作の取り消しが復活させない。"""
    w1, w2, w3 = World(run, "b2x1", 1), World(run, "b2x2", 1), World(run, "b2x3", 1)
    x = w1.entity("Zzge B2X X", ["Part"])
    y = w1.entity("Zzge B2X Y", ["Part"])
    run("MATCH (c:Chunk {id: $c}), (e:__Entity__ {id: 'Zzge B2X X'}) CREATE (c)-[:HAS_ENTITY]->(e)", c=w2.chunks[0])
    run("MATCH (c:Chunk {id: $c}), (e:__Entity__ {id: 'Zzge B2X Y'}) CREATE (c)-[:HAS_ENTITY]->(e)", c=w3.chunks[0])
    w1.rel("Zzge B2X X", "ONLY_IN_ONE", "Zzge B2X Y")
    changed = ops.update_props(graph, y, "Zzge B2X Y", json.dumps({"note": "n"}), USER)
    graphDBdataAccess(graph).delete_file_from_graph(json.dumps([w1.doc]), json.dumps(["local file"]), "true", str(tmp_path), "")
    assert not run("MATCH (:__Entity__ {id: 'Zzge B2X X'})-[r:ONLY_IN_ONE]->() RETURN r")
    assert w1.node("Zzge B2X Y")["props"]["_v"] == 2 and w1.node("Zzge B2X X")["props"]["_v"] == 1
    expect_error("NOT_UNDOABLE", ops.undo, graph, changed["op_id"], USER)
    assert not run("MATCH (:__Entity__ {id: 'Zzge B2X X'})-[r:ONLY_IN_ONE]->() RETURN r"), "取り消しでリレーションが戻った"
    assert x
    # エンティティを消さない削除でも、HAS_ENTITY を失ったエンティティの版を上げる
    w4 = World(run, "b2x4", 1)
    z = w4.entity("Zzge B2X Z", ["Part"])
    touched = ops.update_props(graph, z, "Zzge B2X Z", json.dumps({"note": "n"}), USER)
    graphDBdataAccess(graph).delete_file_from_graph(json.dumps([w4.doc]), json.dumps(["local file"]), "false", str(tmp_path), "")
    assert w4.node("Zzge B2X Z")["props"]["_v"] == 2
    expect_error("NOT_UNDOABLE", ops.undo, graph, touched["op_id"], USER)


def test_review_reprocess_delete_blocks_undo(graph, run):
    """[medium 2] 再処理の削除（B1）で消えたエンティティとのリレーションを、取り消しが復活させない。"""
    from src.shared.common_fn import execute_graph_query
    from src.shared.constants import QUERY_TO_DELETE_EXISTING_ENTITIES
    w, other = World(run, "b1x", 2), World(run, "b1y", 1)
    k = w.entity("Zzge B1X K", ["Part"])
    run("MATCH (c:Chunk {id: $c}), (e:__Entity__ {id: 'Zzge B1X K'}) CREATE (c)-[:HAS_ENTITY]->(e)", c=other.chunks[0])
    w.entity("Zzge B1X E", ["Part"], chunks=(1,))
    w.rel("Zzge B1X K", "NEAR", "Zzge B1X E")
    changed = ops.update_props(graph, k, "Zzge B1X K", json.dumps({"note": "n"}), USER)
    execute_graph_query(graph, QUERY_TO_DELETE_EXISTING_ENTITIES, {"filename": w.doc})
    assert w.node("Zzge B1X E") is None and w.node("Zzge B1X K")["props"]["_v"] == 2
    w.entity("Zzge B1X E", ["Part"], chunks=(1,))  # 再処理で同じ名前が作り直された
    expect_error("NOT_UNDOABLE", ops.undo, graph, changed["op_id"], USER)
    assert not run("MATCH (:__Entity__ {id: 'Zzge B1X K'})-[r:NEAR]->() RETURN r"), "取り消しでリレーションが戻った"


def test_review_alias_of_another_entity_is_not_taken(graph, run):
    """[medium 3] 他のエンティティの別名と同じ名前への改名・統合は CONFLICT。台帳の別名を消したら aliases もそろえる。"""
    w = World(run, "al")
    x = w.entity("Zzge AL X")
    y = w.entity("Zzge AL Y")
    z = w.entity("Zzge AL Z")
    v = w.entity("Zzge AL V")
    ops.merge(graph, x, "Zzge AL X", json.dumps([{"element_id": y, "expected_id": "Zzge AL Y"}]), "Zzge AL X",
              json.dumps(["Concept"]), "target", None, USER)
    for new_name in ("Zzge AL Y", "zzge al y"):
        error = expect_error("CONFLICT", ops.rename, graph, z, "Zzge AL Z", new_name, USER)
        assert conflict_ids(error) == ["Zzge AL X"], new_name
    error = expect_error("CONFLICT", ops.merge, graph, z, "Zzge AL Z", json.dumps([{"element_id": v, "expected_id": "Zzge AL V"}]),
                         "Zzge AL Y", json.dumps(["Concept"]), "target", None, USER)
    assert conflict_ids(error) == ["Zzge AL X"]
    assert w.ledger(alias_uid("Zzge AL Y"))["target"] == "Zzge AL X"
    assert w.node("Zzge AL X")["props"]["aliases"] == ["Zzge AL Y"]
    dropped = ops.ledger_delete(graph, alias_uid("Zzge AL Y"), USER)
    assert "aliases" not in w.node("Zzge AL X")["props"], "台帳の別名を消しても aliases に残っている"
    ops.undo(graph, dropped["op_id"], USER)
    assert w.node("Zzge AL X")["props"]["aliases"] == ["Zzge AL Y"]
    assert w.ledger(alias_uid("Zzge AL Y"))["target"] == "Zzge AL X"
    # 自分の別名の名前への改名は通る
    ops.rename(graph, x, "Zzge AL X", "Zzge AL Y", USER)
    assert w.ledger(alias_uid("Zzge AL Y")) is None and w.ledger(alias_uid("Zzge AL X"))["target"] == "Zzge AL Y"


def test_review_m3_property_names(graph, run):
    """[low b] M3 のプロパティ名の形を検査し、画面で非表示の項目（text・summary）は書き換えさせない。"""
    w = World(run, "m3n")
    eid = w.entity("Zzge M3N Bolt", ["Part"])
    for key in ("text", "summary", "a b", "1st", "x-y", "x`y", "x.y"):
        expect_error("INVALID", ops.update_props, graph, eid, "Zzge M3N Bolt", json.dumps({key: "v"}), USER)
    ops.update_props(graph, eid, "Zzge M3N Bolt", json.dumps({"材質": "SUS304", "grade_2": "A"}), USER)
    assert w.node("Zzge M3N Bolt")["props"]["材質"] == "SUS304"


def test_review_maintenance_jobs_hold_the_lease(graph, run, monkeypatch, tmp_path):
    """[medium 4] 文書の削除（B2）・再処理の削除（B1）・スキーマの統合・コミュニティの作成も貸し出しを取る。"""
    from langchain_core.language_models.fake import FakeListLLM
    from src import communities, post_processing
    from src import main as main_module
    from src.shared.constants import DELETE_ENTITIES_AND_START_FROM_BEGINNING
    seen = {}

    # B2
    w = World(run, "ls1")
    w.entity("Zzge LS A", ["Part"])
    original_execute = graphDBdataAccess.execute_query

    def spy_execute(self, query, param=None, *args, **kwargs):
        if "DETACH DELETE" in query:
            seen.setdefault("b2", []).append(lease_holder(run))
        return original_execute(self, query, param, *args, **kwargs)
    monkeypatch.setattr(graphDBdataAccess, "execute_query", spy_execute)
    graphDBdataAccess(graph).delete_file_from_graph(json.dumps([w.doc]), json.dumps(["local file"]), "true", str(tmp_path), "")
    assert seen["b2"] and all(seen["b2"]), f"文書の削除が貸し出しを取っていない: {seen['b2']}"
    assert lease_holder(run) is None

    # B1
    w2 = World(run, "ls2")
    w2.entity("Zzge LS B", ["Part"])
    original_graph_query = main_module.execute_graph_query

    def spy_graph_query(graph_, query, params=None, *args, **kwargs):
        if "DETACH DELETE" in query:
            seen.setdefault("b1", []).append(lease_holder(run))
        return original_graph_query(graph_, query, params, *args, **kwargs)
    monkeypatch.setattr(main_module, "execute_graph_query", spy_graph_query)
    main_module.set_status_retry(graph, w2.doc, DELETE_ENTITIES_AND_START_FROM_BEGINNING)
    assert seen["b1"] and all(seen["b1"]), f"再処理の削除が貸し出しを取っていない: {seen['b1']}"
    assert w2.node("Zzge LS B") is None and lease_holder(run) is None

    # スキーマの統合（LLM の応答だけを差し替える）
    w3 = World(run, "ls3")
    a = w3.entity("Zzge LS C", ["Part"])
    b = w3.entity("Zzge LS D", ["Part"])
    ops.set_labels(graph, a, "Zzge LS C", json.dumps(["ZzgeOldKind"]), USER)
    ops.create_relationship(graph, a, "Zzge LS C", b, "Zzge LS D", "ZZGE_OLD_REL", json.dumps({"by": "hand"}), USER)
    rid = w3.rel("Zzge LS D", "ZZGE_OLD_REL", "Zzge LS C")
    ops.delete_relationship(graph, rid, json.dumps({"src": "Zzge LS D", "type": "ZZGE_OLD_REL", "tgt": "Zzge LS C"}), "true", USER)
    mapping = {"nodes": {"ZzgeNewKind": ["ZzgeOldKind"]}, "relationships": {"ZZGE_NEW_REL": ["ZZGE_OLD_REL"]}}
    monkeypatch.setattr(post_processing, "get_llm", lambda model: (FakeListLLM(responses=[json.dumps(mapping)]), model, None))
    original_pp_query = post_processing.execute_graph_query

    def spy_pp_query(graph_, query, params=None, *args, **kwargs):
        if "ZzgeOldKind" in query or "ZZGE_OLD_REL" in query:
            seen.setdefault("schema", []).append(lease_holder(run))
        return original_pp_query(graph_, query, params, *args, **kwargs)
    monkeypatch.setattr(post_processing, "execute_graph_query", spy_pp_query)
    post_processing.graph_schema_consolidation(graph)
    assert seen["schema"] and all(seen["schema"]), f"スキーマの統合が貸し出しを取っていない: {seen['schema']}"
    assert lease_holder(run) is None
    assert sorted(w3.node("Zzge LS C")["labels"]) == ["ZzgeNewKind", "__Entity__"]
    assert w3.ledger(type_uid("Zzge LS C"))["labels"] == ["ZzgeNewKind"], "種類の固定の種類名が書き換わっていない"
    assert w3.ledger(mrel_uid("Zzge LS C", "ZZGE_OLD_REL", "Zzge LS D")) is None
    manual = w3.ledger(mrel_uid("Zzge LS C", "ZZGE_NEW_REL", "Zzge LS D"))
    assert manual is not None and manual["type"] == "ZZGE_NEW_REL", "手動のリレーションの種類名が書き換わっていない"
    assert w3.ledger(trel_uid("Zzge LS D", "ZZGE_OLD_REL", "Zzge LS C")) is None
    tomb = w3.ledger(trel_uid("Zzge LS D", "ZZGE_NEW_REL", "Zzge LS C"))
    assert tomb is not None and tomb["type"] == "ZZGE_NEW_REL", "削除したリレーションの記録の種類名が書き換わっていない"
    assert run("MATCH (:__Entity__ {id: 'Zzge LS C'})-[r:ZZGE_NEW_REL]->(:__Entity__ {id: 'Zzge LS D'}) RETURN r.by AS by") == \
        [{"by": "hand"}], "種類を替えたリレーションのプロパティが落ちた"

    # コミュニティの作成（GDS は試験用 Neo4j に無いので、問い合わせだけを流す代わりの物にする）
    import pandas as pd

    class FakeGds:
        def run_cypher(self, query, params=None):
            return pd.DataFrame(graph.query(query, params or {}))

    def stop_after_check(gds):
        seen["communities"] = lease_holder(run)
        raise RuntimeError("stop here")
    monkeypatch.setattr(communities, "get_gds_driver", lambda *args, **kwargs: FakeGds())
    monkeypatch.setattr(communities, "clear_communities", stop_after_check)
    communities.create_communities(TEST_URI, TEST_USER, TEST_PASSWORD, TEST_DATABASE)
    assert seen.get("communities"), "コミュニティの作成が貸し出しを取っていない"
    assert lease_holder(run) is None


def test_review_lease_heartbeat_and_long_wait(graph, run, monkeypatch):
    """[low a] 貸し出しは持っている間延長し続け、取れないときは 60 回で諦めずに待ち続ける。"""
    from src.graph_edit import replay
    from src.graph_edit.ledger import ensure_schema
    ensure_schema(graph)
    assert replay.LEASE_MAX_WAIT_SECONDS >= 600
    with replay.hold_lease(graph, "zzge:heartbeat", heartbeat=0.2) as token:
        run("MATCH (l:__GraphLock__ {name: 'graph'}) SET l.lease_until = timestamp() + 1000")
        time.sleep(0.8)
        state = lock_state(run)
        assert state["owner"] == token and state["until"] - state["now"] > 60000, "持っている間に延長されていない"
    assert lease_holder(run) is None
    run("MATCH (l:__GraphLock__ {name: 'graph'}) SET l.lease_until = timestamp() + 600000, l.lease_owner = 'extract:other#9'")
    sleeps = []
    real_sleep = time.sleep
    caller = threading.current_thread()

    def fake_sleep(seconds):
        # time.sleep の差し替えはプロセス全体に効く。数えるのは貸し出しを取りに行くこのスレッドの待ちだけにし、
        # 他のスレッド（前の試験の import で動き出した ragas の利用状況の送信のスレッドなど）には本物の sleep を返す
        if threading.current_thread() is not caller:
            return real_sleep(seconds)
        sleeps.append(seconds)
        if len(sleeps) == 100:
            run("MATCH (l:__GraphLock__ {name: 'graph'}) SET l.lease_until = 0, l.lease_owner = null")
    monkeypatch.setattr(replay.time, "sleep", fake_sleep)
    try:
        token = replay.acquire_lease(graph, "extract:zzge-wait")
    finally:
        monkeypatch.undo()
    assert len(sleeps) == 100 and lease_holder(run) == token
    replay.release_lease(graph, token)


def test_default_user_falls_back_to_env_connection_user(monkeypatch):
    """画面がバックエンドの環境変数で接続したとき（ユーザー名もパスワードも送らない）、操作した人は接続のユーザー名になる（V3 で見つけた unknown の修正）。"""
    from src.graph_edit import api as edit_api
    monkeypatch.setenv("NEO4J_USERNAME", "env-user")
    env_conn = Neo4jCredentials(uri=TEST_URI, userName=None, password=None, database=TEST_DATABASE, email=None)
    assert edit_api.default_user("", env_conn) == "env-user"
    assert edit_api.default_user("画面の人", env_conn) == "画面の人"
    with_email = Neo4jCredentials(uri=TEST_URI, userName=None, password=None, database=TEST_DATABASE, email="a@example.com")
    assert edit_api.default_user("", with_email) == "a@example.com"
    explicit = Neo4jCredentials(uri=TEST_URI, userName="neo4j", password="x", database=TEST_DATABASE, email=None)
    assert edit_api.default_user(None, explicit) == "neo4j"
    monkeypatch.delenv("NEO4J_USERNAME")
    assert edit_api.default_user("", env_conn) == "unknown"
