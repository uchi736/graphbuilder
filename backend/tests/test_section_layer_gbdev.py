# -*- coding: utf-8 -*-
"""章・節の層の後処理（M1〜M4）と、参照追跡の照会（M3・M5・M6）を、試験用 Neo4j（neo4j-gbdev）の小さな 2 文書の仮グラフで流す試験。

試験用の文書・チャンク・文書番号・節は、fileName・id の接頭辞 night1109- で区別し、試験の最後に消す。
build_section_layer はグラフ全体を作り直すので、ここでは読む照会の結果を night1109- の文書に絞り、全体を消す 2 つの照会を
night1109- の節に限る形に置き換えた代役（ScopedGraph）を通して流す（他の試験データの節や抽出のエンティティには触れない）。

確かめること:
- M1 章の番号（「上記2項」）は、LLM が選んだ 1 章のチャンクではなく 2 章の節へ付く（via = number）
- M2 一文の文書番号の直後の番号（「HS-VTR-T902の3-5項」）は、LLM が選んだ文書ではなく、文書番号の文書の 3-5 へ付く（via = standard）
- M3 文書層の一文の節番号から、参照元のチャンクが参照先の文書の節へ付く（LLM のリンクが無いものも）
- M4 本文の先頭が「### 3-5 潤滑油供給条件」のチャンクが 3-5 の主所属になる
- 別表1 の節が無いときは LLM の結び先の節に、解けなかった語（unresolved_locator）を残す。同じ参照の結び先 2 つは、既定（まとめ無効）では
  従来どおりチャンクごとの主所属の節へ付く
- 2 回流しても節・所属・参照の線が同じ（冪等）
- 検索側の照会: 章・節の参照は主所属のチャンクを先に、表示名は「3-5 潤滑油供給条件」。文書層の参照は節（sections）を返す。
  表示名の式は、Markdown の見出しから作った節（題名が番号を含む）と、題名が数字で始まる目録の節でも正しい
実行: GBDEV_TEST_PASSWORD を渡して backend コンテナで流す（渡さなければ飛ばす）
  docker exec -e GBDEV_TEST_PASSWORD=... -w /code backend python -m pytest -q -p no:cacheprovider tests/test_section_layer_gbdev.py
"""
import os

import pytest
from neo4j import GraphDatabase

from src import rerank_retriever as rr
from src import section_layer as SL
from src.entities.user_credential import Neo4jCredentials
from src.shared.common_fn import create_graph_database_connection

TEST_URI = os.environ.get("GBDEV_TEST_URI", "neo4j://database-gbdev:7687")
TEST_PASSWORD = os.environ.get("GBDEV_TEST_PASSWORD")
TEST_DATABASE = "neo4j"
PREFIX = "night1109-"
A, B = PREFIX + "A.pdf", PREFIX + "B.pdf"
STANDARDS = {"HS-VTR-T901": (A, "A"), "HS-VTR-T902": (B, "B")}

if "gbdev" not in TEST_URI:
    raise RuntimeError(f"この試験は試験用 Neo4j（database-gbdev）でしか流さない: {TEST_URI}")
pytestmark = pytest.mark.skipif(not TEST_PASSWORD, reason="GBDEV_TEST_PASSWORD が無い（試験用 Neo4j の試験は飛ばす）")

Q_CHAP = "検査部門は上記2項の試験結果に基づき管理する。"
Q_STD = "性能計測時の運転条件は、HS-VTR-T902の3-5項に依る。"
Q_STD_ONLY = "潤滑油温度はHS-VTR-T902の3-6項による。"
CHUNKS = [
    # (id の後ろ, 文書, 位置, 見出しの経路, 本文)
    ("a1", A, 1, "A > 1. 適用", "## 1. 適用\nこの基準は試験用の文書である。試験方法は別表2による。\n\n## 2. 試験要領\n組立部門は次により運転を行う。"),
    ("a2", A, 2, "A > 2. 試験要領", "### 2-1 第1台目\n計測は別表1による。\n### 2-2 第2台目\n上記2-1と同じとする。"),
    ("a3", A, 3, "A > 3. 管理", "## 3. 管理\n" + Q_CHAP + Q_STD),
    ("a4", A, 4, "A > 別表2 試験方法", "# 別表2 試験方法\n試験方法を示す。潤滑油はHS-VTR-T902による。" + Q_STD_ONLY),
    ("b1", B, 1, "B > 1. 適用", "## 1. 適用\nこの基準は判定基準である。\n## 2. 確認項目\n確認を行う。\n## 3. 判定基準\n判定は次による。\n### 3-1 性能試験\n性能を確かめる。"),
    ("b2", B, 2, "B > 3. 判定基準 > 3-4 外観検査", "### 3-4 外観検査\n漏れの無いこと。目視で確かめる。"),
    ("b3", B, 3, "B > 3. 判定基準 > 3-5 潤滑油供給条件", "### 3-5 潤滑油供給条件\n単独運転装置で試運転時の潤滑油供給条件を示す。"),
    ("b4", B, 4, "B > 3. 判定基準", "### 3-6 潤滑油温度\n130°C以下であること。"),
]
LINKS = [
    # (参照元, 結び先のチャンクか None（文書へ）, 結び先の文書, 位置の語, 一文)
    ("a3", "a1", A, "上記2項", Q_CHAP),
    ("a3", None, A, "3-5項", Q_STD),
    ("a2", "a2", A, "上記2-1", "上記2-1と同じとする。"),
    ("a2", "a4", A, "別表1", "計測は別表1による。"),
    ("a1", "a4", A, "別表2", "試験方法は別表2による。"),
    ("a4", "b3", B, "", "潤滑油はHS-VTR-T902による。"),
    ("a4", "b4", B, "", "潤滑油はHS-VTR-T902による。"),
]


