"""工程 S2 の試験 V2: 取り込みの保存の直前と直後の、修正台帳の再適用（設計メモ 4 章、A2）。

1. 純粋な関数（apply_ledger_before_save）の試験。抽出結果は 2 つの形で作る:
   - 標準の抽出: langchain_experimental の LLMGraphTransformer が応答を GraphDocument に直す関数（_convert_to_graph_document）
     そのものに、構造化出力の形の値を渡して作る。リレーションの source/target はノードの一覧とは別の Node で、名前は
     語の頭だけが大文字（title()）になる
   - EDC の抽出: edc_extraction._triplets_to_graph_document に 3 つ組を渡して作る。source/target はノードの一覧と同じ Node
2. 試験用 Neo4j で、実際の保存処理（main.processing_chunks: handle_backticks → 再適用 → save_graphDocuments_in_neo4j →
   merge_relationship_between_chunk_and_entites → post_save）に通す。台帳は編集の API（ops）で作る。LLM と埋め込みの
   呼び出しだけを差し替える。合成の文書・チャンク・エンティティ（名前の正規化キーが zzge で始まる）だけを使い、試験の後で消す。
3. 保存の段の貸し出し（lease）: 保存の最中の編集は BUSY、他の取り込みが持っている間は待つ、同時に取りに来ても 1 つだけ取れる。

実行: 本番の backend を読み取り専用で載せた一時コンテナから、試験用 Neo4j（neo4j-gbdev）に向けて流す
（docs/graph-edit/DESIGN.md の 7 章）。本番の backend コンテナでは流さない
"""
import asyncio
import copy
import json
import os
import threading
import time
from types import SimpleNamespace as NS

import pytest
from langchain_community.graphs.graph_document import GraphDocument
from langchain_core.documents import Document
from langchain_experimental.graph_transformers.llm import _convert_to_graph_document
from neo4j import GraphDatabase

from src import main as main_module
from src.edc_extraction import _triplets_to_graph_document
from src.entities.user_credential import Neo4jCredentials
from src.graph_edit import ops
from src.graph_edit.ledger import Ledger, alias_uid, mrel_uid, tnode_uid, trel_uid, type_uid
from src.graph_edit.normalize import norm_key
from src.graph_edit.replay import (
    acquire_lease, alias_targets, apply_ledger_before_save, release_lease,
)
from src.graph_edit.validate import EditError
from src.shared.common_fn import create_graph_database_connection, handle_backticks_nodes_relationship_id_type
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException

TEST_URI = os.environ.get("GBDEV_TEST_URI", "neo4j://database-gbdev:7687")
TEST_USER = "neo4j"
TEST_PASSWORD = os.environ.get("GBDEV_TEST_PASSWORD", "gbdev-test-only")
TEST_DATABASE = "neo4j"
# 写しの件数。既定は 10/31 の写し。別の写しで流すときは GBDEV_PRISTINE='{"documents": 6, "chunks": 83, "entities": 492}' のように渡す
PRISTINE = json.loads(os.environ.get("GBDEV_PRISTINE", '{"documents": 7, "chunks": 184, "entities": 671}'))
USER = "pytest-s2-replay"

if "gbdev" not in TEST_URI:
    raise RuntimeError(f"この試験は試験用 Neo4j（database-gbdev）でしか流さない: {TEST_URI}")


# ---- 抽出結果の 2 つの形 ----

def standard_doc(nodes, rels, chunk_ids=("c1",)):
    """標準の抽出の形。nodes: [(id, type, props)]、rels: [(src, src_type, rel_type, tgt, tgt_type)]。"""
    parsed = NS(nodes=[NS(id=i, type=t, properties=[NS(key=k, value=v) for k, v in (p or {}).items()]) for i, t, p in nodes],
                relationships=[NS(source_node_id=s, source_node_type=st, target_node_id=o, target_node_type=ot, type=r,
                                  properties=[]) for s, st, r, o, ot in rels])
    graph_nodes, graph_rels = _convert_to_graph_document({"parsed": parsed, "raw": None})
    return GraphDocument(nodes=graph_nodes, relationships=graph_rels,
                         source=Document(page_content="zzge", metadata={"combined_chunk_ids": list(chunk_ids)}))


