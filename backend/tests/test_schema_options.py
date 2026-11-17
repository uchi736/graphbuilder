# -*- coding: utf-8 -*-
"""グラフスキーマ設定欄の選択肢の件数（POST /schema_options）の単体試験。Neo4j には接続しない（トランザクションと接続を差し替える）。
実行: cd backend && python3 -m pytest tests/test_schema_options.py -q"""
import asyncio
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src import schema_options as S  # noqa: E402
from src.entities.user_credential import Neo4jCredentials  # noqa: E402

LABELS = ["Concept", "Equipment", "Chunk", "Document", "__Entity__", "Session", "bad-label", "Standard"]
REL_TYPES = ["USES", "HAS_ENTITY", "PART_OF", "REFERS_TO", "lower_case", "APPLIES_TO"]
NODE_COUNTS = {"Concept": 127, "Equipment": 45, "Standard": 12}
REL_COUNTS = {"USES": 60, "REFERS_TO": 122, "APPLIES_TO": 50}


class Result:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def single(self):
        assert len(self.rows) == 1
        return self.rows[0]


class FakeTx:
    """tx.run の照会文に応じて行を返す。照会文を記録する。"""

    def __init__(self):
        self.queries = []

    def run(self, query, **params):
        self.queries.append(query)
        if query.startswith("CALL db.labels()"):
            return Result([{"label": label} for label in LABELS])
        if query.startswith("CALL db.relationshipTypes()"):
            return Result([{"t": t} for t in REL_TYPES])
        node = re.fullmatch(r"MATCH \(n:`(.+)`\) RETURN count\(n\) AS c", query)
        if node:
            return Result([{"c": NODE_COUNTS.get(node.group(1), 0)}])
        rel = re.fullmatch(r"MATCH \(\)-\[r:`(.+)`\]->\(\) RETURN count\(r\) AS c", query)
        if rel:
            return Result([{"c": REL_COUNTS.get(rel.group(1), 0)}])
        raise AssertionError(f"想定外の照会: {query}")


class FakeSession:
    def __init__(self, owner, database):
        self.owner, self.database = owner, database

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_read(self, fn):
        self.owner.read_database = self.database
        tx = FakeTx()
        self.owner.tx = tx
        return fn(tx)

    def execute_write(self, fn):
        raise AssertionError("書き込みのトランザクションを使用した")


class FakeDriver:
    def __init__(self):
        self.closed = False
        self.read_database = None
        self.tx = None

    def verify_connectivity(self):
        pass

    def session(self, database=None, **kwargs):
        return FakeSession(self, database)

    def close(self):
        self.closed = True


class FakeGraph:
    def __init__(self, database="neo4j"):
        self._driver = FakeDriver()
        self._database = database


def test_count_filters_like_vocabulary_and_sorts_by_name():
    tx = FakeTx()
    data = S.count_schema_options(tx)
    # 予約ラベル（Chunk・Session）、__Entity__、Document、名称の形が不正なラベルは除外する
    assert data["labels"] == [{"name": "Concept", "count": 127}, {"name": "Equipment", "count": 45},
                              {"name": "Standard", "count": 12}]
    # 予約リレーションタイプ（HAS_ENTITY・PART_OF など）と、名称の形が不正なリレーションタイプは除外する
    names = [d["name"] for d in data["relationship_types"]]
    assert names == sorted(names) and "HAS_ENTITY" not in names and "PART_OF" not in names and "lower_case" not in names
    assert {"name": "USES", "count": 60} in data["relationship_types"]
    assert {"name": "APPLIES_TO", "count": 50} in data["relationship_types"]
    # 除外した名称は件数を照会しない
    assert not any("Chunk" in q or "HAS_ENTITY" in q or "bad-label" in q for q in tx.queries)


def test_same_names_as_vocabulary():
    # 名称の一覧は POST /graph_edit/vocabulary と一致する（画面は vocabulary の名称に件数を付与する）
    from src.graph_edit import read
    vocabulary = read.vocabulary(FakeGraph())
    data = S.count_schema_options(FakeTx())
    assert [d["name"] for d in data["labels"]] == vocabulary["labels"]
    assert [d["name"] for d in data["relationship_types"]] == vocabulary["relationship_types"]


