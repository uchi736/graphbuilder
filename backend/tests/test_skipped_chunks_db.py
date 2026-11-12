"""抽出に失敗したチャンクの記録（T16）を processing_source に通す試験。

試験用 Neo4j（neo4j-gbdev）で、開いたトランザクションの中で流し、最後に取り消す（写しは変わらない）。
問い合わせは本物の Neo4jGraph を通す（接続だけをトランザクションへ差し替える）。本番の接続は sanitize=True で、
128 件以上のリストを結果から落とすので、その動きも本番と同じになる。
LLM は呼ばない。processing_chunks を差し替え、指定したチャンクで抽出が失敗したふりをする
（本物の抽出と同じく skipped_chunks.note を呼ぶだけで、そのチャンクの HAS_ENTITY には触れない）。

確かめること
  (1) 前のチャンクで最初から → 続きから → 続きから: 飛ばしたチャンクを記録し、続きからで拾い直すと減り、最後は 0 件
  (2) 画面の「エンティティを消して最初から」の後: 共有のエンティティへの HAS_ENTITY が残るチャンクでも、続きからで拾い直す
  (3) 記録が 128 件以上でも続きからが動く（previous_ids が sanitize に落とされない）
  (4) 続きからを途中で取り消すと、まだ処理していないチャンクの記録は残る
  (5) 例外で終わったバッチ: そのバッチで飛ばしたチャンクと、処理し直せなかった前回の記録が文書に残る
  (6) 今の文書のチャンクでない古い ID は、続きからの始めに捨てる
  (7) 全部のチャンクを処理し終えた文書の続きからは、記録したチャンクだけを選ぶ（抽出結果の無いチャンクは選ばない）
  (8) 記録したチャンクだけのバッチがまた全部失敗しても、文書は Completed のまま記録が残る
  (9) 続きからが止まっても、処理済みの数（processed_chunk）は続きからの前の値より下がらない
  (10) 失敗した文書の続きからで同じ原因（LLM が止まったまま）でまた全部失敗しても、Completed にならない

実行: 本番の backend を読み取り専用で載せた一時コンテナから、試験用 Neo4j に向けて流す（docs/graph-edit/DESIGN.md の 7 章と同じ）
    python -m pytest -q tests/test_skipped_chunks_db.py
"""
import asyncio
import contextlib
import inspect
import os

import pytest
from langchain_neo4j import Neo4jGraph
from neo4j import GraphDatabase

from src import main as main_module
from src import skipped_chunks
from src.entities.source_extract_params import SourceScanExtractParams
from src.entities.user_credential import Neo4jCredentials
from src.main import processing_source
from src.shared import common_fn
from src.shared.constants import DELETE_ENTITIES_AND_START_FROM_BEGINNING, START_FROM_LAST_PROCESSED_POSITION
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException

TEST_URI = os.environ.get("GBDEV_TEST_URI", "neo4j://database-gbdev:7687")
TEST_USER = "neo4j"
TEST_PASSWORD = os.environ.get("GBDEV_TEST_PASSWORD", "gbdev-test-only")
TEST_DATABASE = "neo4j"
FILE = "HS-VTR-T002F.pdf"  # 写しでチャンク 20、どのチャンクにも HAS_ENTITY がある。他の文書とチャンクを共有しない
LONG_FILE = "T16試験_長い文書.pdf"  # 試験の中だけで作る、チャンク 130 の文書
PRISTINE = {"documents": 7, "chunks": 184, "entities": 671}
CRED = Neo4jCredentials(uri=TEST_URI, userName=TEST_USER, password=TEST_PASSWORD, database=TEST_DATABASE)

if "gbdev" not in TEST_URI:
    raise RuntimeError(f"この試験は試験用 Neo4j（database-gbdev）でしか流さない: {TEST_URI}")


class TxSession:
    def __init__(self, tx):
        self.tx = tx

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, query, params=None, **kwargs):
        return list(self.tx.run(getattr(query, "text", query), params or {}))