def edc_doc(nodes, rels, chunk_ids=("c1",)):
    """EDC の抽出の形。3 つ組から作る（ノードの一覧は 3 つ組の主語と目的語）。"""
    types = {i: t for i, t, _ in nodes}
    triplets = [{"subject": s, "subject_type": types.get(s, st), "relation": r, "object": o, "object_type": types.get(o, ot)}
                for s, st, r, o, ot in rels]
    covered = {t["subject"] for t in triplets} | {t["object"] for t in triplets}
    # 3 つ組に出ないノードは、EDC では作られない（試験の値はどのノードも 3 つ組に出るようにする）
    assert covered >= {i for i, _, _ in nodes}, "EDC の形では、どのノードも 3 つ組に出る必要がある"
    return _triplets_to_graph_document(triplets, Document(page_content="zzge", metadata={"combined_chunk_ids": list(chunk_ids)}))


FORMS = {"standard": standard_doc, "edc": edc_doc}


def summary(document):
    return ({(n.id, n.type, json.dumps(n.properties, sort_keys=True, ensure_ascii=False)) for n in document.nodes},
            sorted((r.source.id, r.type.replace(" ", "_").upper(), r.target.id) for r in document.relationships))


def frozen(documents):
    return [(summary(d), [(r.source.id, r.source.type, r.target.id, r.target.type) for r in d.relationships])
            for d in documents]


LEDGER_ROWS = [
    # 別名（統合で使われなくなった名前）
    {"kind": "alias", "uid": alias_uid("zzge SACM645材"), "alias": "zzge SACM645材", "target": "Zzge SACM645"},
    # 改名した端点（旧い名前は別名）。削除したリレーションの記録は新しい名前に追従している
    {"kind": "alias", "uid": alias_uid("Zzge Valve"), "alias": "Zzge Valve", "target": "Zzge Valve2"},
    {"kind": "tombstone_rel", "uid": trel_uid("Zzge Pump", "FEEDS", "Zzge Valve2"), "src": "Zzge Pump", "type": "FEEDS",
     "tgt": "Zzge Valve2"},
    {"kind": "manual_rel", "uid": mrel_uid("Zzge Pump", "SUPPLIES", "Zzge Valve2"), "src": "Zzge Pump", "type": "SUPPLIES",
     "tgt": "Zzge Valve2", "props_json": "{}"},
    # 削除したエンティティ（全角半角だけが違う名前も当たる）と、削除したリレーション
    {"kind": "tombstone_node", "uid": tnode_uid("ZZGE NOISE"), "name": "ZZGE NOISE"},
    {"kind": "tombstone_rel", "uid": trel_uid("Zzge Gear", "USES", "Zzge Pump"), "src": "Zzge Gear", "type": "USES",
     "tgt": "Zzge Pump"},
    # 種類の指定と、修正した説明
    {"kind": "type_override", "uid": type_uid("Zzge Gear"), "entity": "Zzge Gear", "labels": ["Component"]},
    {"kind": "curated", "uid": "cur:Zzge Pump", "entity": "Zzge Pump", "keys": ["description"], "values_json": "{}"},
]
CANON = {"Zzge SACM645": ["Material"], "Zzge Valve2": ["Part"]}