def test_quote_escapes_backticks():
    assert S._quote("Concept") == "`Concept`"
    assert S._quote("a`b") == "`a``b`"


def test_quote_rejects_backslash():
    # Neo4j は識別子の中の \u0060 をバッククォートとして解釈するため、「\」を含む名称は引用しない
    for name in ("a\\u0060) DETACH DELETE n //", "Concept\\", "\\"):
        try:
            S._quote(name)
            raise AssertionError(f"例外にならなかった: {name!r}")
        except ValueError:
            pass


def test_name_rule_uses_fullmatch():
    # 規則の末尾の $ は末尾の改行を許すため、名称全体で判定する（末尾に改行のある名称は対象外）
    assert S.usable_label("Concept") and S.usable_type("USES")
    assert not S.usable_label("Concept\n") and not S.usable_type("USES\n")
    assert not S.usable_label("a\\u0060b") and not S.usable_type("A\\u0060B")
    # 照会する名称は規則で絞るため、「\」を含む名称が _quote に渡ることはない
    tx = FakeTx()
    S.count_schema_options(tx)
    assert not any("\\" in q for q in tx.queries)


def _fake_neo4j_driver(monkeypatch, driver):
    """neo4j.GraphDatabase.driver を差し替え、渡された (uri, auth) を記録する。"""
    import neo4j
    seen = {}

    def fake(uri, auth=None):
        seen["uri"], seen["auth"] = uri, auth
        return driver
    monkeypatch.setattr(neo4j.GraphDatabase, "driver", fake)
    return seen


def test_collect_passes_credentials_and_closes_driver(monkeypatch):
    # 空の資格情報は環境変数 NEO4J_* で補う（資格情報そのものは書き換えない）。読み取りのトランザクションで集計し、接続を閉じる
    driver = FakeDriver()
    seen = _fake_neo4j_driver(monkeypatch, driver)
    monkeypatch.setenv("NEO4J_URI", "neo4j://env-host:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "env-user")
    monkeypatch.setenv("NEO4J_PASSWORD", "env-pass")
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    credentials = Neo4jCredentials()
    data = S.collect_schema_options(credentials)
    assert seen == {"uri": "neo4j://env-host:7687", "auth": ("env-user", "env-pass")}
    assert (credentials.uri, credentials.userName, credentials.password, credentials.database) == (None, None, None, None)
    assert driver.read_database == "neo4j" and driver.closed is True
    assert [d["name"] for d in data["labels"]] == ["Concept", "Equipment", "Standard"]


def test_collect_closes_driver_on_error(monkeypatch):
    driver = FakeDriver()

    def failing_session(database=None, **kwargs):
        raise RuntimeError("session failed")
    driver.session = failing_session
    _fake_neo4j_driver(monkeypatch, driver)
    try:
        S.collect_schema_options(Neo4jCredentials(uri="neo4j://x:7687", userName="u", password="p"))
        raise AssertionError("例外にならなかった")
    except RuntimeError:
        pass
    assert driver.closed is True


def test_route_returns_failed_instead_of_raising():
    orig = S.collect_schema_options

    def boom(credentials):
        raise RuntimeError("Unable to connect")
    S.collect_schema_options = boom
    try:
        r = asyncio.run(S.schema_options(credentials=Neo4jCredentials()))
    finally:
        S.collect_schema_options = orig
    assert r["status"] == "Failed" and r["message"] == "グラフスキーマの選択肢の件数を取得できませんでした"
    assert r["error"] == "Unable to connect"


def test_route_registered_and_accepts_empty_form():
    # 画面は接続を backend の設定に任せるとき資格情報を送らない。フォームが空でも受け付ける
    app = FastAPI()
    app.include_router(S.router)
    orig = S.collect_schema_options
    received = {}

    def fake(credentials):
        received["credentials"] = credentials
        return {"labels": [{"name": "Concept", "count": 1}], "relationship_types": []}
    S.collect_schema_options = fake
    try:
        response = TestClient(app).post("/schema_options", data={})
    finally:
        S.collect_schema_options = orig
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Success" and body["data"]["labels"] == [{"name": "Concept", "count": 1}]
    assert received["credentials"].userName is None and received["credentials"].password is None
