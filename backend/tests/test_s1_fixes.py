"""工程 S1（前提の不具合の修正 B1・B2・B4・B5・B8・B9）の試験 V0。

試験用 Neo4j（neo4j-gbdev。neo4j-kgb の写しで、文書 7、チャンク 184、エンティティ 671）で行う。
書き込む試験は、開いたトランザクションの中で本番と同じ関数・問い合わせを流して確かめ、最後に取り消す。
このため写しは変わらない。始める前と終わった後に写しの件数を確かめ、違えば止める
（戻すときは試験の場の用意で作った restore_gbdev.sh を使う）。

期待する件数は、同じ写しの生の行（PART_OF・HAS_ENTITY・リレーション）から、設計メモ 5 章の手順を
Python で数え直したものと比べる（問い合わせを同じ形で書き写した答え合わせにしない）。

点検の指摘への直しの試験も含む: B9 の再開の一覧（get_chunkId_chunkDoc_list と processing_source を通す）、
B2 の大きな文書での重さ・共有のチャンクの fileName・どの文書にも属さないチャンクの扱い、B5 の全文索引が無いとき、
埋め込みモデルの変更で索引を作り直せなかったときの応答。

実行: 本番の backend を読み取り専用で載せた一時コンテナから、試験用 Neo4j（neo4j-gbdev）に向けて流す
（docs/graph-edit/DESIGN.md の 7 章）。本番の backend コンテナでは流さない
"""
import asyncio
import json
import os
import time
from collections import defaultdict

import pytest
from langchain_community.graphs.graph_document import GraphDocument, Node
from langchain_core.documents import Document
from langchain_neo4j import Neo4jVector
from neo4j import GraphDatabase

from src import main as main_module
from src.entities.source_extract_params import SourceScanExtractParams
from src.entities.user_credential import Neo4jCredentials
from src.graphDB_dataAccess import RESERVED_RELATIONSHIP_TYPES, graphDBdataAccess
from src.main import get_chunkId_chunkDoc_list, processing_source
from src.make_relationships import merge_relationship_between_chunk_and_entites
from src.post_processing import create_vector_fulltext_indexes
from src.QA_integration import ensure_keyword_index, initialize_neo4j_vector
from src.rerank_retriever import build_candidate_db, CHUNK_CANDIDATE_QUERY
from src.shared.common_fn import create_graph_database_connection, load_embedding_model
from src.shared.constants import (
    CHAT_MODE_CONFIG_MAP, CHAT_VECTOR_GRAPH_FULLTEXT_MODE, QUERY_TO_DELETE_EXISTING_ENTITIES, QUERY_TO_GET_CHUNKS,
    QUERY_TO_GET_LAST_PROCESSED_CHUNK_POSITION, QUERY_TO_GET_LAST_PROCESSED_CHUNK_WITHOUT_ENTITY,
    START_FROM_LAST_PROCESSED_POSITION,
)
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException

TEST_URI = os.environ.get("GBDEV_TEST_URI", "neo4j://database-gbdev:7687")
TEST_USER = "neo4j"
TEST_PASSWORD = os.environ.get("GBDEV_TEST_PASSWORD", "gbdev-test-only")
TEST_DATABASE = "neo4j"
EMBEDDING_PROVIDER = "vllm"
EMBEDDING_MODEL = "cl-nagoya/ruri-v3-310m"

TARGET = "VTR-T004C.pdf"     # 再処理・削除する文書
SHARED_WITH = "VTR-T005B.pdf"  # TARGET とチャンク 2 つを共有する文書
# 写しの件数。既定は 10/31 の写し。別の写しで流すときは GBDEV_PRISTINE='{"documents": 6, "chunks": 83, "entities": 492}' のように渡す
PRISTINE = json.loads(os.environ.get("GBDEV_PRISTINE", '{"documents": 7, "chunks": 184, "entities": 671}'))

# 修正前の問い合わせ（git の HEAD のもの）。修正前の不具合を同じ写しで再現し、試験が不具合を見分けることを示す
OLD_QUERY_TO_DELETE_EXISTING_ENTITIES = """
    MATCH (d:Document {fileName:$filename})
    WITH d
    MATCH (d)<-[:PART_OF]-(c:Chunk)
    WITH d,c
    MATCH (c)-[:HAS_ENTITY]->(e)
    WHERE NOT EXISTS { (e)<-[:HAS_ENTITY]-()<-[:PART_OF]-(d2:Document) }
    DETACH DELETE e
    """
OLD_QUERY_TO_GET_CHUNKS = """
    MATCH (d:Document)
    WHERE d.fileName = $filename
    WITH d
    OPTIONAL MATCH (d)<-[:PART_OF|FIRST_CHUNK]-(c:Chunk)
    RETURN c.id as id, c.text as text, c.position as position
    """
OLD_HAS_ENTITY_QUERY = """
    UNWIND $batch_data AS data
    MATCH (c:Chunk {id: data.chunk_id})
    CALL apoc.merge.node([data.node_type], {id: data.node_id}) YIELD node AS n
    MERGE (c)-[:HAS_ENTITY]->(n)
    """

if "gbdev" not in TEST_URI:
    raise RuntimeError(f"この試験は試験用 Neo4j（database-gbdev）でしか流さない: {TEST_URI}")


class TxGraph:
    """Neo4jGraph の代わりに、開いたトランザクションの中で問い合わせを流す（試験の後で取り消すため）。"""

    def __init__(self, tx, database=TEST_DATABASE):
        self.tx = tx
        self._database = database

    def query(self, query, params=None, session_params=None):
        return [record.data() for record in self.tx.run(query, params or {})]


@pytest.fixture(scope="module")
def driver():
    drv = GraphDatabase.driver(TEST_URI, auth=(TEST_USER, TEST_PASSWORD))
    yield drv
    drv.close()


def counts(run):
    # 排他のノード（__GraphLock__）は数えない（文書の削除が貸し出しを取るときに、無ければ作る）
    q = """
    CALL () { MATCH (d:Document) WHERE d.fileName IS NOT NULL RETURN count(d) AS documents }
    CALL () { MATCH (c:Chunk) RETURN count(c) AS chunks }
    CALL () { MATCH (e:__Entity__) RETURN count(e) AS entities }
    CALL () { MATCH (c:Chunk) WHERE NOT EXISTS { (c)-[:PART_OF]->(:Document) } RETURN count(c) AS orphan_chunks }
    CALL () { MATCH (n) WHERE NOT n:__Entity__ AND NOT n:Chunk AND NOT n:Document AND NOT n:Session AND NOT n:Message
                AND NOT n:__GraphLock__
              RETURN count(n) AS other_nodes }
    CALL () { MATCH (n) WHERE NOT n:__GraphLock__ RETURN count(n) AS nodes }
    CALL () { MATCH ()-[r]->() RETURN count(r) AS relationships }
    RETURN documents, chunks, entities, orphan_chunks, other_nodes, nodes, relationships
    """
    return dict(run(q).single())