EXTRACTED_NODES = [
    ("zzge SACM645材", "Grade", {"description": "from extraction"}),   # 別名（ノード名）
    ("ｚｚｇｅ ｎｏｉｓｅ", "Concept", {}),                              # 全角の削除済みの名前
    ("Zzge Gear", "Concept", {}),                                       # 種類の指定
    ("Zzge Pump", "Equipment", {"description": "overwritten?", "unit": "m3/h"}),  # 修正した説明
    ("Zzge Valve", "Part", {}),                                         # 改名した端点の旧い名前
    ("Zzge New Thing", "Concept", {}),                                  # 台帳に当たらない
]
EXTRACTED_RELS = [
    ("Zzge Gear", "Concept", "USES", "Zzge Pump", "Equipment"),                 # 削除したリレーション → 落とす
    ("Zzge Pump", "Equipment", "FEEDS", "Zzge Valve", "Part"),                  # 端点の改名の後の削除の記録 → 落とす
    ("ｚｚｇｅ ｎｏｉｓｅ", "Concept", "RELATED_TO", "Zzge Gear", "Concept"),    # 削除したノードが端点 → 落とす
    ("Zzge Valve", "Part", "LINKS", "Zzge Valve2", "Part"),                      # 読み替えで始点と終点が同じ → 落とす
    ("zzge SACM645材", "Grade", "USED_IN", "Zzge Gear", "Concept"),             # 別名（始点）
    ("Zzge New Thing", "Concept", "HAS_MATERIAL", "zzge SACM645材", "Grade"),   # 別名（終点）
    ("Zzge New Thing", "Concept", "RELATED_TO", "Zzge Pump", "Equipment"),      # そのまま
]


def extracted(form):
    nodes = EXTRACTED_NODES + ([("Zzge Valve2", "Part", {})] if form == "edc" else [])
    return [FORMS[form](nodes, EXTRACTED_RELS)]


# ---- 1. 純粋な関数 ----

@pytest.mark.parametrize("form", list(FORMS))
def test_apply_ledger_before_save(form):
    documents = handle_backticks_nodes_relationship_id_type(extracted(form))
    # 入力の形: 標準の抽出ではリレーションの端点がノードの一覧とは別のオブジェクト、EDC では同じオブジェクト
    listed = {id(n) for n in documents[0].nodes}
    ends = [r.source for r in documents[0].relationships] + [r.target for r in documents[0].relationships]
    if form == "standard":
        assert not any(id(e) in listed for e in ends)
    else:
        assert all(id(e) in listed for e in ends)
    before = frozen(documents)
    deep_before = copy.deepcopy(documents)
    ledger = Ledger(LEDGER_ROWS)
    canon = {k: v for k, v in CANON.items() if k in alias_targets(documents, ledger)}
    assert set(canon) == {"Zzge SACM645", "Zzge Valve2"}
    out, stats = apply_ledger_before_save(documents, ledger, canon)
    assert frozen(documents) == before and documents == deep_before, "入力の GraphDocument を書き換えた"
    nodes, rels = summary(out[0])
    by_id = {n.id: n for n in out[0].nodes}
    # 標準の抽出は名前の語の頭だけを大文字にするが、正規化キーで台帳に当たる
    sacm_alias = "Zzge Sacm645材" if form == "standard" else "zzge SACM645材"
    assert sacm_alias in {n.id for n in documents[0].nodes}
    assert set(by_id) == {"Zzge SACM645", "Zzge Gear", "Zzge Pump", "Zzge Valve2", "Zzge New Thing"}
    assert by_id["Zzge SACM645"].type == "Material", "別名で読み替えたノードが代表の種類にそろっていない"
    assert by_id["Zzge Valve2"].type == "Part"
    assert by_id["Zzge Gear"].type == "Component", "種類の指定が当たっていない"
    assert "description" not in by_id["Zzge Pump"].properties, "修正した説明が抽出で上書きされる"
    if form == "standard":
        assert by_id["Zzge Pump"].properties == {"unit": "m3/h"}
        assert by_id["Zzge SACM645"].properties == {"description": "from extraction"}
    assert rels == sorted([("Zzge SACM645", "USED_IN", "Zzge Gear"), ("Zzge New Thing", "HAS_MATERIAL", "Zzge SACM645"),
                           ("Zzge New Thing", "RELATED_TO", "Zzge Pump")])
    for rel in out[0].relationships:
        assert rel.source.id in by_id and rel.target.id in by_id
        # 端点はノードの一覧の Node（種類もそろう）
        assert rel.source is by_id[rel.source.id] and rel.target is by_id[rel.target.id]
    assert stats["aliased"] == 2 and stats["dropped_nodes"] == 1 and stats["dropped_rels"] == 4
    assert stats["protected_props"] == (1 if form == "standard" else 0)
    assert stats["type_overridden"] == 1
    assert out[0].source is documents[0].source, "チャンクの id（source.metadata）を失った"