def cid(short):
    return PREFIX + short


@pytest.fixture(scope="module")
def run():
    drv = GraphDatabase.driver(TEST_URI, auth=("neo4j", TEST_PASSWORD))

    def _run(query, **params):
        with drv.session(database=TEST_DATABASE) as session:
            return [record.data() for record in session.run(query, params)]
    yield _run
    drv.close()


class ScopedGraph:
    """build_section_layer を night1109- の文書だけに効かせる代役。読む照会の結果を絞り、全体を消す照会を night1109- の節に限る。"""

    def __init__(self, graph):
        self.graph = graph
        self.writes = []

    def query(self, cypher, params=None):
        params = dict(params or {})
        if "REMOVE e:Section SET e:SectionRef" in cypher:
            return []                           # 抽出のエンティティの付け替えは試験用の文書に関係しない（他の試験データに触れない）
        if "MATCH (c:Chunk)-[r:REFERS_TO]->(s:Section) WHERE s.method = $m DELETE r" in cypher:
            cypher = "MATCH (c:Chunk)-[r:REFERS_TO]->(s:Section) WHERE s.method = $m AND s.fileName STARTS WITH $prefix DELETE r"
            params["prefix"] = PREFIX
        elif "MATCH (s:Section) WHERE s.method = $m DETACH DELETE s" in cypher:
            cypher = "MATCH (s:Section) WHERE s.method = $m AND s.fileName STARTS WITH $prefix DETACH DELETE s"
            params["prefix"] = PREFIX
        elif any(w in cypher for w in ("DELETE ", "REMOVE ")):
            raise AssertionError("想定外の消す照会: " + cypher[:80])
        if any(w in cypher for w in ("CREATE ", "MERGE ", " SET ", "DELETE ")):
            self.writes.append(cypher)
            for row in params.get("rows") or []:
                ids = [v for k, v in row.items() if k in ("c", "fileName", "id", "s", "parent") and isinstance(v, str)]
                assert all(v.startswith(PREFIX) or len(v) == 40 for v in ids), row   # 試験の文書・チャンクと、節の id（sha1）だけ
        rows = self.graph.query(cypher, params)
        if "c.section_path AS section_path" in cypher:
            return [r for r in rows if (r.get("fileName") or "").startswith(PREFIX)]
        if "WHERE b:Chunk OR b:Document" in cypher:
            return [r for r in rows if (r.get("src") or "").startswith(PREFIX)]
        if "IS_VERSION_OF" in cypher or "r.quotes AS quotes" in cypher:
            return [r for r in rows if (r.get("f") or "").startswith(PREFIX)]
        return rows


def cleanup(run):
    run("MATCH (s:Section) WHERE s.fileName STARTS WITH $p DETACH DELETE s", p=PREFIX)
    run("MATCH (c:Chunk) WHERE c.id STARTS WITH $p DETACH DELETE c", p=PREFIX)
    run("MATCH (d:Document) WHERE d.fileName STARTS WITH $p DETACH DELETE d", p=PREFIX)
    run("MATCH (s:Standard) WHERE s.id IN $ids AND s.night1109 = true DETACH DELETE s", ids=list(STANDARDS))