class TxDriver:
    """Neo4jGraph._driver の代わり。問い合わせを開いたトランザクションへ流す（Neo4jGraph.query の本体と sanitize はそのまま通る）"""

    def __init__(self, tx):
        self.tx = tx
        self._closed = False

    def execute_query(self, query, database_=None, parameters_=None, **kwargs):
        return list(self.tx.run(getattr(query, "text", query), parameters_ or {})), None, None

    def session(self, **kwargs):
        return TxSession(self.tx)

    def close(self):
        pass


def tx_graph(tx):
    """本物の Neo4jGraph（接続だけ差し替え）。sanitize=True は create_graph_database_connection と同じ"""
    graph = Neo4jGraph.__new__(Neo4jGraph)
    graph._driver = TxDriver(tx)
    graph._database = TEST_DATABASE
    graph.timeout = None
    graph.sanitize = True
    return graph


class FakeProcessingChunks:
    """processing_chunks の代わり。fail のチャンクは抽出に失敗したことにし（note するだけ）、ほかは仮のエンティティへ HAS_ENTITY を張る。
    raise_on 番目のバッチでは、本物の「全部失敗」と同じく note してから例外を出す（error を渡せば note せずにその例外を出す）。
    cancel_after 番目のバッチの後で文書を取り消しの状態にする"""

    def __init__(self, tx, fail=(), raise_on=None, error=None, cancel_after=None):
        self.tx = tx
        self.fail = set(fail)
        self.raise_on = raise_on
        self.error = error
        self.cancel_after = cancel_after
        self.batches = []

    async def __call__(self, chunkId_chunkDoc_list, graph, credentials, file_name, model, allowedNodes, allowedRelationship,
                       chunks_to_combine, node_count, rel_count, additional_instructions, embedding_provider, embedding_model,
                       extraction_method=None, edc_options=None):  # edc_options は 11/8 の EDC 初期スキーマの指定で増えた引数
        ids = [c["chunk_id"] for c in chunkId_chunkDoc_list]
        self.batches.append(ids)
        if self.raise_on == len(self.batches):
            if self.error is not None:
                raise self.error
            skipped_chunks.note(ids)
            raise LLMGraphBuilderException("Error in getting graph from llm: Graph transformation failed for all chunks: timeout")
        bad = [i for i in ids if i in self.fail]
        good = [i for i in ids if i not in self.fail]
        skipped_chunks.note(bad)  # 本物では llm.get_graph_document_list が呼ぶ
        self.tx.run("""MERGE (e:__Entity__:Concept {id: 'T16試験'}) WITH e
                       UNWIND $ids AS id MATCH (c:Chunk {id: id}) MERGE (c)-[:HAS_ENTITY]->(e)""", ids=good).consume()
        if self.cancel_after == len(self.batches):
            self.tx.run("MATCH (d:Document {fileName: $f}) SET d.is_cancelled = true", f=file_name).consume()
        return node_count, rel_count, {}, 0


def counts(session):
    return session.run("CALL () { MATCH (d:Document) WHERE d.fileName IS NOT NULL RETURN count(d) AS documents } "
                       "CALL () { MATCH (c:Chunk) RETURN count(c) AS chunks } "
                       "CALL () { MATCH (e:__Entity__) RETURN count(e) AS entities } "
                       "RETURN documents, chunks, entities").single().data()


@pytest.fixture(scope="module")
def driver():
    drv = GraphDatabase.driver(TEST_URI, auth=(TEST_USER, TEST_PASSWORD))
    with drv.session(database=TEST_DATABASE) as session:
        if counts(session) != PRISTINE:
            pytest.fail(f"試験用 DB が写しの状態でない: {counts(session)}（restore_gbdev.sh で戻してから流す）")
    yield drv
    with drv.session(database=TEST_DATABASE) as session:
        assert counts(session) == PRISTINE
        left = session.run("MATCH (d:Document) WHERE d.skipped_chunks IS NOT NULL OR d.skipped_chunk_ids IS NOT NULL "
                           "RETURN count(d) AS n").single()["n"]
        assert left == 0, "試験の記録が写しに残った"
    drv.close()