@pytest.mark.parametrize("form", list(FORMS))
def test_nodes_that_become_the_same_are_merged(form):
    ledger = Ledger([LEDGER_ROWS[1]])
    rels = [("Zzge Valve", "Part", "NEAR", "Zzge Seat", "Part"), ("Zzge Valve2", "Part", "NEAR", "Zzge Seat", "Part")]
    nodes = [("Zzge Valve", "Part", {"description": "a"}), ("Zzge Valve2", "Part", {"unit": "u"}), ("Zzge Seat", "Part", {})]
    out, stats = apply_ledger_before_save([FORMS[form](nodes, rels)], ledger, {"Zzge Valve2": ["Part"]})
    ids = [n.id for n in out[0].nodes]
    assert ids.count("Zzge Valve2") == 1 and "Zzge Valve" not in ids
    assert stats["merged_nodes"] == 1 and stats["aliased"] == 1
    assert all(r.source.id == "Zzge Valve2" for r in out[0].relationships)


@pytest.mark.parametrize("form", list(FORMS))
def test_empty_ledger_changes_nothing(form):
    documents = extracted(form)
    out, stats = apply_ledger_before_save(documents, Ledger([]), {})
    assert [summary(d) for d in out] == [summary(d) for d in documents]
    assert not any(stats.values())


def test_tombstone_matches_normalized_names_and_types():
    ledger = Ledger([{"kind": "tombstone_node", "name": "SACM645"},
                     {"kind": "tombstone_rel", "src": "Ａ　Ｂ", "type": "GOVERNED_BY", "tgt": "c"}])
    assert ledger.map_id("ＳＡＣＭ６４５") == (None, False)
    assert ledger.map_id("sacm 645") == (None, False)
    assert ledger.map_id("SACM645材") == ("SACM645材", False), "末尾の「材」まで落としてはいけない"
    assert ledger.is_tombstoned_rel("a b", "governed by", "Ｃ")


# ---- 2. 試験用 Neo4j で保存処理に通す ----

@pytest.fixture(scope="module")
def driver():
    drv = GraphDatabase.driver(TEST_URI, auth=(TEST_USER, TEST_PASSWORD))
    yield drv
    drv.close()


@pytest.fixture(scope="module")
def credentials():
    return Neo4jCredentials(uri=TEST_URI, userName=TEST_USER, password=TEST_PASSWORD, database=TEST_DATABASE)


@pytest.fixture(scope="module")
def graph(credentials):
    g = create_graph_database_connection(credentials)
    yield g
    g._driver.close()


def make_run(driver):
    def run(query, **params):
        with driver.session(database=TEST_DATABASE) as session:
            return [record.data() for record in session.run(query, params)]
    return run


def cleanup(run, keep):
    ids = [r["id"] for r in run("MATCH (e:__Entity__) RETURN e.id AS id") if "zzge" in norm_key(r["id"])]
    run("UNWIND $ids AS id MATCH (e:__Entity__ {id: id}) DETACH DELETE e", ids=ids)
    run("MATCH (n) WHERE (n:Chunk AND n.id STARTS WITH 'zzge') OR (n:Document AND n.fileName STARTS WITH 'zzge') "
        "DETACH DELETE n")
    run("MATCH (n) WHERE (n:__GraphEdit__ OR n:__GraphEditOp__) AND NOT elementId(n) IN $keep DETACH DELETE n",
        keep=list(keep))
    run("MATCH (l:__GraphLock__ {name: 'graph'}) SET l.lease_until = 0, l.lease_owner = null")