@pytest.fixture(scope="module", autouse=True)
def pristine_copy(driver):
    """写しが読み込んだ直後の状態であることを確かめ、試験の後も変わっていないことを確かめる。"""
    with driver.session(database=TEST_DATABASE) as session:
        before = counts(session.run)
        flags = session.run("""
            CALL () { MATCH (e) WHERE e._curated IS NOT NULL RETURN count(e) AS curated }
            CALL () { MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c) AS chunks_without_embedding }
            RETURN curated, chunks_without_embedding""").single()
    for key, value in PRISTINE.items():
        if before[key] != value:
            pytest.fail(f"試験用 DB が写しの状態でない（{key}={before[key]}、期待 {value}）。restore_gbdev.sh で戻してから流す")
    if before["orphan_chunks"] or flags["curated"] or flags["chunks_without_embedding"]:
        pytest.fail(f"試験用 DB が写しの状態でない（{before}, {dict(flags)}）。restore_gbdev.sh で戻してから流す")
    yield before
    with driver.session(database=TEST_DATABASE) as session:
        after = counts(session.run)
    assert after == before, f"試験の後で写しが変わった: {before} -> {after}"


@pytest.fixture
def tx(driver):
    """試験の書き込みを入れるトランザクション。試験の後で必ず取り消す。"""
    session = driver.session(database=TEST_DATABASE)
    transaction = session.begin_transaction()
    yield transaction
    if transaction.closed():
        session.close()
        return
    transaction.rollback()
    session.close()


# ---------- 生の行から、設計メモの手順で期待する集合を数える ----------

def load_structure(run):
    st = {"docs_of": defaultdict(set), "chunks_of": defaultdict(set), "ents_of": defaultdict(set)}
    for row in run("MATCH (c:Chunk) OPTIONAL MATCH (c)-[:PART_OF]->(d:Document) RETURN c.id AS chunk, d.fileName AS doc"):
        st["docs_of"][row["chunk"]]
        if row["doc"] is not None:
            st["docs_of"][row["chunk"]].add(row["doc"])
    for row in run("MATCH (c:Chunk)-[:HAS_ENTITY]->(e) RETURN c.id AS chunk, elementId(e) AS entity"):
        st["chunks_of"][row["entity"]].add(row["chunk"])
        st["ents_of"][row["chunk"]].add(row["entity"])
    st["entity_rels"] = [dict(r) for r in run(
        "MATCH (a:__Entity__)-[r]->(b:__Entity__) RETURN elementId(r) AS rel, type(r) AS type, "
        "elementId(a) AS a, elementId(b) AS b, a.id AS a_id, b.id AS b_id")]
    st["curated"] = {r["e"] for r in run("MATCH (e) WHERE e._curated = true RETURN elementId(e) AS e")}
    return st


def expected_b1(st, target):
    """B1: target のチャンクから指され、他の文書に PART_OF するチャンクからは指されず、手動で修正していないもの。"""
    own = {c for c, docs in st["docs_of"].items() if target in docs}
    touched = {e for c in own for e in st["ents_of"][c]}
    shared = {e for e in touched if any(st["docs_of"][c] - {target} for c in st["chunks_of"][e])}
    return touched - shared - st["curated"], shared


def expected_b2(st, docs, delete_entities=True):
    """B2 の削除の手順 1〜3（設計メモ 5 章）。"""
    docs = set(docs)
    del_chunks = {c for c, ds in st["docs_of"].items() if ds & docs and ds <= docs}
    remaining = {c for c, ds in st["docs_of"].items() if ds - docs}
    if not delete_entities:
        return del_chunks, set(), set()
    del_entities = {e for e, cs in st["chunks_of"].items()
                    if cs & del_chunks and not cs & remaining and e not in st["curated"]}
    del_rels = set()
    for r in st["entity_rels"]:
        a, b = r["a"], r["b"]
        if r["type"] in RESERVED_RELATIONSHIP_TYPES or a in del_entities or b in del_entities:
            continue
        together = st["chunks_of"][a] & st["chunks_of"][b]
        if together & del_chunks and not together & remaining:
            del_rels.add(r["rel"])
    return del_chunks, del_entities, del_rels


def run_delete(tx, filenames, delete_entities, tmp_path):
    data_access = graphDBdataAccess(TxGraph(tx))
    return data_access.delete_file_from_graph(
        filenames=f'["{filenames}"]', source_types='["local file"]', deleteEntities=delete_entities,
        merged_dir=str(tmp_path), uri=TEST_URI)


# ---------- (1) B1: 再処理の「エンティティを消して最初から」 ----------

def test_b1_reprocess_deletes_only_entities_of_this_document(tx):
    st = load_structure(tx.run)
    expected, shared = expected_b1(st, TARGET)
    assert (len(expected), len(shared)) == (42, 43)

    TxGraph(tx).query(QUERY_TO_DELETE_EXISTING_ENTITIES, {"filename": TARGET})

    alive = {r["e"] for r in tx.run("MATCH (e:__Entity__) RETURN elementId(e) AS e")}
    assert not expected & alive, "消すべきエンティティが残っている"
    assert shared <= alive, "他の文書と共有するエンティティが消えた"
    assert len(alive) == PRISTINE["entities"] - 42


def test_b1_keeps_curated_entities(tx):
    st = load_structure(tx.run)
    expected, _ = expected_b1(st, TARGET)
    curated = sorted(expected)[:3]
    tx.run("UNWIND $ids AS id MATCH (e) WHERE elementId(e) = id SET e._curated = true", ids=curated)

    TxGraph(tx).query(QUERY_TO_DELETE_EXISTING_ENTITIES, {"filename": TARGET})

    alive = {r["e"] for r in tx.run("MATCH (e:__Entity__) RETURN elementId(e) AS e")}
    assert set(curated) <= alive, "手動で修正したエンティティ（_curated）が消えた"
    assert len(alive) == PRISTINE["entities"] - 42 + len(curated)


def test_b1_old_query_deleted_shared_entities(tx):
    """修正前の問い合わせは、共有のエンティティまで 85 件すべてを消していた（不具合の再現）。"""
    before = tx.run("MATCH (e:__Entity__) RETURN count(e) AS n").single()["n"]
    tx.run(OLD_QUERY_TO_DELETE_EXISTING_ENTITIES, filename=TARGET).consume()
    after = tx.run("MATCH (e:__Entity__) RETURN count(e) AS n").single()["n"]
    assert before - after == 85


# ---------- (2)(3) B2: 文書の削除 ----------

def shared_chunk_ids(st):
    return {c for c, ds in st["docs_of"].items() if TARGET in ds and len(ds) > 1}


def test_b2_reserved_relationship_types_match_design():
    """B2 で消さない予約のリレーション種類は、設計メモ 2.2 の一覧と同じ。"""
    assert set(RESERVED_RELATIONSHIP_TYPES) == {
        "HAS_ENTITY", "PART_OF", "NEXT_CHUNK", "FIRST_CHUNK", "SIMILAR", "IN_COMMUNITY", "PARENT_COMMUNITY",
        "NEXT", "LAST_MESSAGE", "REFERS_TO", "ALIAS_OF", "WHEN", "MENTIONS", "HAS_SECTION", "IN_SECTION"}