@pytest.fixture
def tx(driver):
    """試験の書き込みを入れるトランザクション。試験の後で必ず取り消す"""
    session = driver.session(database=TEST_DATABASE)
    transaction = session.begin_transaction()
    yield transaction
    if not transaction.closed():
        transaction.rollback()
    session.close()


@pytest.fixture
def env(monkeypatch, tx):
    monkeypatch.setenv("TRACK_USER_USAGE", "false")
    monkeypatch.setenv("UPDATE_GRAPH_CHUNKS_PROCESSED", "8")
    graph = tx_graph(tx)
    monkeypatch.setattr(main_module, "create_graph_database_connection", lambda credentials: graph)
    return monkeypatch


def chunk_ids(tx, file_name=FILE):
    return [r["id"] for r in tx.run("MATCH (c:Chunk)-[:PART_OF]->(:Document {fileName: $f}) RETURN c.id AS id ORDER BY c.position",
                                    f=file_name)]


def document(tx, file_name=FILE):
    return tx.run("MATCH (d:Document {fileName: $f}) RETURN d.status AS status, d.skipped_chunks AS n, d.skipped_chunk_ids AS ids",
                  f=file_name).single().data()


def processed(tx, file_name=FILE):
    return tx.run("MATCH (d:Document {fileName: $f}) RETURN d.processed_chunk AS n", f=file_name).single()["n"]


def set_document(tx, file_name=FILE, **props):
    tx.run("MATCH (d:Document {fileName: $f}) SET d += $p", f=file_name, p=props).consume()


def params(retry_condition, file_name=FILE):
    return SourceScanExtractParams(model="gemma4", file_name=file_name, retry_condition=retry_condition,
                                   token_chunk_size=512, chunk_overlap=50, chunks_to_combine=1,
                                   embedding_provider="vllm", embedding_model="cl-nagoya/ruri-v3-310m")


def run(fake, env, retry_condition, file_name=FILE):
    env.setattr(main_module, "processing_chunks", fake)
    return asyncio.run(processing_source(CRED, params(retry_condition, file_name), []))[1]


def test_production_connection_uses_sanitize():
    # この試験の前提: 本番の接続は sanitize=True（128 件以上のリストを結果から落とす）
    assert "sanitize=True" in inspect.getsource(common_fn.create_graph_database_connection)


# ---------- (1) 記録して、続きからで拾い直す ----------

def test_record_then_resume_until_zero(tx, env):
    ids = chunk_ids(tx)
    assert len(ids) == 20, "写しの状態が違う"
    a, b = ids[2], ids[11]  # 1 つ目と 2 つ目のバッチに 1 件ずつ
    set_document(tx, status="Failed", is_cancelled=False, skipped_chunks=5, skipped_chunk_ids=["前回の記録"])

    first = FakeProcessingChunks(tx, fail=[a, b])
    response = run(first, env, DELETE_ENTITIES_AND_START_FROM_BEGINNING)  # 前のチャンクで最初から。前回の記録は数え直す
    assert [len(x) for x in first.batches] == [8, 8, 4]
    assert response["status"] == "Completed" and response["skipped_chunks"] == 2
    assert document(tx) == {"status": "Completed", "n": 2, "ids": [a, b]}

    second = FakeProcessingChunks(tx, fail=[b])  # 続きから。記録した 2 件を拾い直し、1 件がまた失敗する
    response = run(second, env, START_FROM_LAST_PROCESSED_POSITION)
    assert second.batches == [[a, b]]
    assert response["skipped_chunks"] == 1
    assert document(tx) == {"status": "Completed", "n": 1, "ids": [b]}

    third = FakeProcessingChunks(tx)  # 続きから。残りの 1 件が通ると 0 件になる
    response = run(third, env, START_FROM_LAST_PROCESSED_POSITION)
    assert third.batches == [[b]]
    assert response["skipped_chunks"] == 0
    assert document(tx) == {"status": "Completed", "n": 0, "ids": []}