def real_counts(run):
    return run("""
        CALL () { MATCH (d:Document) WHERE d.fileName IS NOT NULL AND NOT d.fileName STARTS WITH 'zzge' RETURN count(d) AS documents }
        CALL () { MATCH (c:Chunk) WHERE NOT c.id STARTS WITH 'zzge' RETURN count(c) AS chunks }
        CALL () { MATCH (e:__Entity__) RETURN count(e) AS entities }
        CALL () { MATCH (e:__Entity__) WHERE e._v IS NOT NULL OR e._curated IS NOT NULL RETURN count(e) AS marked }
        CALL () { MATCH ()-[r]->() RETURN count(r) AS relationships }
        RETURN documents, chunks, entities, marked, relationships""")[0]


@pytest.fixture(scope="module", autouse=True)
def pristine(driver):
    run = make_run(driver)
    keep = {r["eid"] for r in run("MATCH (n) WHERE n:__GraphEdit__ OR n:__GraphEditOp__ OR n:__GraphLock__ "
                                  "RETURN elementId(n) AS eid")}
    had_lock = bool(run("MATCH (l:__GraphLock__) RETURN l LIMIT 1"))
    cleanup(run, keep)
    before = real_counts(run)
    for key, value in PRISTINE.items():
        if before[key] != value:
            pytest.fail(f"試験用 DB が写しの状態でない（{key}={before[key]}、期待 {value}）。restore_gbdev.sh で戻してから流す")
    if before["marked"]:
        pytest.fail("写しのエンティティに _v か _curated が付いている。restore_gbdev.sh で戻してから流す")
    yield keep
    cleanup(run, keep)
    if not had_lock:
        run("MATCH (l:__GraphLock__) WHERE NOT elementId(l) IN $keep DELETE l", keep=list(keep))
    assert real_counts(run) == before, "試験の後で写しが変わった"


@pytest.fixture
def run(driver, pristine):
    run = make_run(driver)
    yield run
    cleanup(run, pristine)


DOC = "zzge_replay.pdf"
CHUNKS = ["zzge-replay-c1", "zzge-replay-c2", "zzge-replay-c3"]


def build_ledger_with_ops(graph, run):
    """編集の API で台帳を作る（統合・削除・リレーションの削除・種類の指定・説明の修正・付け替え・改名・手動の追加）。"""
    run("""CREATE (d:Document {fileName: $doc, status: 'Completed', fileSource: 'local file'})
           WITH d UNWIND range(0, size($chunks) - 1) AS i
           CREATE (c:Chunk {id: $chunks[i], text: 'zzge replay chunk ' + toString(i), position: i + 1, fileName: $doc})
           CREATE (c)-[:PART_OF]->(d)""", doc=DOC, chunks=CHUNKS)

    def entity(name, labels, chunk=0, **props):
        return run("""CREATE (e) SET e = $props WITH e CALL apoc.create.addLabels(e, $labels) YIELD node
                      WITH node MATCH (c:Chunk {id: $chunk}) CREATE (c)-[:HAS_ENTITY]->(node) RETURN elementId(node) AS eid""",
                   props={"id": name, **props}, labels=["__Entity__"] + labels, chunk=CHUNKS[chunk])[0]["eid"]

    def rel(a, t, b):
        return run("MATCH (a:__Entity__ {id: $a}) MATCH (b:__Entity__ {id: $b}) CALL apoc.create.relationship(a, $t, {}, b) "
                   "YIELD rel RETURN elementId(rel) AS rid", a=a, b=b, t=t)[0]["rid"]

    sacm = entity("Zzge SACM645", ["Material"], description="target")
    old = entity("zzge SACM645材", ["Material"], chunk=1)
    noise = entity("Zzge Noise", ["Concept"])
    gear = entity("Zzge Gear", ["Concept"])
    pump = entity("Zzge Pump", ["Equipment"], description="extracted")
    valve = entity("Zzge Valve", ["Part"])
    uses = rel("Zzge Gear", "USES", "Zzge Pump")
    feeds = rel("Zzge Pump", "FEEDS", "Zzge Valve")
    ops.merge(graph, sacm, "Zzge SACM645", json.dumps([{"element_id": old, "expected_id": "zzge SACM645材"}]), "Zzge SACM645",
              json.dumps(["Material"]), "target", None, USER)
    ops.delete_nodes(graph, json.dumps([{"element_id": noise, "expected_id": "Zzge Noise"}]), "true", USER)
    ops.delete_relationship(graph, uses, json.dumps({"src": "Zzge Gear", "type": "USES", "tgt": "Zzge Pump"}), "true", USER)
    ops.set_labels(graph, gear, "Zzge Gear", json.dumps(["Component"]), USER)
    ops.update_props(graph, pump, "Zzge Pump", json.dumps({"description": "手で直した説明"}), USER)
    ops.update_relationship(graph, feeds, json.dumps({"src": "Zzge Pump", "type": "FEEDS", "tgt": "Zzge Valve"}), "SUPPLIES",
                            "false", None, USER)
    ops.rename(graph, valve, "Zzge Valve", "Zzge Valve2", USER)
    ops.create_relationship(graph, valve, "Zzge Valve2", gear, "Zzge Gear", "CONNECTED_TO", json.dumps({"by": "hand"}), USER)
    # 手動のリレーションが何かの理由で無くなった状態（保存の後に作り直されることを見る）
    run("MATCH (:__Entity__ {id: 'Zzge Valve2'})-[r:CONNECTED_TO]->(:__Entity__ {id: 'Zzge Gear'}) DELETE r")
    run("MATCH (:__Entity__ {id: 'Zzge Pump'})-[r:SUPPLIES]->(:__Entity__ {id: 'Zzge Valve2'}) DELETE r")
    return {"sacm": sacm, "gear": gear, "pump": pump, "valve": valve}