def test_b2_delete_document_with_entities(tx, tmp_path, caplog):
    st = load_structure(tx.run)
    del_chunks, del_entities, del_rels = expected_b2(st, [TARGET])
    shared = shared_chunk_ids(st)
    assert (len(del_chunks), len(del_entities), len(del_rels), len(shared)) == (12, 42, 9, 2)
    assert {next(iter(st["docs_of"][c] - {TARGET})) for c in shared} == {SHARED_WITH}
    # 消すリレーションは、両端とも共有のエンティティ（両端が残る）で、TARGET のチャンクにしか一緒に出ないもの
    rel_by_id = {r["rel"]: r for r in st["entity_rels"]}
    for rel in del_rels:
        r = rel_by_id[rel]
        for end in (r["a"], r["b"]):
            assert st["chunks_of"][end] - del_chunks, "消すリレーションの端が共有のエンティティでない"

    with caplog.at_level("INFO"):
        assert run_delete(tx, TARGET, "true", tmp_path) == 1
    # 削除のログの件数: 明示的に消すリレーションは両端が残るものだけ（端ごと消えるリレーションはエンティティと一緒に消える）
    assert any("'chunks': 12, 'entities': 42, 'relationships': 9" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records if "Deleting" in r.getMessage()]

    after = counts(tx.run)
    assert after["orphan_chunks"] == 0, "どの文書にも PART_OF しないチャンクが残った"
    assert after["documents"] == PRISTINE["documents"] - 1
    assert after["chunks"] == PRISTINE["chunks"] - 12
    assert after["entities"] == PRISTINE["entities"] - 42
    assert after["other_nodes"] == 0
    rows = tx.run("UNWIND $ids AS id MATCH (c:Chunk {id: id}) "
                  "RETURN c.id AS id, [(c)-[:PART_OF]->(d:Document) | d.fileName] AS docs", ids=list(shared)).data()
    assert {r["id"] for r in rows} == shared, "共有のチャンクが消えた"
    assert all(r["docs"] == [SHARED_WITH] for r in rows)
    alive_rels = {r["rel"] for r in tx.run("MATCH (:__Entity__)-[r]->(:__Entity__) RETURN elementId(r) AS rel")}
    assert not del_rels & alive_rels, "TARGET のチャンクにしか一緒に出ないリレーションが残った"
    # それ以外のリレーション（両端が残るもの）はすべて残る
    keep = {r["rel"] for r in st["entity_rels"]
            if r["a"] not in del_entities and r["b"] not in del_entities and r["rel"] not in del_rels}
    assert alive_rels == keep


def test_b2_keeps_manual_and_curated(tx, tmp_path):
    """manual_rel に記録したリレーションと、手動で修正したエンティティは消さない。"""
    st = load_structure(tx.run)
    del_chunks, del_entities, del_rels = expected_b2(st, [TARGET])
    rel_by_id = {r["rel"]: r for r in st["entity_rels"]}
    manual = rel_by_id[sorted(del_rels)[0]]
    tx.run("CREATE (g:__GraphEdit__ {kind: 'manual_rel', uid: 'mrel:' + $src + '|' + $type + '|' + $tgt, "
           "src: $src, type: $type, tgt: $tgt, props_json: '{}'})",
           src=manual["a_id"], type=manual["type"], tgt=manual["b_id"])
    curated = sorted(del_entities)[0]
    tx.run("MATCH (e) WHERE elementId(e) = $id SET e._curated = true", id=curated)

    run_delete(tx, TARGET, "true", tmp_path)

    alive_rels = {r["rel"] for r in tx.run("MATCH (:__Entity__)-[r]->(:__Entity__) RETURN elementId(r) AS rel")}
    assert manual["rel"] in alive_rels, "manual_rel に記録したリレーションが消えた"
    assert not (del_rels - {manual["rel"]}) & alive_rels
    assert tx.run("MATCH (e) WHERE elementId(e) = $id RETURN count(e) AS n", id=curated).single()["n"] == 1, \
        "手動で修正したエンティティ（_curated）が消えた"
    assert counts(tx.run)["entities"] == PRISTINE["entities"] - 42 + 1


def test_b2_delete_document_only_keeps_shared_chunks(tx, tmp_path):
    st = load_structure(tx.run)
    del_chunks, _, _ = expected_b2(st, [TARGET], delete_entities=False)
    shared = shared_chunk_ids(st)
    assert len(del_chunks) == 12

    assert run_delete(tx, TARGET, "false", tmp_path) == 1

    after = counts(tx.run)
    assert after["orphan_chunks"] == 0
    assert after["documents"] == PRISTINE["documents"] - 1
    assert after["chunks"] == PRISTINE["chunks"] - 12
    assert after["entities"] == PRISTINE["entities"], "文書だけ消すのにエンティティが消えた"
    rows = tx.run("UNWIND $ids AS id MATCH (c:Chunk {id: id}) "
                  "RETURN c.id AS id, [(c)-[:PART_OF]->(d:Document) | d.fileName] AS docs", ids=list(shared)).data()
    assert {r["id"] for r in rows} == shared, "文書だけ消すのに共有のチャンクが消えた"
    assert all(r["docs"] == [SHARED_WITH] for r in rows)
    remaining_ids = {r["id"] for r in tx.run("MATCH (c:Chunk) RETURN c.id AS id")}
    assert not del_chunks & remaining_ids


def test_b2_delete_two_documents_sharing_chunks(tx, tmp_path):
    """共有する 2 文書をまとめて消すと、共有のチャンクも消える（D 以外の文書に PART_OF しないため）。"""
    st = load_structure(tx.run)
    del_chunks, del_entities, del_rels = expected_b2(st, [TARGET, SHARED_WITH])
    assert shared_chunk_ids(st) <= del_chunks

    graphDBdataAccess(TxGraph(tx)).delete_file_from_graph(
        filenames=f'["{TARGET}", "{SHARED_WITH}"]', source_types='["local file"]', deleteEntities="true",
        merged_dir=str(tmp_path), uri=TEST_URI)

    after = counts(tx.run)
    assert after["orphan_chunks"] == 0
    assert after["chunks"] == PRISTINE["chunks"] - len(del_chunks)
    assert after["entities"] == PRISTINE["entities"] - len(del_entities)
    alive_rels = {r["rel"] for r in tx.run("MATCH (:__Entity__)-[r]->(:__Entity__) RETURN elementId(r) AS rel")}
    assert not del_rels & alive_rels