@pytest.fixture(scope="module")
def world(run):
    taken = run("MATCH (e:__Entity__) WHERE e.id IN $ids AND coalesce(e.night1109, false) = false RETURN e.id AS id", ids=list(STANDARDS))
    if taken:
        pytest.skip("試験用の文書番号が試験用 Neo4j に既にある: %s" % taken)
    cleanup(run)
    try:
        run("UNWIND $docs AS d CREATE (:Document {fileName: d, status: 'Completed', night1109: true})", docs=[A, B])
        run("""UNWIND $rows AS r MATCH (d:Document {fileName: r.f})
               CREATE (c:Chunk {id: r.id, fileName: r.f, position: r.pos, section: r.section, text: r.text, night1109: true})-[:PART_OF]->(d)""",
            rows=[{"id": cid(s), "f": f, "pos": p, "section": sec, "text": "[%s]\n%s" % (sec, t)} for s, f, p, sec, t in CHUNKS])
        run("""UNWIND $rows AS r MATCH (d:Document {fileName: r.f})
               CREATE (s:__Entity__:Standard {id: r.id, method: 'rule', night1109: true})
               CREATE (d)-[:IS_VERSION_OF {rev: r.rev, method: 'test-night1109'}]->(s)""",
            rows=[{"id": k, "f": f, "rev": rev} for k, (f, rev) in STANDARDS.items()])
        run("""MATCH (d:Document {fileName: $a}) MATCH (s:Standard {id: 'HS-VTR-T902'})
               CREATE (d)-[:REFERS_TO {method: 'test-night1109', quotes: $quotes, scope: 'other_document'}]->(s)""", a=A, quotes=[Q_STD, Q_STD_ONLY])
        for src, tgt, f, loc, quote in LINKS:
            if tgt:
                run("MATCH (a:Chunk {id: $s}) MATCH (b:Chunk {id: $t}) CREATE (a)-[:REFERS_TO {locator: $loc, quote: $q, method: 'llm'}]->(b)",
                    s=cid(src), t=cid(tgt), loc=loc, q=quote)
            else:
                run("MATCH (a:Chunk {id: $s}) MATCH (d:Document {fileName: $f}) CREATE (a)-[:REFERS_TO {locator: $loc, quote: $q, method: 'llm'}]->(d)",
                    s=cid(src), f=f, loc=loc, q=quote)
        graph = create_graph_database_connection(Neo4jCredentials(uri=TEST_URI, userName="neo4j", password=TEST_PASSWORD, database=TEST_DATABASE))
        yield ScopedGraph(graph)
        graph._driver.close()
    finally:
        cleanup(run)


def snapshot(run):
    secs = run("MATCH (s:Section) WHERE s.fileName STARTS WITH $p RETURN s.id AS id, s.fileName AS f, s.number AS number, s.title AS title ORDER BY id", p=PREFIX)
    mem = run("""MATCH (c:Chunk)-[r:IN_SECTION]->(s:Section) WHERE c.id STARTS WITH $p
                 RETURN c.id AS c, s.id AS s, r.primary AS primary ORDER BY c, s""", p=PREFIX)
    refs = run("""MATCH (c:Chunk)-[r:REFERS_TO]->(s:Section) WHERE c.id STARTS WITH $p
                  RETURN c.id AS c, s.id AS s, r.via AS via, r.standard AS standard, r.unresolved_locator AS unresolved,
                         r.quote AS quote, r.source_method AS source_method, r.level AS level, r.method AS method ORDER BY c, s""", p=PREFIX)
    return secs, mem, refs