def run_processing_chunks(monkeypatch, graph, credentials, documents, model, on_save=None):
    async def fake_llm(*args, **kwargs):
        return documents, 0

    def no_chunk_embeddings(*args, **kwargs):
        return None

    monkeypatch.setattr(main_module, "get_graph_from_llm", fake_llm)
    monkeypatch.setattr(main_module, "create_chunk_embeddings", no_chunk_embeddings)
    if on_save is not None:
        real_save = main_module.save_graphDocuments_in_neo4j

        def save(graph_, docs):
            on_save()
            return real_save(graph_, docs)
        monkeypatch.setattr(main_module, "save_graphDocuments_in_neo4j", save)
    chunk_list = [{"chunk_id": cid, "chunk_doc": Document(page_content=f"zzge replay chunk {i}", metadata={"id": cid, "position": i + 1})}
                  for i, cid in enumerate(CHUNKS)]
    return asyncio.run(main_module.processing_chunks(chunk_list, graph, credentials, DOC, model, None, None, 1, 0, 0, None,
                                                     "vllm", "cl-nagoya/ruri-v3-310m"))


@pytest.mark.parametrize("form", list(FORMS))
def test_replay_through_real_save(form, graph, credentials, run, monkeypatch):
    elements = build_ledger_with_ops(graph, run)
    versions = {r["id"]: r["v"] for r in run("MATCH (e:__Entity__) WHERE e.id STARTS WITH 'Zzge' RETURN e.id AS id, e._v AS v")}
    nodes = EXTRACTED_NODES + ([("Zzge Valve2", "Part", {})] if form == "edc" else [])
    # 2 つ目の文書（別のチャンク）でも、別名が代表に当たり、種類の指定が当たる
    second = FORMS[form]([EXTRACTED_NODES[0], ("Zzge Gear", "Concept", {})], [EXTRACTED_RELS[4]], CHUNKS[2:])
    documents = [FORMS[form](nodes, EXTRACTED_RELS, CHUNKS[:2]), second]
    _, _, latency, _ = run_processing_chunks(monkeypatch, graph, credentials, documents, "edc" if form == "edc" else "gemma4")
    stats = latency["graph_edit_ledger"]
    assert stats["manual_rels_recreated"] == 2 and stats["versioned"] == 5 and stats["dropped_nodes"] == 1
    assert stats["aliased"] == 3 and stats["dropped_rels"] == 4 and stats["type_overridden"] == 2

    # 種類のラベルの無い __Entity__ と、HAS_ENTITY の先で __Entity__ を持たないノードが 0 件（DB 全体）
    assert run("MATCH (e:__Entity__) WHERE size(labels(e)) = 1 RETURN count(e) AS n")[0]["n"] == 0
    assert run("MATCH (:Chunk)-[:HAS_ENTITY]->(n) WHERE NOT n:__Entity__ RETURN count(n) AS n")[0]["n"] == 0
    names = {r["id"] for r in run("MATCH (e:__Entity__) WHERE toLower(e.id) STARTS WITH 'zzge' OR e.id STARTS WITH 'Ｚ' "
                                  "OR e.id STARTS WITH 'ｚ' RETURN e.id AS id")}
    # 削除したものが作られない・別名が当たる（旧い名前のエンティティが作られない）
    assert names == {"Zzge SACM645", "Zzge Gear", "Zzge Pump", "Zzge Valve2", "Zzge New Thing"}, names
    assert not [n for n in names if "noise" in norm_key(n)]

    def labels(name):
        return sorted(run("MATCH (e:__Entity__ {id: $id}) RETURN labels(e) AS l", id=name)[0]["l"])

    assert labels("Zzge SACM645") == ["Material", "__Entity__"], "別名のノードの種類が代表に足された"
    assert labels("Zzge Gear") == ["Component", "__Entity__"], "種類の指定が当たっていない"
    assert labels("Zzge Valve2") == ["Part", "__Entity__"] and labels("Zzge New Thing") == ["Concept", "__Entity__"]
    pump = run("MATCH (e:__Entity__ {id: 'Zzge Pump'}) RETURN e.description AS d, e.unit AS u")[0]
    assert pump["d"] == "手で直した説明", "修正した説明が抽出で上書きされた"
    if form == "standard":
        assert pump["u"] == "m3/h"
    sacm_chunks = {r["c"] for r in run("MATCH (c:Chunk)-[:HAS_ENTITY]->(:__Entity__ {id: 'Zzge SACM645'}) RETURN c.id AS c")}
    assert sacm_chunks == set(CHUNKS), "別名のノードの HAS_ENTITY が代表に張られていない"

    rels = {(r["s"], r["t"], r["e"]) for r in run("""
        MATCH (s:__Entity__)-[r]->(e:__Entity__) WHERE s.id STARTS WITH 'Zzge' OR e.id STARTS WITH 'Zzge'
        RETURN s.id AS s, type(r) AS t, e.id AS e""")}
    assert ("Zzge Gear", "USES", "Zzge Pump") not in rels, "削除したリレーションが作り直された"
    assert ("Zzge Pump", "FEEDS", "Zzge Valve2") not in rels, "付け替えた旧いリレーションが作り直された"
    assert ("Zzge Valve2", "LINKS", "Zzge Valve2") not in rels
    assert ("Zzge Pump", "SUPPLIES", "Zzge Valve2") in rels, "付け替えた新しいリレーション（手動）が作り直されていない"
    assert ("Zzge Valve2", "CONNECTED_TO", "Zzge Gear") in rels, "手動のリレーションが作り直されていない"
    assert run("MATCH (:__Entity__ {id: 'Zzge Valve2'})-[r:CONNECTED_TO]->() RETURN r.by AS by")[0]["by"] == "hand"
    assert {("Zzge SACM645", "USED_IN", "Zzge Gear"), ("Zzge New Thing", "HAS_MATERIAL", "Zzge SACM645"),
            ("Zzge New Thing", "RELATED_TO", "Zzge Pump")} <= rels
    after = {r["id"]: r["v"] for r in run("MATCH (e:__Entity__) WHERE e.id STARTS WITH 'Zzge' RETURN e.id AS id, e._v AS v")}
    for name in ("Zzge SACM645", "Zzge Gear", "Zzge Pump", "Zzge Valve2"):
        assert after[name] == versions[name] + 1, f"{name} の _v が増えていない"
    assert after["Zzge New Thing"] == 1
    lock = run("MATCH (l:__GraphLock__ {name: 'graph'}) RETURN l.lease_until AS until, l.lease_owner AS owner")[0]
    assert lock["until"] == 0 and lock["owner"] is None, "保存の後に貸し出しが返されていない"
    # 保存で版が変わったので、前の操作は取り消せない
    history = ops.history_rows(graph, 50)
    assert all(not row["undoable"] for row in history if "Zzge Pump" in row["summary"] or "Zzge Gear" in row["summary"])
    assert elements