@pytest.mark.parametrize("delete_entities", ["true", "false"])
def test_b2_shared_chunks_take_the_name_of_the_remaining_document(tx, tmp_path, delete_entities):
    """共有のチャンクを残すとき、fileName が消す文書の名前なら残る文書の名前に替える（文書で絞る検索はチャンクの fileName
    で絞るので、消した文書の名前のままだと、どの文書で絞っても出ない）。他のチャンクの fileName は変えない。"""
    st = load_structure(tx.run)
    shared = shared_chunk_ids(st)

    def file_names():
        return {r["id"]: r["f"] for r in tx.run("MATCH (c:Chunk) RETURN c.id AS id, c.fileName AS f")}

    before = file_names()
    # 写しでは、共有のチャンクの fileName は後で処理した SHARED_WITH を指す
    assert {before[c] for c in shared} == {SHARED_WITH}
    run_delete(tx, SHARED_WITH, delete_entities, tmp_path)

    after = file_names()
    assert {after[c] for c in shared} == {TARGET}
    assert {c: f for c, f in after.items() if c not in shared} == {c: before[c] for c in after if c not in shared}
    rows = tx.run("UNWIND $ids AS id MATCH (c:Chunk {id: id}) RETURN [(c)-[:PART_OF]->(d:Document) | d.fileName] AS docs",
                  ids=list(shared)).data()
    assert all(r["docs"] == [TARGET] for r in rows)


def test_b2_keeps_file_name_that_names_a_remaining_document(tx, tmp_path):
    """TARGET を消すときは、共有のチャンクの fileName は残る文書（SHARED_WITH）を指しているので変えない。"""
    shared = shared_chunk_ids(load_structure(tx.run))
    run_delete(tx, TARGET, "true", tmp_path)
    names = {r["f"] for r in tx.run("MATCH (c:Chunk) WHERE c.id IN $ids RETURN c.fileName AS f", ids=list(shared))}
    assert names == {SHARED_WITH}


def build_documents(tx, docs, chunks, entities, has):
    """合成の文書。docs: [fileName]、chunks: {chunk_id: [fileName...]}、entities: [id]、has: [(chunk_id, entity_id)]"""
    tx.run("UNWIND $docs AS f CREATE (:Document {fileName: f, fileSource: 'local file', status: 'Completed'})", docs=docs).consume()
    for i, (cid, files) in enumerate(chunks.items(), 1):
        tx.run("CREATE (c:Chunk {id: $id, text: $id, position: $p}) WITH c "
               "UNWIND $files AS f MATCH (d:Document {fileName: f}) CREATE (c)-[:PART_OF]->(d)", id=cid, p=i, files=files).consume()
    tx.run("UNWIND $ids AS id CREATE (:__Entity__:Concept {id: id})", ids=entities).consume()
    tx.run("UNWIND $has AS h MATCH (c:Chunk {id: h[0]}), (e:__Entity__ {id: h[1]}) CREATE (c)-[:HAS_ENTITY]->(e)",
           has=[list(h) for h in has]).consume()


@pytest.mark.parametrize("operation", ["reprocess", "delete"])
def test_b1_and_b2_ignore_chunks_without_document(tx, tmp_path, operation):
    """どの文書にも PART_OF しないチャンク（以前の削除の残り）から指されるだけのエンティティは、再処理（B1）でも削除（B2）でも
    消す（どちらも、残る出どころを「他の文書に PART_OF するチャンク」で判定する）。他の文書のチャンクから指されるものは残す。"""
    build_documents(tx, docs=["S1V_D1.pdf", "S1V_D2.pdf"],
                    chunks={"s1v-c1": ["S1V_D1.pdf"], "s1v-c2": ["S1V_D2.pdf"], "s1v-orphan": []},
                    entities=["S1V_ORPHAN_ONLY", "S1V_SHARED"],
                    has=[("s1v-c1", "S1V_ORPHAN_ONLY"), ("s1v-orphan", "S1V_ORPHAN_ONLY"),
                         ("s1v-c1", "S1V_SHARED"), ("s1v-c2", "S1V_SHARED")])
    if operation == "reprocess":
        TxGraph(tx).query(QUERY_TO_DELETE_EXISTING_ENTITIES, {"filename": "S1V_D1.pdf"})
    else:
        run_delete(tx, "S1V_D1.pdf", "true", tmp_path)
    alive = {r["id"] for r in tx.run("MATCH (e:__Entity__) WHERE e.id STARTS WITH 'S1V_' RETURN e.id AS id")}
    assert alive == {"S1V_SHARED"}
    assert tx.run("MATCH (c:Chunk {id: 's1v-orphan'}) RETURN count(c) AS n").single()["n"] == 1   # チャンク自体は消さない