def test_section_layer_on_gbdev(world, run):
    result = SL.build_section_layer(world)
    summary = result["summary"]
    assert summary["chunks"] == len(CHUNKS)
    secs, mem, refs = snapshot(run)
    name = {s["id"]: (s["f"], s["number"] or s["title"]) for s in secs}

    # M4: 見出しの行で始まるチャンクの主所属
    primary = {m["c"]: name[m["s"]] for m in mem if m["primary"]}
    assert primary[cid("b3")] == (B, "3-5") and primary[cid("b4")] == (B, "3-6") and primary[cid("a3")] == (A, "3")
    assert sum(1 for m in mem if m["c"] == cid("b4") and m["primary"]) == 1

    got = {(r["c"], name[r["s"]], r["via"], r["standard"], r["unresolved"]) for r in refs}
    assert all(r["level"] == "section" and r["method"] == "rule" for r in refs)
    # M1: 上記2項 → A の 2 章
    assert (cid("a3"), (A, "2"), "number", None, None) in got
    # M2 と M3: 「HS-VTR-T902の3-5項」→ B の 3-5（LLM は文書 A を選んでいた）。E9 と E10 は 1 本にまとまる
    assert (cid("a3"), (B, "3-5"), "standard", "HS-VTR-T902", None) in got
    # M3: LLM のリンクが無い文書層の一文（3-6項）からも線を作る
    assert (cid("a4"), (B, "3-6"), "standard", "HS-VTR-T902", None) in got
    assert [r["source_method"] for r in refs if r["c"] == cid("a4") and name[r["s"]] == (B, "3-6")] == ["document_layer"]
    # M1: 付属の節（別表2）。別表1 は節が無いので LLM の結び先の節に、解けなかった語を残す
    assert (cid("a1"), (A, "別表2 試験方法"), "number", None, None) in got
    assert (cid("a2"), (A, "別表2 試験方法"), "chunk", None, "別表1") in got
    # 同じ参照の結び先 b3・b4 は、既定（まとめ無効）では従来どおり主所属の節（3-5 と 3-6）へ。3-6 は文書層の一文（3-6項）の線と
    # 同じなので、決め方の順（文書番号と番号 > LLM の結び先）で via = standard の 1 本になる
    a4 = sorted((x[1], x[2]) for x in got if x[0] == cid("a4"))
    assert a4 == [((B, "3-5"), "chunk"), ((B, "3-6"), "standard")], a4

    # 冪等: もう一度流しても同じ
    SL.build_section_layer(world)
    assert snapshot(run) == (secs, mem, refs)


def test_retriever_queries_on_gbdev(world, run):
    SL.build_section_layer(world)
    rows = run(rr.SECTION_REFERENCE_QUERY, ids=[cid("a3")], query_vector=None, per_section=rr.REFERENCE_SECTION_CANDIDATES)
    by_section = {}
    for r in rows:
        by_section.setdefault(r["section_title"], []).append(r["id"])
    # M6: 表示名は「番号 題名」。M5: 主所属のチャンク（b3）を先に、副所属（b4）を後に。
    # b4 が 3-5 の副所属になるのは、見出しの位置が「### 」の後ろにあり、3-5 の範囲が b4 の先頭の「### 」の数文字に重なるため
    # （主所属は M4 で 3-6 に直したが、副所属は残している。方針の外の課題として残した）
    assert by_section["3-5 潤滑油供給条件"] == [cid("b3"), cid("b4")], by_section
    assert "2 試験要領" in by_section
    doc_rows = run(rr.DOCUMENT_LAYER_REFERENCE_QUERY, ids=[cid("a3")])
    sec_35 = run("MATCH (s:Section {fileName: $f, number: '3-5'}) RETURN s.id AS id", f=B)[0]["id"]
    assert [(r["standard"], r["file_name"], r["sections"]) for r in doc_rows] == [("HS-VTR-T902", B, [sec_35])], doc_rows
    cands = run(rr.STANDARD_SECTION_CHUNKS_QUERY, sections=[sec_35], skip=[], query_vector=None, limit=rr.REFERENCE_DOCUMENT_CANDIDATES)
    assert [r["id"] for r in cands] == [cid("b3"), cid("b4")]
    # 文書層の参照で節の線が無いチャンク（a1 は一文が文書層の quotes に無い）は返らない。節の無い参照は sections が空
    assert run(rr.DOCUMENT_LAYER_REFERENCE_QUERY, ids=[cid("a1")]) == []


def test_section_label_expression_on_gbdev(run):
    """経由点の表示名の式（M6）を、節の代わりの値（map）で確かめる。ノードは作らない（読むだけの照会）。"""
    cases = [
        ({"number": "3-5", "title": "潤滑油供給条件", "source": "catalogue"}, "3-5 潤滑油供給条件"),
        ({"number": "3", "title": "3台目以降の試験", "source": "catalogue"}, "3 3台目以降の試験"),   # 題名が数字で始まっても番号を消さない
        ({"number": "2", "title": "(2) 計測", "source": None}, "(2) 計測"),                         # Markdown の見出しの節は題名のまま
        ({"number": "3-1", "title": "3-1 .サージ誤差計算", "source": None}, "3-1 .サージ誤差計算"),
        ({"number": None, "title": "別表2 過給機性能試験", "source": "catalogue"}, "別表2 過給機性能試験"),
        ({"number": "2-1", "title": "2-1", "source": "catalogue"}, "2-1"),                           # 題名が取れず番号で代えた節
    ]
    for value, want in cases:
        got = run("WITH $s AS s RETURN " + rr._SECTION_LABEL + " AS label", s=value)[0]["label"]
        assert got == want, (value, got)