# ---------- (2) 「エンティティを消して最初から」の後でも拾い直す ----------

def test_resume_after_delete_entities_picks_chunk_with_shared_entity(tx, env):
    graph = tx_graph(tx)
    env.setattr(main_module, "hold_lease", lambda *args, **kwargs: contextlib.nullcontext())
    set_document(tx, is_cancelled=False)
    main_module.set_status_retry(graph, FILE, DELETE_ENTITIES_AND_START_FROM_BEGINNING)  # 画面の再処理と同じ削除
    ids = chunk_ids(tx)
    kept = [r["id"] for r in tx.run("UNWIND $ids AS id MATCH (c:Chunk {id: id}) WHERE EXISTS { (c)-[:HAS_ENTITY]->() } RETURN id",
                                    ids=ids)]
    assert kept, "削除の後に HAS_ENTITY の残るチャンクが無い（筋書きが成り立たない）"
    s = kept[0]

    first = FakeProcessingChunks(tx, fail=[s])  # 失敗しても、共有のエンティティへの HAS_ENTITY は残る
    response = run(first, env, DELETE_ENTITIES_AND_START_FROM_BEGINNING)
    assert response["skipped_chunks"] == 1 and document(tx)["ids"] == [s]

    set_document(tx, status="Ready to Reprocess", retry_condition=START_FROM_LAST_PROCESSED_POSITION)
    second = FakeProcessingChunks(tx)
    response = run(second, env, START_FROM_LAST_PROCESSED_POSITION)
    assert second.batches == [[s]]
    assert response["status"] == "Completed" and response["skipped_chunks"] == 0
    assert document(tx) == {"status": "Completed", "n": 0, "ids": []}


# ---------- (3) 記録が 128 件以上 ----------

def make_long_document(tx, n=130):
    tx.run("""CREATE (d:Document {fileName: $f, status: 'Completed', is_cancelled: false, processed_chunk: $n, total_chunks: $n,
                                  fileSource: 'local file', model: 'gemma4', nodeCount: 0, relationshipCount: 0})
              WITH d UNWIND range(1, $n) AS i
              CREATE (c:Chunk {id: 'T16試験-' + toString(i), text: '本文 ' + toString(i), position: i, fileName: $f,
                               embedding: [0.1, 0.2]})-[:PART_OF]->(d)""", f=LONG_FILE, n=n).consume()
    return chunk_ids(tx, LONG_FILE)


@pytest.mark.parametrize("n", [127, 128, 130])
def test_previous_ids_reads_long_lists_through_sanitize(tx, n):
    ids = make_long_document(tx, 130)[:n]
    set_document(tx, LONG_FILE, skipped_chunk_ids=ids, skipped_chunks=len(ids))
    assert skipped_chunks.previous_ids(tx_graph(tx), LONG_FILE) == ids


def test_resume_with_130_records(tx, env):
    ids = make_long_document(tx, 130)
    set_document(tx, LONG_FILE, skipped_chunk_ids=ids, skipped_chunks=len(ids))
    env.setenv("UPDATE_GRAPH_CHUNKS_PROCESSED", "50")
    fake = FakeProcessingChunks(tx, fail=[ids[99]])
    response = run(fake, env, START_FROM_LAST_PROCESSED_POSITION, LONG_FILE)
    assert [len(x) for x in fake.batches] == [50, 50, 30]
    assert response["status"] == "Completed" and response["skipped_chunks"] == 1
    assert document(tx, LONG_FILE) == {"status": "Completed", "n": 1, "ids": [ids[99]]}


# ---------- (4) 続きからを途中で取り消す ----------