def build_big_document(tx, n_chunks, n_entities, hub_out, hub_in):
    """点検で重さを測った形の大きな文書 S1V_BIG.pdf。中心のエンティティ S1V_HUB は全チャンクと、他の文書 S1V_OTHER.pdf の
    チャンク 1 つから指される。エンティティ S1V_BE{i} は大きな文書のチャンク 2 つから指され、偶数の i は他の文書のチャンクからも
    指される。中心から hub_out 本・中心へ hub_in 本のリレーションと、S1V_BE{i} -> S1V_BE{i+1} の鎖を張る。"""
    tx.run("""
        CREATE (big:Document {fileName: 'S1V_BIG.pdf', fileSource: 'local file'}),
               (other:Document {fileName: 'S1V_OTHER.pdf', fileSource: 'local file'})
        WITH big, other
        UNWIND range(1, $n_chunks) AS i
        CREATE (c:Chunk {id: 's1v-big-' + i, position: i, fileName: 'S1V_BIG.pdf'})-[:PART_OF]->(big)
        WITH other, collect(c) AS big_chunks
        UNWIND range(1, $n_other) AS i
        CREATE (o:Chunk {id: 's1v-oth-' + i, position: i, fileName: 'S1V_OTHER.pdf'})-[:PART_OF]->(other)
        WITH big_chunks, collect(o) AS other_chunks
        CREATE (hub:__Entity__:Concept {id: 'S1V_HUB'})
        FOREACH (c IN big_chunks + [other_chunks[0]] | CREATE (c)-[:HAS_ENTITY]->(hub))
        WITH big_chunks, other_chunks, hub
        UNWIND range(1, $n_entities) AS i
        CREATE (e:__Entity__:Concept {id: 'S1V_BE' + i})
        WITH big_chunks, other_chunks, hub, i, e, big_chunks[i % $n_chunks] AS c1, big_chunks[(i * 7) % $n_chunks] AS c2
        MERGE (c1)-[:HAS_ENTITY]->(e)
        MERGE (c2)-[:HAS_ENTITY]->(e)
        FOREACH (o IN CASE WHEN i % 2 = 0 THEN [other_chunks[i / 2 - 1]] ELSE [] END | CREATE (o)-[:HAS_ENTITY]->(e))
        FOREACH (_ IN CASE WHEN i <= $hub_out THEN [1] ELSE [] END | CREATE (hub)-[:S1V_REL]->(e))
        FOREACH (_ IN CASE WHEN 1 < i AND i <= $hub_in + 1 THEN [1] ELSE [] END | CREATE (e)-[:S1V_IN]->(hub))
        WITH collect(e) AS chain
        UNWIND range(0, size(chain) - 2) AS j
        WITH chain[j] AS a, chain[j + 1] AS b
        CREATE (a)-[:S1V_REL2]->(b)
        """, n_chunks=n_chunks, n_other=n_entities // 2, n_entities=n_entities, hub_out=hub_out, hub_in=hub_in).consume()


def test_b2_large_document_is_fast_and_follows_the_procedure(tx, tmp_path):
    """多くのチャンクに出るエンティティ（中心）を持つ大きな文書を消しても速い（点検の時点の版は同じ形で 34〜72 秒、
    修正前の問い合わせは 1.6〜2.8 秒だった）。消すものは設計メモの手順どおり。"""
    build_big_document(tx, 3000, 10000, 5000, 5000)
    st = load_structure(tx.run)
    del_chunks, del_entities, del_rels = expected_b2(st, ["S1V_BIG.pdf"])
    assert (len(del_chunks), len(del_entities)) == (3000, 5000)

    start = time.time()
    run_delete(tx, "S1V_BIG.pdf", "true", tmp_path)
    elapsed = time.time() - start
    assert elapsed < 15, f"大きな文書の削除に {elapsed:.1f} 秒かかった"

    alive_entities = {r["e"] for r in tx.run("MATCH (e) WHERE e.id STARTS WITH 'S1V_' RETURN elementId(e) AS e")}
    synthetic = {e for e in st["chunks_of"] if any(c.startswith("s1v-") for c in st["chunks_of"][e])}
    assert alive_entities == synthetic - del_entities
    alive_rels = {r["rel"] for r in tx.run("MATCH (a:__Entity__)-[r]->(b:__Entity__) WHERE a.id STARTS WITH 'S1V_' "
                                           "RETURN elementId(r) AS rel")}
    keep = {r["rel"] for r in st["entity_rels"] if (r["a_id"] or "").startswith("S1V_")
            and r["a"] not in del_entities and r["b"] not in del_entities and r["rel"] not in del_rels}
    assert alive_rels == keep
    # 中心 -> 偶数の i のうち、他の文書のチャンクでも一緒に出るのは S1V_BE2 だけ（中心は s1v-oth-1 から、S1V_BE2 も s1v-oth-1 から）
    assert sorted(r["t"] + ":" + r["other"] for r in tx.run(
        "MATCH (h:__Entity__ {id: 'S1V_HUB'})-[r]-(o:__Entity__) RETURN type(r) AS t, o.id AS other")) == [
        "S1V_IN:S1V_BE2", "S1V_REL:S1V_BE2"]


# ---------- (4) B9: 「最後の位置から再開」で選ぶチャンク ----------

def doc_chunks(tx, file_name):
    """文書のチャンク（位置の順）。own: 他の文書と共有していない。"""
    return tx.run("""MATCH (d:Document {fileName: $f})<-[:PART_OF]-(c:Chunk)
        RETURN c.id AS id, c.position AS position, NOT EXISTS { (c)-[:PART_OF]->(o:Document) WHERE o <> d } AS own,
               c.embedding IS NOT NULL AS has_embedding, EXISTS { (c)-[:HAS_ENTITY]->() } AS has_entity
        ORDER BY c.position, c.id""", f=file_name).data()


def drop_embeddings(tx, ids):
    tx.run("MATCH (c:Chunk) WHERE c.id IN $ids SET c._s1_embedding = c.embedding, c.embedding = null", ids=list(ids)).consume()


def restore_embeddings(tx):
    tx.run("MATCH (c:Chunk) WHERE c._s1_embedding IS NOT NULL SET c.embedding = c._s1_embedding REMOVE c._s1_embedding").consume()


def resume_ids(tx, file_name):
    """本番の get_chunkId_chunkDoc_list（最後の位置から再開）が選ぶチャンクの id（処理する順）。"""
    _, chunk_list = get_chunkId_chunkDoc_list(TxGraph(tx), file_name, [], 512, 50, START_FROM_LAST_PROCESSED_POSITION, "")
    return [c["chunk_id"] for c in chunk_list]


def old_resume_ids(tx, file_name):
    """直す前の選び方（埋め込みの無い最初のチャンクの位置の値で、保存の順の一覧を切る）。不具合の再現に使う。"""
    chunks = tx.run(OLD_QUERY_TO_GET_CHUNKS, filename=file_name).data()
    start = TxGraph(tx).query(QUERY_TO_GET_LAST_PROCESSED_CHUNK_POSITION, {"filename": file_name})
    if start and start[0]["position"] < len(chunks):
        return [c["id"] for c in chunks[start[0]["position"] - 1:]]
    if start and start[0]["position"] == len(chunks):
        start = TxGraph(tx).query(QUERY_TO_GET_LAST_PROCESSED_CHUNK_WITHOUT_ENTITY, {"filename": file_name})
        return [c["id"] for c in chunks[start[0]["position"] - 1:]]
    raise LLMGraphBuilderException("All chunks are already processed")


def set_processed_chunk(tx, file_name, value):
    tx.run("MATCH (d:Document {fileName: $f}) SET d.processed_chunk = $v", f=file_name, v=value).consume()


def make_failed_batch(tx, file_name="TS3-5.pdf"):
    """2 バッチ（40 個）を処理した後、3 つ目のバッチ（位置 41〜60）の抽出で止まった形を作る。
    41〜60 は埋め込みがあり HAS_ENTITY が無い。61〜100 は埋め込みも HAS_ENTITY も無い。"""
    by_pos = {c["position"]: c["id"] for c in doc_chunks(tx, file_name)}
    set_processed_chunk(tx, file_name, 40)
    tx.run("MATCH (c:Chunk)-[h:HAS_ENTITY]->() WHERE c.id IN $ids DELETE h", ids=[by_pos[p] for p in range(41, 101)]).consume()
    drop_embeddings(tx, [by_pos[p] for p in range(61, 101)])
    return by_pos


def test_b9_chunk_list_is_in_position_order(tx):
    """再処理の一覧は位置の順（同じ位置は id の順）に返る（直す前は保存の順で、HS-VTR-T001E では 20, 19, 18, ... の順）。"""
    for file_name in ["HS-VTR-T001E.pdf", TARGET, "TS3-5.pdf"]:
        rows = TxGraph(tx).query(QUERY_TO_GET_CHUNKS, {"filename": file_name})
        assert [(r["position"], r["id"]) for r in rows] == sorted((r["position"], r["id"]) for r in rows)
        assert {r["id"] for r in rows} == {c["id"] for c in doc_chunks(tx, file_name)}


def test_b9_resume_includes_every_chunk_without_embedding(tx):
    """位置 p 以降のチャンクに埋め込みが無い形（p で止まった）で、それらをすべて選ぶ。共有のチャンクも含む。"""
    for file_name in [TARGET, "HS-VTR-T001E.pdf", "TS3-5.pdf"]:
        chunks = doc_chunks(tx, file_name)
        position = {c["id"]: (c["position"], c["id"]) for c in chunks}
        for p in sorted({c["position"] for c in chunks}):
            missing = {c["id"] for c in chunks if c["position"] >= p}
            drop_embeddings(tx, missing)
            got = resume_ids(tx, file_name)
            restore_embeddings(tx)
            assert missing <= set(got), (file_name, p, sorted(position[i] for i in missing - set(got)))
            assert [position[i] for i in got] == sorted(position[i] for i in got)


def test_b9_resume_single_chunk_without_embedding(tx):
    """埋め込みの無いチャンクが 1 つだけなら、そのチャンクだけを選ぶ（文書は処理済みの数が総数と同じ）。"""
    for chunk in doc_chunks(tx, TARGET):
        drop_embeddings(tx, [chunk["id"]])
        got = resume_ids(tx, TARGET)
        restore_embeddings(tx)
        assert got == [chunk["id"]], chunk


def test_b9_old_resume_failed_on_last_and_shared_positions(tx):
    """直す前は、最後の位置（自分のチャンクの位置 14 = 一覧の数）で IndexError になり、他の文書の位置（17）を持つ
    共有のチャンクでは「すべて処理済み」で止まった（不具合の再現）。直した後はそのチャンクを選ぶ。"""
    chunks = doc_chunks(tx, TARGET)
    last_own = max((c for c in chunks if c["own"]), key=lambda c: c["position"])
    shared_far = max((c for c in chunks if not c["own"]), key=lambda c: c["position"])
    assert (last_own["position"], len(chunks), shared_far["position"]) == (14, 14, 17)

    drop_embeddings(tx, [last_own["id"]])
    with pytest.raises(IndexError):
        old_resume_ids(tx, TARGET)
    assert resume_ids(tx, TARGET) == [last_own["id"]]
    restore_embeddings(tx)

    drop_embeddings(tx, [shared_far["id"]])
    with pytest.raises(LLMGraphBuilderException):
        old_resume_ids(tx, TARGET)
    assert resume_ids(tx, TARGET) == [shared_far["id"]]


def test_b9_resume_after_failed_batch(tx):
    """抽出が 3 つ目のバッチで止まった形: 41〜100 と、40 までのうち抽出で何も出なかったチャンク（HAS_ENTITY が無い）を選ぶ。
    直す前は埋め込みの無い最初のチャンク（61）の位置で 40 個に切るので、未処理の 60 個のうち 20 個以上が落ちた。"""
    chunks = doc_chunks(tx, "TS3-5.pdf")
    empty_before = {c["id"] for c in chunks if c["position"] <= 40 and not c["has_entity"]}
    by_pos = make_failed_batch(tx)
    unprocessed = {by_pos[p] for p in range(41, 101)}

    got = resume_ids(tx, "TS3-5.pdf")
    assert len(got) == len(set(got)) and set(got) == unprocessed | empty_before
    assert len(empty_before) == 4
    old = old_resume_ids(tx, "TS3-5.pdf")
    assert len(unprocessed - set(old)) >= 20


def test_b9_resume_after_reextraction_stopped(tx):
    """「最初から」の再抽出が 40 個で止まった形: どのチャンクにも前回の抽出の埋め込みと HAS_ENTITY が残っている。
    処理済みの数より後のチャンクを選ぶ（直す前は埋め込みだけを見るので「すべて処理済み」で止まった）。"""
    set_processed_chunk(tx, "TS3-5.pdf", 40)
    expected = {c["id"] for c in doc_chunks(tx, "TS3-5.pdf") if c["position"] > 40 or not c["has_entity"]}
    assert set(resume_ids(tx, "TS3-5.pdf")) == expected
    with pytest.raises(LLMGraphBuilderException):
        old_resume_ids(tx, "TS3-5.pdf")


def test_b9_all_processed_raises(tx):
    with pytest.raises(LLMGraphBuilderException, match="already processed"):
        resume_ids(tx, "HS-VTR-T001E.pdf")


class FakeProcessingChunks:
    """processing_chunks の代わり（LLM を呼ばない）。受け取ったチャンクを記録し、処理したことにする
    （埋め込みを戻し、仮のエンティティへ HAS_ENTITY を張る）。cancel_after 回目の後に文書を取り消しの状態にする。"""

    def __init__(self, tx, cancel_after=None):
        self.tx = tx
        self.cancel_after = cancel_after
        self.batches = []

    async def __call__(self, chunkId_chunkDoc_list, graph, credentials, file_name, model, allowedNodes, allowedRelationship,
                       chunks_to_combine, node_count, rel_count, additional_instructions, embedding_provider, embedding_model,
                       extraction_method=None, edc_options=None):  # edc_options は 11/8 の EDC 初期スキーマの指定で増えた引数
        ids = [c["chunk_id"] for c in chunkId_chunkDoc_list]
        self.batches.append(ids)
        self.tx.run("""MERGE (e:__Entity__:Concept {id: 'S1試験_処理済み'}) WITH e
                       UNWIND $ids AS id MATCH (c:Chunk {id: id})
                       SET c.embedding = coalesce(c.embedding, c._s1_embedding) REMOVE c._s1_embedding
                       MERGE (c)-[:HAS_ENTITY]->(e)""", ids=ids).consume()
        if self.cancel_after == len(self.batches):
            self.tx.run("MATCH (d:Document {fileName: $f}) SET d.is_cancelled = true", f=file_name).consume()
        return node_count, rel_count, {}, 0


def test_b9_processing_source_resumes_and_counts_processed_chunks(tx, monkeypatch):
    """processing_source を通す: 止まった所から再開し、途中で取り消して、もう一度再開する。処理済みの数（processed_chunk）は
    「一覧に入らなかった数 + 処理した数」で数え、2 回目の再開は 1 回目に処理した続きから始まり、最後に総数と同じになる。"""
    file_name = "TS3-5.pdf"
    position = {c["id"]: c["position"] for c in doc_chunks(tx, file_name)}
    make_failed_batch(tx)
    tx.run("MATCH (d:Document {fileName: $f}) SET d.status = 'Failed', d.is_cancelled = false", f=file_name).consume()
    monkeypatch.setattr(main_module, "create_graph_database_connection", lambda credentials: TxGraph(tx))
    monkeypatch.setenv("UPDATE_GRAPH_CHUNKS_PROCESSED", "20")
    monkeypatch.setenv("TRACK_USER_USAGE", "false")
    params = SourceScanExtractParams(model="gemma4", file_name=file_name, retry_condition=START_FROM_LAST_PROCESSED_POSITION,
                                     token_chunk_size=512, chunk_overlap=50, chunks_to_combine=1,
                                     embedding_provider=EMBEDDING_PROVIDER, embedding_model=EMBEDDING_MODEL)
    credentials = Neo4jCredentials(uri=TEST_URI, userName=TEST_USER, password=TEST_PASSWORD, database=TEST_DATABASE)

    def document():
        return tx.run("MATCH (d:Document {fileName: $f}) RETURN d.status AS status, d.processed_chunk AS processed, "
                      "d.total_chunks AS total", f=file_name).single().data()

    # 1 回目: 一覧は 64 個（41〜100 と、40 までで抽出に何も出なかった 15・17・35・38）。1 バッチの後に取り消す
    first = FakeProcessingChunks(tx, cancel_after=1)
    monkeypatch.setattr(main_module, "processing_chunks", first)
    _, response = asyncio.run(processing_source(credentials, params, []))
    assert response["status"] == "Cancelled"
    assert [[position[i] for i in batch] for batch in first.batches] == [[15, 17, 35, 38] + list(range(41, 57))]
    assert document() == {"status": "Cancelled", "processed": (100 - 64) + 20, "total": 100}

    # 2 回目: 1 回目に処理した続き（57〜100）だけを選び、最後に処理済みの数は総数と同じになる
    tx.run("MATCH (d:Document {fileName: $f}) SET d.is_cancelled = false", f=file_name).consume()
    second = FakeProcessingChunks(tx)
    monkeypatch.setattr(main_module, "processing_chunks", second)
    _, response = asyncio.run(processing_source(credentials, params, []))
    assert response["status"] == "Completed"
    assert [position[i] for batch in second.batches for i in batch] == list(range(57, 101))
    assert [len(batch) for batch in second.batches] == [20, 20, 4]
    assert document() == {"status": "Completed", "processed": 100, "total": 100}
    assert tx.run("MATCH (d:Document {fileName: $f})<-[:PART_OF]-(c:Chunk) WHERE c.embedding IS NULL RETURN count(c) AS n",
                  f=file_name).single()["n"] == 0


# ---------- (5) B4: 索引の作成を 2 回呼んでも索引が消えない ----------

INDEX_SNAPSHOT = """
SHOW INDEXES YIELD id, name, type, labelsOrTypes, properties, options, trackedSince
WHERE type IN ['VECTOR', 'FULLTEXT']
RETURN name, id, type, labelsOrTypes, properties, options, toString(trackedSince) AS trackedSince ORDER BY name
"""


def test_b4_creating_indexes_twice_keeps_existing_indexes(driver, caplog):
    credentials = Neo4jCredentials(uri=TEST_URI, userName=TEST_USER, password=TEST_PASSWORD, database=TEST_DATABASE)
    with driver.session(database=TEST_DATABASE) as session:
        before = session.run(INDEX_SNAPSHOT).data()
    names = {row["name"] for row in before}
    assert {"vector", "entity_vector", "community_vector", "keyword", "entities", "community_keyword"} <= names

    snapshots = []
    for _ in range(2):
        caplog.clear()
        with caplog.at_level("INFO"):
            create_vector_fulltext_indexes(credentials, EMBEDDING_PROVIDER, EMBEDDING_MODEL)
        # この関数は失敗をログに残して戻るので、6 つの索引の作成の文が通ったことをログで確かめる
        messages = [r.getMessage() for r in caplog.records]
        assert not [m for m in messages if m.startswith("Failed to create") or "error occurred" in m], messages
        assert sum("is ready" in m for m in messages) == 6, messages
        with driver.session(database=TEST_DATABASE) as session:
            snapshots.append(session.run(INDEX_SNAPSHOT).data())

    assert snapshots[0] == before, "索引の作成で既存の索引が作り直された（ID か作成時刻が変わった）"
    assert snapshots[1] == before


# ---------- (6) B8: HAS_ENTITY の作成 ----------

def has_entity_input(chunk_id, nodes):
    doc = GraphDocument(nodes=nodes, relationships=[], source=Document(page_content="", metadata={"combined_chunk_ids": [chunk_id]}))
    return [{"graph_doc": doc, "chunk_id": chunk_id}]


def b8_case(tx):
    chunk = tx.run("MATCH (d:Document {fileName: $f})<-[:PART_OF]-(c:Chunk) RETURN c.id AS id ORDER BY c.position LIMIT 1",
                   f=TARGET).single()["id"]
    # 保存した後に種類が変わったエンティティ（抽出の結果の種類はこのエンティティのラベルに無い）
    entity = tx.run("MATCH (e:__Entity__:Standard) WHERE NOT e:Concept AND NOT EXISTS { (:Chunk {id: $c})-[:HAS_ENTITY]->(e) } "
                    "RETURN e.id AS id, elementId(e) AS eid ORDER BY e.id LIMIT 1", c=chunk).single()
    nodes = [Node(id=entity["id"], type="Concept"), Node(id="S1試験_存在しない名前", type="Concept")]
    return chunk, entity, nodes


def test_b8_has_entity_does_not_create_nodes_without_entity_label(tx):
    chunk, entity, nodes = b8_case(tx)
    before = counts(tx.run)

    merge_relationship_between_chunk_and_entites(TxGraph(tx), has_entity_input(chunk, nodes))

    after = counts(tx.run)
    assert after["nodes"] == before["nodes"], "HAS_ENTITY の作成でノードが作られた"
    assert after["other_nodes"] == 0, "__Entity__ を持たないノードが作られた"
    assert tx.run("MATCH (n {id: 'S1試験_存在しない名前'}) RETURN count(n) AS n").single()["n"] == 0
    linked = tx.run("MATCH (:Chunk {id: $c})-[:HAS_ENTITY]->(e) WHERE elementId(e) = $eid RETURN count(e) AS n",
                    c=chunk, eid=entity["eid"]).single()["n"]
    assert linked == 1, "保存済みの __Entity__ に HAS_ENTITY が張られない"
    no_entity_targets = tx.run("MATCH (:Chunk)-[:HAS_ENTITY]->(n) WHERE NOT n:__Entity__ RETURN count(n) AS n").single()["n"]
    assert no_entity_targets == 0


def test_b8_old_query_created_nodes_without_entity_label(tx):
    """修正前は、種類のラベルと名前で探して無ければ作るので、__Entity__ を持たないノードが 2 つ作られた（不具合の再現）。"""
    chunk, _, nodes = b8_case(tx)
    batch = [{"chunk_id": chunk, "node_type": n.type, "node_id": n.id} for n in nodes]
    tx.run(OLD_HAS_ENTITY_QUERY, batch_data=batch).consume()
    assert counts(tx.run)["other_nodes"] == 2


# ---------- (7) B5: 検索の準備が今と同じ設定の Neo4jVector を返す（埋め込みは書かない） ----------

SEARCH_ATTRIBUTES = ["index_name", "node_label", "embedding_node_property", "retrieval_query", "search_type", "_index_type",
                     "embedding_dimension", "_distance_strategy", "support_metadata_filter", "neo4j_version_is_5_23_or_above",
                     "_is_enterprise", "text_node_property", "override_relevance_score_fn"]


def old_store(settings, retrieval_query, embedding, graph):
    """修正前の作り方（from_existing_graph）。写しは埋め込みが揃っているので、ここで書き込みは起きない。"""
    common = dict(embedding=embedding, index_name=settings["index_name"], retrieval_query=retrieval_query, graph=graph,
                  node_label=settings["node_label"], embedding_node_property=settings["embedding_node_property"],
                  text_node_properties=settings["text_node_properties"])
    if settings.get("keyword_index"):
        return Neo4jVector.from_existing_graph(search_type="hybrid", keyword_index_name=settings["keyword_index"], **common)
    return Neo4jVector.from_existing_graph(**common)


def attributes(store, hybrid):
    values = {name: getattr(store, name) for name in SEARCH_ATTRIBUTES}
    if hybrid:
        # 全文索引の名前は hybrid のときだけ検索に使う（vector のときは問い合わせに現れない）
        values["keyword_index_name"] = store.keyword_index_name
    return values


def missing_embeddings(run):
    return run("""
        CALL () { MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c) AS chunks }
        CALL () { MATCH (e:__Entity__) WHERE e.embedding IS NULL RETURN count(e) AS entities }
        RETURN chunks, entities""").single().data()


@pytest.mark.parametrize("mode", [m for m in CHAT_MODE_CONFIG_MAP])
def test_b5_vector_store_has_same_search_settings(driver, mode):
    with driver.session(database=TEST_DATABASE) as session:
        assert missing_embeddings(session.run) == {"chunks": 0, "entities": 0}
    credentials = Neo4jCredentials(uri=TEST_URI, userName=TEST_USER, password=TEST_PASSWORD, database=TEST_DATABASE)
    graph = create_graph_database_connection(credentials)
    settings = dict(CHAT_MODE_CONFIG_MAP[mode])
    hybrid = bool(settings.get("keyword_index"))
    embedding, _ = load_embedding_model(EMBEDDING_PROVIDER, EMBEDDING_MODEL)

    new = initialize_neo4j_vector(graph, settings, EMBEDDING_PROVIDER, EMBEDDING_MODEL)
    old = old_store(settings, settings["retrieval_query"], embedding, graph)
    assert attributes(new, hybrid) == attributes(old, hybrid)

    if settings["node_label"] == "Chunk":
        # リランクの候補を取る Neo4jVector（P1-a）も同じ設定になる
        new_candidates = build_candidate_db(new, settings, embedding, graph)
        old_candidates = old_store(settings, CHUNK_CANDIDATE_QUERY, embedding, graph)
        assert attributes(new_candidates, hybrid) == attributes(old_candidates, hybrid)

    with driver.session(database=TEST_DATABASE) as session:
        assert missing_embeddings(session.run) == {"chunks": 0, "entities": 0}


# ---------- (8) B5 の続き: 全文索引が無いときは、その全文索引だけを作る ----------

TEST_KEYWORD_INDEX = "s1_test_keyword"


def fulltext_index(session, name):
    row = session.run("SHOW FULLTEXT INDEXES YIELD id, name, state, labelsOrTypes, properties WHERE name = $name "
                      "RETURN id, state, labelsOrTypes, properties", name=name).single()
    return row.data() if row else None


def test_b5_missing_keyword_index_is_created_without_embeddings(driver):
    """全文索引（keyword）が無い DB でも hybrid のモードの検索の準備が通る（from_existing_index は全文索引が無いと失敗する）。
    写しの keyword 索引は消さず、別の名前の全文索引（Chunk の fileName）で確かめ、最後に消す。"""
    credentials = Neo4jCredentials(uri=TEST_URI, userName=TEST_USER, password=TEST_PASSWORD, database=TEST_DATABASE)
    graph = create_graph_database_connection(credentials)
    settings = dict(CHAT_MODE_CONFIG_MAP[CHAT_VECTOR_GRAPH_FULLTEXT_MODE])
    settings.update(keyword_index=TEST_KEYWORD_INDEX, text_node_properties=["fileName"])
    with driver.session(database=TEST_DATABASE) as session:
        session.run(f"DROP INDEX {TEST_KEYWORD_INDEX} IF EXISTS").consume()
        before = [row for row in session.run(INDEX_SNAPSHOT).data()]
    try:
        store = initialize_neo4j_vector(graph, settings, EMBEDDING_PROVIDER, EMBEDDING_MODEL)
        assert store.keyword_index_name == TEST_KEYWORD_INDEX
        with driver.session(database=TEST_DATABASE) as session:
            created = fulltext_index(session, TEST_KEYWORD_INDEX)
            assert {k: created[k] for k in ("state", "labelsOrTypes", "properties")} == {
                "state": "ONLINE", "labelsOrTypes": ["Chunk"], "properties": ["fileName"]}
            # 2 回目は作り直さない
            ensure_keyword_index(graph, TEST_KEYWORD_INDEX, "Chunk", ["fileName"])
            assert fulltext_index(session, TEST_KEYWORD_INDEX)["id"] == created["id"]
            # 他の索引は変えず、埋め込みも書かない
            after = [row for row in session.run(INDEX_SNAPSHOT).data() if row["name"] != TEST_KEYWORD_INDEX]
            assert after == before
            assert missing_embeddings(session.run) == {"chunks": 0, "entities": 0}
    finally:
        with driver.session(database=TEST_DATABASE) as session:
            session.run(f"DROP INDEX {TEST_KEYWORD_INDEX} IF EXISTS").consume()


def test_b5_existing_keyword_index_is_not_recreated(driver):
    """全文索引があるときは何もしない（ID と作成時刻が変わらない）。"""
    credentials = Neo4jCredentials(uri=TEST_URI, userName=TEST_USER, password=TEST_PASSWORD, database=TEST_DATABASE)
    graph = create_graph_database_connection(credentials)
    with driver.session(database=TEST_DATABASE) as session:
        before = session.run(INDEX_SNAPSHOT).data()
    initialize_neo4j_vector(graph, dict(CHAT_MODE_CONFIG_MAP[CHAT_VECTOR_GRAPH_FULLTEXT_MODE]), EMBEDDING_PROVIDER, EMBEDDING_MODEL)
    with driver.session(database=TEST_DATABASE) as session:
        assert session.run(INDEX_SNAPSHOT).data() == before


# ---------- (9) 埋め込みモデルの変更（score.change_embedding_model）: 索引を作り直せなかったときの応答 ----------

def call_change_embedding_model(monkeypatch, drop_create):
    import score
    monkeypatch.setattr(score, "change_user_embedding_model", lambda email, uri, provider, model: {
        "status": "Success", "change_index": True, "new_embedding_provider": provider, "new_embedding_model": model,
        "new_dimension": 1024})
    monkeypatch.setattr(score.graphDBdataAccess, "drop_create_vector_index", drop_create)
    credentials = Neo4jCredentials(uri=TEST_URI, userName=TEST_USER, password=TEST_PASSWORD, database=TEST_DATABASE)
    return asyncio.run(score.change_embedding_model(credentials=credentials, embedding_provider="vllm",
                                                    embedding_model="s1-test-model"))


def test_change_embedding_model_reports_that_the_index_was_not_recreated(monkeypatch):
    """利用者のモデルを変えた後で索引の作り直しに失敗したら、モデルは変わったことと索引が古いままのことを返す
    （索引の作り直しは差し替えて失敗させ、試験用 DB の索引には触れない）。"""
    def fail(self, isVectorIndexExist, embedding_provider, embedding_model):
        raise RuntimeError("S1 test: index failure")

    response = call_change_embedding_model(monkeypatch, fail)
    assert response["status"] == "Failed"
    assert "Embedding model was changed to vllm s1-test-model" in response["message"]
    assert "could not be re-created" in response["message"] and "S1 test: index failure" in response["error"]


def test_change_embedding_model_recreates_the_index(monkeypatch):
    calls = []
    response = call_change_embedding_model(monkeypatch, lambda self, *args: calls.append(args))
    assert response["status"] == "Success"
    assert response["message"].endswith("Vector index was dropped and recreated.")
    assert calls == [("true", "vllm", "s1-test-model")]