def test_edit_is_busy_while_saving(graph, credentials, run, monkeypatch):
    elements = build_ledger_with_ops(graph, run)
    seen = []

    def try_edit():
        try:
            ops.rename(graph, elements["pump"], "Zzge Pump", "Zzge Pump X", USER)
            seen.append("done")
        except EditError as e:
            seen.append(e.code)

    documents = [standard_doc([("Zzge Pump", "Equipment", {})], [("Zzge Pump", "Equipment", "RELATED_TO", "Zzge Gear", "Concept")],
                              CHUNKS[:1])]
    run_processing_chunks(monkeypatch, graph, credentials, documents, "gemma4", on_save=try_edit)
    assert seen == ["BUSY"], "保存の最中に編集が通った"
    ops.rename(graph, elements["pump"], "Zzge Pump", "Zzge Pump X", USER)  # 保存の後は通る


def test_lease_waits_and_is_exclusive(graph, run):
    from src.graph_edit.ledger import ensure_schema
    ensure_schema(graph)
    run("MATCH (l:__GraphLock__ {name: 'graph'}) SET l.lease_until = timestamp() + 1500, l.lease_owner = 'extract:other#1'")
    start = time.time()
    token = acquire_lease(graph, "extract:zzge", attempts=10, interval=0.5)
    waited = time.time() - start
    assert 0.9 <= waited < 5, f"他の取り込みの貸し出しを待っていない（{waited:.2f} 秒）"
    assert run("MATCH (l:__GraphLock__ {name: 'graph'}) RETURN l.lease_owner AS o")[0]["o"] == token
    release_lease(graph, token)
    lock = run("MATCH (l:__GraphLock__ {name: 'graph'}) RETURN l.lease_until AS u, l.lease_owner AS o")[0]
    assert lock == {"u": 0, "o": None}
    run("MATCH (l:__GraphLock__ {name: 'graph'}) SET l.lease_until = timestamp() + 60000, l.lease_owner = 'extract:other#2'")
    with pytest.raises(LLMGraphBuilderException):
        acquire_lease(graph, "extract:zzge", attempts=2, interval=0.1)
    run("MATCH (l:__GraphLock__ {name: 'graph'}) SET l.lease_until = 0, l.lease_owner = null")
    # 同時に取りに来ても 1 つだけ取れる
    results, barrier = [], threading.Barrier(8)

    def take():
        barrier.wait()
        try:
            results.append(acquire_lease(graph, "extract:race", attempts=1, interval=0))
        except LLMGraphBuilderException:
            results.append(None)

    threads = [threading.Thread(target=take) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in results if r]
    assert len(winners) == 1, f"貸し出しが {len(winners)} 本同時に取れた"
    release_lease(graph, winners[0])


def test_lease_is_renewed_at_each_stage(graph, credentials, run, monkeypatch):
    """[点検 low a] 保存・HAS_ENTITY・保存の後の処理の各段で貸し出しを延長する。"""
    build_ledger_with_ops(graph, run)
    calls = []
    real_renew = main_module.renew_lease

    def counting_renew(graph_, token):
        calls.append(token)
        return real_renew(graph_, token)
    monkeypatch.setattr(main_module, "renew_lease", counting_renew)
    documents = [standard_doc([("Zzge Pump", "Equipment", {})], [("Zzge Pump", "Equipment", "RELATED_TO", "Zzge Gear", "Concept")],
                              CHUNKS[:1])]
    run_processing_chunks(monkeypatch, graph, credentials, documents, "gemma4")
    assert len(calls) >= 3 and len(set(calls)) == 1, calls