def test_cancelled_resume_keeps_unprocessed_records(tx, env):
    ids = chunk_ids(tx)
    f1, f2 = ids[2], ids[11]
    set_document(tx, status="Completed", is_cancelled=False, processed_chunk=20, skipped_chunk_ids=[f1, f2], skipped_chunks=2)
    env.setenv("UPDATE_GRAPH_CHUNKS_PROCESSED", "1")
    fake = FakeProcessingChunks(tx, cancel_after=1)
    response = run(fake, env, START_FROM_LAST_PROCESSED_POSITION)
    assert fake.batches == [[f1]]
    assert response["status"] == "Cancelled" and response["skipped_chunks"] == 1
    assert document(tx) == {"status": "Cancelled", "n": 1, "ids": [f2]}
    assert processed(tx) == 20  # 続きからの前の値（全部処理済み）に戻す
    fake2 = FakeProcessingChunks(tx)  # 次の続きからは残りの記録だけを選ぶ
    set_document(tx, is_cancelled=False)
    response = run(fake2, env, START_FROM_LAST_PROCESSED_POSITION)
    assert fake2.batches == [[f2]] and response["skipped_chunks"] == 0


# ---------- (5) 例外で終わったバッチ ----------

def test_failed_batch_is_recorded_when_extraction_raises(tx, env):
    ids = chunk_ids(tx)
    set_document(tx, status="Failed", is_cancelled=False)
    fake = FakeProcessingChunks(tx, fail=[ids[2]], raise_on=2)  # 2 つ目のバッチは全部失敗して例外
    with pytest.raises(LLMGraphBuilderException):
        run(fake, env, DELETE_ENTITIES_AND_START_FROM_BEGINNING)
    assert document(tx)["ids"] == [ids[2]] + ids[8:16]


def test_resume_that_raises_keeps_previous_records(tx, env):
    ids = chunk_ids(tx)
    f1, f2 = ids[2], ids[11]
    set_document(tx, status="Failed", is_cancelled=False, processed_chunk=20, skipped_chunk_ids=[f1, f2], skipped_chunks=2)
    fake = FakeProcessingChunks(tx, raise_on=1, error=RuntimeError("保存の途中で DB が落ちた"))  # 抽出の外の例外（note しない）
    with pytest.raises(RuntimeError):
        run(fake, env, START_FROM_LAST_PROCESSED_POSITION)
    assert fake.batches == [[f1, f2]]
    assert document(tx)["ids"] == [f1, f2]
    assert processed(tx) == 20  # 下げたまま残さない


# ---------- (6) 古い ID ----------

def test_stale_ids_are_dropped_on_resume(tx, env):
    ids = chunk_ids(tx)
    f1 = ids[5]
    set_document(tx, status="Completed", is_cancelled=False, processed_chunk=20, skipped_chunk_ids=["分割し直す前の古いID", f1],
                 skipped_chunks=2)
    fake = FakeProcessingChunks(tx)
    response = run(fake, env, START_FROM_LAST_PROCESSED_POSITION)
    assert fake.batches == [[f1]]
    assert response["skipped_chunks"] == 0 and document(tx)["ids"] == []



# ---------- (7) 全部のチャンクを処理し終えた文書の続きから ----------

def test_completed_resume_selects_only_recorded_chunks(tx, env):
    ids = chunk_ids(tx)
    f1, empty = ids[3], ids[7]
    tx.run("MATCH (c:Chunk {id: $id})-[r:HAS_ENTITY]->() DELETE r", id=empty).consume()  # 抽出結果の無いチャンク（記録には無い）
    set_document(tx, status="Completed", is_cancelled=False, processed_chunk=20, skipped_chunk_ids=[f1], skipped_chunks=1)
    fake = FakeProcessingChunks(tx)
    response = run(fake, env, START_FROM_LAST_PROCESSED_POSITION)
    assert fake.batches == [[f1]]
    assert response["status"] == "Completed" and response["skipped_chunks"] == 0
    assert processed(tx) == 20


def test_failed_document_resume_still_picks_unprocessed_and_empty_chunks(tx, env):
    ids = chunk_ids(tx)
    f1, empty = ids[3], ids[5]
    tx.run("MATCH (c:Chunk {id: $id})-[r:HAS_ENTITY]->() DELETE r", id=empty).consume()
    set_document(tx, status="Failed", is_cancelled=False, processed_chunk=16, skipped_chunk_ids=[f1], skipped_chunks=1)
    fake = FakeProcessingChunks(tx)
    response = run(fake, env, START_FROM_LAST_PROCESSED_POSITION)
    assert [i for b in fake.batches for i in b] == [f1, empty] + ids[16:]  # 位置の順
    assert response["status"] == "Completed" and response["skipped_chunks"] == 0
    assert processed(tx) == 20


# ---------- (8) 記録したチャンクだけのバッチがまた全部失敗する ----------

def test_refailed_recorded_chunks_keep_document_completed(tx, env):
    ids = chunk_ids(tx)
    f1, f2 = ids[2], ids[11]
    set_document(tx, status="Completed", is_cancelled=False, processed_chunk=20, skipped_chunk_ids=[f1, f2], skipped_chunks=2)
    fake = FakeProcessingChunks(tx, raise_on=1)  # 本物の「全部失敗」と同じく、全部を記録してから例外
    response = run(fake, env, START_FROM_LAST_PROCESSED_POSITION)
    assert fake.batches == [[f1, f2]]
    assert response["status"] == "Completed" and response["skipped_chunks"] == 2
    assert document(tx) == {"status": "Completed", "n": 2, "ids": [f1, f2]}
    assert processed(tx) == 20


def test_refailed_batch_mixed_with_other_chunks_still_raises(tx, env):
    ids = chunk_ids(tx)
    f1, empty = ids[2], ids[4]
    tx.run("MATCH (c:Chunk {id: $id})-[r:HAS_ENTITY]->() DELETE r", id=empty).consume()
    set_document(tx, status="Failed", is_cancelled=False, processed_chunk=19, skipped_chunk_ids=[f1], skipped_chunks=1)
    fake = FakeProcessingChunks(tx, raise_on=1)  # 記録の無いチャンクも混ざったバッチの全部失敗は、これまでどおり例外
    with pytest.raises(LLMGraphBuilderException):
        run(fake, env, START_FROM_LAST_PROCESSED_POSITION)
    assert fake.batches == [[f1, empty, ids[19]]]
    assert document(tx)["ids"] == [f1, empty, ids[19]]
    assert processed(tx) == 19  # 続きからの前の値より下げない


# ---------- (10) 失敗した文書に、LLM が止まったまま続きからを掛ける ----------

def test_resume_of_failed_document_during_outage_stays_failed(tx, env):
    ids = chunk_ids(tx)
    set_document(tx, status="Failed", is_cancelled=False)
    first = FakeProcessingChunks(tx, raise_on=1)  # 1 回目: 最初のバッチが全部失敗（本物と同じく全部を記録してから例外）
    with pytest.raises(LLMGraphBuilderException):
        run(first, env, DELETE_ENTITIES_AND_START_FROM_BEGINNING)
    set_document(tx, status="Failed")  # 本物では score.py の例外処理（update_exception_db）が付ける
    assert first.batches == [ids[:8]] and processed(tx) == 0
    for _ in range(2):  # 2 回目と 3 回目: 止まったまま続きから。記録したチャンクだけで Completed にしない
        again = FakeProcessingChunks(tx, raise_on=1)
        with pytest.raises(LLMGraphBuilderException):
            run(again, env, START_FROM_LAST_PROCESSED_POSITION)
        set_document(tx, status="Failed")
        assert again.batches == [ids[:8]]  # 1 回で呼ぶのは最初のバッチだけ
        assert processed(tx) == 0 and document(tx)["ids"] == ids[:8]
    recovered = FakeProcessingChunks(tx)  # LLM が戻れば続きからで全部抽出し、記録は 0 件になる
    response = run(recovered, env, START_FROM_LAST_PROCESSED_POSITION)
    assert [len(b) for b in recovered.batches] == [8, 8, 4]
    assert response["status"] == "Completed" and response["skipped_chunks"] == 0 and processed(tx) == 20
