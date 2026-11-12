"""規則で作る層（文書層・章節の層）とグラフの手動修正の方針の試験（本番だけの項目。docs/graph-edit/DESIGN.md の 2.2・2.6・5 章）。

確かめること:
- 章・節のノード Section は予約ラベルで、どの操作もできない（理由は「章・節」を含む日本語）
- 規則で作ったノード（method='rule' の文書番号 Standard・型式 Model）は、名前・種類・プロパティの変更と削除ができない。
  統合先（名前と種類はそのまま）・リレーションの端・別物の登録には使える。規則で作ったリレーションは手で変えられない
- 孤立ノードの一覧と削除は、規則で作ったノードを扱わない
- 取り込み直し（document_replace.clear_previous_chunks、貸し出しは run_with_lease で取る）は、手で直したエンティティと
  他の文書と共有のエンティティを残して版（_v）を上げ、その文書のチャンクと規則の節を消す（C8 の判定に C6 の版を足したもの）
- 文書の削除（graphDBdataAccess.delete_file_from_graph）は、規則の節を消し、規則が作った文書番号のエンティティは残す。
  端が規則で残るエンティティでも、出どころ（両端を一緒に指すチャンク）が消す文書のチャンクだけのリレーションは消す
- run_with_lease は貸し出しを持ったまま関数を動かし、終われば（失敗しても）返す

合成のノード（名前の正規化キーが zzge で始まるエンティティ、zzge で始まるチャンク・文書・節）だけを作って消す。どの写しでも流せる。
試験の場・後片付け・写しの件数の確かめは test_graph_edit_ops の取り決めをそのまま使う（節は下の fixture で消す）。

実行: 本番の backend を読み取り専用で載せた一時コンテナから、試験用 Neo4j（neo4j-gbdev）に向けて流す
（docs/graph-edit/DESIGN.md の 7 章）。本番の backend コンテナでは流さない
"""
import json

import pytest

from src.document_replace import clear_previous_chunks
from src.graph_edit import ops, read
from src.graph_edit.lease import run_with_lease
from src.graph_edit.ledger import alias_uid
from src.graph_edit.validate import RULE_MADE_REASON, validate_type_labels
from src.graphDB_dataAccess import graphDBdataAccess
from tests.test_graph_edit_ops import (  # noqa: F401  （試験の場と後片付けの fixture をそのまま使う）
    TEST_URI, USER, World, driver, expect_error, graph, lock_state, make_run, pristine, run,
)

SECTION_REASON_WORD = "章・節"


@pytest.fixture(autouse=True)
def drop_sections(run):
    """試験で作った規則の節（id が zzge で始まる Section）を消す。合成の文書・チャンク・エンティティは run の後片付けが消す。"""
    yield
    run("MATCH (s:Section) WHERE s.id STARTS WITH 'zzge' DETACH DELETE s")


def create_section(run, world, section_id, parent=None, attach=True):
    """規則の節（build_section_layer が作る形: Section、fileName、method='rule'、__Entity__ なし）を作り、要素 ID を返す。
    attach なら文書（parent を渡したときはその節）から HAS_SECTION で結ぶ。"""
    eid = run("CREATE (s:Section {id: $id, fileName: $doc, method: 'rule', title: $id}) RETURN elementId(s) AS eid",
              id=section_id, doc=world.doc)[0]["eid"]
    if attach and parent is None:
        run("MATCH (d:Document {fileName: $doc}), (s:Section {id: $id}) CREATE (d)-[:HAS_SECTION]->(s)",
            doc=world.doc, id=section_id)
    elif attach:
        run("MATCH (p:Section {id: $parent}), (s:Section {id: $id}) CREATE (p)-[:HAS_SECTION]->(s)",
            parent=parent, id=section_id)
    return eid


def section_count(run, file_name):
    return run("MATCH (s:Section {fileName: $f}) RETURN count(s) AS n", f=file_name)[0]["n"]


# ---- (1)(2) 予約と規則で作ったノード ----

def test_section_is_reserved(graph, run):
    w = World(run, "rl1")
    sec = create_section(run, w, "zzge-sec-1")
    error = expect_error("RESERVED", ops.rename, graph, sec, "zzge-sec-1", "Zzge RL1 Sec", USER)
    assert SECTION_REASON_WORD in error.data["reason"], "予約ラベルの理由（fileName の理由ではなく）が返っていない"
    expect_error("RESERVED", ops.set_labels, graph, sec, "zzge-sec-1", json.dumps(["Concept"]), USER)
    expect_error("RESERVED", ops.delete_nodes, graph, json.dumps([{"element_id": sec, "expected_id": "zzge-sec-1"}]),
                 "false", USER)
    detail = read.node_detail(graph, sec)
    assert detail["editable"] is False and detail["merge_target_only"] is False
    assert SECTION_REASON_WORD in detail["reason"]
    vocabulary = read.vocabulary(graph)
    assert "Section" not in vocabulary["labels"] and "Section" in vocabulary["reserved_labels"]
    assert not {"HAS_SECTION", "IN_SECTION"} & set(vocabulary["relationship_types"])
    assert vocabulary["reserved_relationship_types"][-2:] == ["HAS_SECTION", "IN_SECTION"]
    assert "APPLIES_TO" not in vocabulary["reserved_relationship_types"], "EDC の述語 APPLIES_TO まで予約になっている"
    # 取り込みが付け替える名前（大小を問わない）は種類に選べない。付け替えた後の名前は選べる
    for labels in (["Section"], ["section"], ["CHUNK"]):
        expect_error("RESERVED", validate_type_labels, labels)
    assert validate_type_labels(["SectionRef"]) == ["SectionRef"]
    assert run("MATCH (s:Section {id: 'zzge-sec-1'}) RETURN count(s) AS n")[0]["n"] == 1


def test_rule_made_entity_is_read_only(graph, run):
    w = World(run, "rl2")
    std = w.entity("Zzge RL2 Std", ["Standard"], method="rule", aliases=["ZZGE-RL2-STD"])
    attempts = [
        (ops.rename, (std, "Zzge RL2 Std", "Zzge RL2 Std 2", USER)),
        (ops.set_labels, (std, "Zzge RL2 Std", json.dumps(["Concept"]), USER)),
        (ops.update_props, (std, "Zzge RL2 Std", json.dumps({"note": "n"}), USER)),
        (ops.delete_nodes, (json.dumps([{"element_id": std, "expected_id": "Zzge RL2 Std"}]), "false", USER)),
    ]
    for fn, args in attempts:
        error = expect_error("RESERVED", fn, graph, *args)
        assert error.data["reason"] == RULE_MADE_REASON, fn.__name__
    detail = read.node_detail(graph, std)
    assert detail["editable"] is False and detail["merge_target_only"] is True and detail["reason"] == RULE_MADE_REASON
    node = w.node("Zzge RL2 Std")
    assert node["props"].get("_v") is None and node["props"]["aliases"] == ["ZZGE-RL2-STD"], "断った操作でノードが変わった"
    # LLM 由来の同じ種類（method なし）は普通のエンティティとして直せる
    llm = w.entity("Zzge RL2 Llm", ["Standard"])
    ops.rename(graph, llm, "Zzge RL2 Llm", "Zzge RL2 Llm 2", USER)
    assert w.node("Zzge RL2 Llm 2") is not None


# ---- (3)(4) 統合: 規則で作ったノードは統合先にだけでき、名前と種類はそのまま ----

def test_merge_into_rule_made_target(graph, run):
    w = World(run, "rl3")
    std = w.entity("Zzge RL3 Std", ["Standard"], chunks=(0,), method="rule", aliases=["ZZGE-RL3-STD"])
    llm = w.entity("Zzge RL3 Std 規格", ["Concept"], chunks=(1,), description="抽出したエンティティ")
    result = ops.merge(graph, std, "Zzge RL3 Std", json.dumps([{"element_id": llm, "expected_id": "Zzge RL3 Std 規格"}]),
                       "Zzge RL3 Std", json.dumps(["Standard"]), "target", None, USER)
    assert result["merged_ids"] == ["Zzge RL3 Std 規格"]
    node = w.node("Zzge RL3 Std")
    assert node["eid"] == std and node["props"]["method"] == "rule"
    assert sorted(node["labels"]) == ["Standard", "__Entity__"]
    assert sorted(node["props"]["aliases"]) == sorted(["ZZGE-RL3-STD", "Zzge RL3 Std 規格"]), \
        "文書層の別名を残して台帳の別名を足していない"
    assert sorted(node["chunks"]) == sorted(w.chunks[:2])
    row = w.ledger(alias_uid("Zzge RL3 Std 規格"))
    assert row["alias"] == "Zzge RL3 Std 規格" and row["target"] == "Zzge RL3 Std"
    assert w.node("Zzge RL3 Std 規格") is None
    # 台帳の別名を消すと、その別名だけが aliases から外れ、文書層の別名は残る
    dropped = ops.ledger_delete(graph, alias_uid("Zzge RL3 Std 規格"), USER)
    assert w.node("Zzge RL3 Std")["props"]["aliases"] == ["ZZGE-RL3-STD"]
    ops.undo(graph, dropped["op_id"], USER)
    assert sorted(w.node("Zzge RL3 Std")["props"]["aliases"]) == sorted(["ZZGE-RL3-STD", "Zzge RL3 Std 規格"])
    # 統合の取り消しは、後の操作（台帳の行の削除と、その取り消し）が同じエンティティに触れたので断られる
    expect_error("NOT_UNDOABLE", ops.undo, graph, result["op_id"], USER)


def test_merge_into_rule_made_target_and_undo(graph, run):
    w = World(run, "rl3u")
    std = w.entity("Zzge RL3U Std", ["Standard"], chunks=(0,), method="rule", aliases=["ZZGE-RL3U-STD"])
    llm = w.entity("Zzge RL3U Std 規格", ["Concept"], chunks=(1,))
    result = ops.merge(graph, std, "Zzge RL3U Std", json.dumps([{"element_id": llm, "expected_id": "Zzge RL3U Std 規格"}]),
                       "Zzge RL3U Std", json.dumps(["Standard"]), "target", None, USER)
    ops.undo(graph, result["op_id"], USER)
    restored = w.node("Zzge RL3U Std")
    assert restored["eid"] == std and restored["props"]["method"] == "rule"
    assert restored["props"]["aliases"] == ["ZZGE-RL3U-STD"] and restored["chunks"] == [w.chunks[0]]
    source = w.node("Zzge RL3U Std 規格")
    assert source is not None and sorted(source["labels"]) == ["Concept", "__Entity__"] and source["chunks"] == [w.chunks[1]]
    assert w.ledger(alias_uid("Zzge RL3U Std 規格")) is None


def test_merge_rule_target_keeps_name_and_labels(graph, run):
    w = World(run, "rl4")
    std = w.entity("Zzge RL4 Std", ["Standard"], method="rule")
    llm = w.entity("Zzge RL4 Std 規格", ["Concept"], chunks=(1,))
    sources = json.dumps([{"element_id": llm, "expected_id": "Zzge RL4 Std 規格"}])
    # 残す名前を変える
    error = expect_error("RESERVED", ops.merge, graph, std, "Zzge RL4 Std", sources, "Zzge RL4 Std 規格",
                         json.dumps(["Standard"]), "target", None, USER)
    assert "名前とラベル" in error.data["reason"]
    # 種類を変える
    error = expect_error("RESERVED", ops.merge, graph, std, "Zzge RL4 Std", sources, "Zzge RL4 Std",
                         json.dumps(["Standard", "Concept"]), "target", None, USER)
    assert "名前とラベル" in error.data["reason"]
    # 規則で作ったノードを統合元にする
    error = expect_error("RESERVED", ops.merge, graph, llm, "Zzge RL4 Std 規格",
                         json.dumps([{"element_id": std, "expected_id": "Zzge RL4 Std"}]), "Zzge RL4 Std 規格",
                         json.dumps(["Concept"]), "target", None, USER)
    assert error.data["reason"] == RULE_MADE_REASON
    assert w.node("Zzge RL4 Std") is not None and w.node("Zzge RL4 Std 規格") is not None
    assert not run("MATCH (g:__GraphEdit__ {kind: 'alias'}) WHERE g.target STARTS WITH 'Zzge RL4' RETURN g")


# ---- (5) リレーション: 規則で作ったノードは端に使える。規則で作ったリレーションは変えられない ----

def test_relationship_with_rule_made_end(graph, run):
    w = World(run, "rl5")
    std = w.entity("Zzge RL5 Std", ["Standard"], method="rule")
    part = w.entity("Zzge RL5 Part", ["Part"])
    created = ops.create_relationship(graph, part, "Zzge RL5 Part", std, "Zzge RL5 Std", "COMPLIES_WITH", "{}", USER)
    rid = created["relationship"]["element_id"]
    updated = ops.update_relationship(graph, rid, json.dumps({"src": "Zzge RL5 Part", "type": "COMPLIES_WITH",
                                                              "tgt": "Zzge RL5 Std"}), "CONFORMS_TO", "false", None, USER)
    assert updated["relationship"]["type"] == "CONFORMS_TO"
    deleted = ops.delete_relationship(graph, updated["relationship"]["element_id"],
                                      json.dumps({"src": "Zzge RL5 Part", "type": "CONFORMS_TO", "tgt": "Zzge RL5 Std"}),
                                      "true", USER)
    between = "MATCH (:__Entity__ {id: 'Zzge RL5 Part'})-[r]->(:__Entity__ {id: 'Zzge RL5 Std'}) RETURN type(r) AS t"
    assert run(between) == []
    ops.undo(graph, deleted["op_id"], USER)
    assert [r["t"] for r in run(between)] == ["CONFORMS_TO"]
    # 規則で作ったリレーション（method='rule'）は、種類が予約でなくても手で変えられない
    rule_rel = w.rel("Zzge RL5 Part", "APPLIES_TO", "Zzge RL5 Std", method="rule")
    expected = json.dumps({"src": "Zzge RL5 Part", "type": "APPLIES_TO", "tgt": "Zzge RL5 Std"})
    error = expect_error("RESERVED", ops.delete_relationship, graph, rule_rel, expected, "true", USER)
    assert "規則により生成したリレーション" in error.data["reason"]
    expect_error("RESERVED", ops.update_relationship, graph, rule_rel, expected, "LINKS", "false", None, USER)
    assert std


# ---- (6)(7) 孤立ノード ----

def test_delete_unconnected_skips_rule_layer(graph, run):
    w = World(run, "rl6")
    sec = create_section(run, w, "zzge-sec-6", attach=False)
    std = w.entity("Zzge RL6 Std", ["Standard"], chunks=(), method="rule")
    lonely = w.entity("Zzge RL6 Lonely", ["Concept"], chunks=())
    data = ops.delete_unconnected(graph, json.dumps([sec, std, lonely]), USER)
    assert data["deleted_ids"] == ["Zzge RL6 Lonely"] and data["op_id"]
    skipped = {s["element_id"]: s for s in data["skipped"]}
    assert set(skipped) == {sec, std}
    assert skipped[std]["reason"] == RULE_MADE_REASON and SECTION_REASON_WORD in skipped[sec]["reason"]
    assert w.node("Zzge RL6 Std") is not None and w.node("Zzge RL6 Lonely") is None
    assert run("MATCH (s:Section {id: 'zzge-sec-6'}) RETURN count(s) AS n")[0]["n"] == 1


def test_list_unconnected_excludes_rule_layer(graph, run):
    data_access = graphDBdataAccess(graph)
    _, before = data_access.list_unconnected_nodes()
    w = World(run, "rl7")
    create_section(run, w, "zzge-sec-7", attach=False)
    w.entity("Zzge RL7 Std", ["Standard"], chunks=(), method="rule")
    w.entity("Zzge RL7 Model", ["Model"], chunks=(), method="rule")
    w.entity("Zzge RL7 Lonely", ["Concept"], chunks=())
    nodes, after = data_access.list_unconnected_nodes()
    assert after["total"] == before["total"] + 1, "規則で作ったノードが孤立ノードに数えられている"
    ids = {row["e"].get("id") for row in nodes}
    assert not {"zzge-sec-7", "Zzge RL7 Std", "Zzge RL7 Model"} & ids


# ---- (8) 取り込み直し（C8 の document_replace に C6 の版と貸し出しを足したもの）----

def test_clear_previous_chunks_keeps_curated(graph, run):
    a = World(run, "rl8a")      # 取り込み直す文書（状態は Completed）
    b = World(run, "rl8b", 1)   # E3 を共有する別の文書
    e1 = a.entity("Zzge RL8 E1", ["Concept"], chunks=(0,))
    a.entity("Zzge RL8 E2", ["Concept"], chunks=(1,))
    a.entity("Zzge RL8 E3", ["Concept"], chunks=(2,))
    run("MATCH (c:Chunk {id: $c}), (e:__Entity__ {id: 'Zzge RL8 E3'}) CREATE (c)-[:HAS_ENTITY]->(e)", c=b.chunks[0])
    a.rel("Zzge RL8 E2", "NEAR", "Zzge RL8 E3")
    ops.update_props(graph, e1, "Zzge RL8 E1", json.dumps({"note": "手で直した"}), USER)
    assert a.node("Zzge RL8 E1")["props"]["_v"] == 1
    create_section(run, a, "zzge-rl8-sec1")
    create_section(run, a, "zzge-rl8-sec2", parent="zzge-rl8-sec1")
    counts = run_with_lease(graph, f"reingest:{a.doc}", clear_previous_chunks, graph, a.doc)
    assert counts == {"chunks": 3, "entities": 1, "sections": 2, "rule_edges": 0}
    n1, n3 = a.node("Zzge RL8 E1"), a.node("Zzge RL8 E3")
    assert n1 is not None and n1["props"]["_curated"] is True and n1["props"]["_v"] == 2, "手で直したエンティティの版が上がっていない"
    assert a.node("Zzge RL8 E2") is None
    assert n3 is not None and n3["props"]["_v"] == 1 and n3["chunks"] == [b.chunks[0]]
    assert not run("MATCH (c:Chunk) WHERE c.id STARTS WITH 'zzge-rl8a' RETURN c")
    assert section_count(run, a.doc) == 0
    assert run("MATCH (d:Document {fileName: $f}) RETURN count(d) AS n", f=a.doc)[0]["n"] == 1, "文書ノードは残す"
    state = lock_state(run)
    assert state["until"] == 0 and state["owner"] is None


# ---- (9) と追加の 1 本: 文書の削除 ----

def test_delete_document_removes_sections(graph, run, tmp_path):
    w = World(run, "rl9")
    other = World(run, "rl9o", 1)
    w.entity("Zzge RL9 E", ["Concept"])
    # 抽出された型 Section のエンティティ（規則の節ではない）。他の文書のチャンクからも指されるので残る
    w.entity("Zzge RL9 SecE", ["Section"], chunks=(0,), fileName=w.doc)
    run("MATCH (c:Chunk {id: $c}), (e:__Entity__ {id: 'Zzge RL9 SecE'}) CREATE (c)-[:HAS_ENTITY]->(e)", c=other.chunks[0])
    create_section(run, w, "zzge-rl9-sec1")
    create_section(run, w, "zzge-rl9-sec2", parent="zzge-rl9-sec1")
    graphDBdataAccess(graph).delete_file_from_graph(json.dumps([w.doc]), json.dumps(["local file"]), "true", str(tmp_path),
                                                     TEST_URI)
    assert section_count(run, w.doc) == 1, "規則の節が残ったか、抽出された型 Section のエンティティまで消えた"
    assert w.node("Zzge RL9 SecE") is not None and w.node("Zzge RL9 E") is None
    assert not run("MATCH (d:Document {fileName: $f}) RETURN d", f=w.doc)
    assert not run("MATCH (c:Chunk) WHERE c.id STARTS WITH 'zzge-rl9-' RETURN c")
    assert lock_state(run)["until"] == 0


def test_delete_document_keeps_rule_standard(graph, run, tmp_path):
    """文書の削除（エンティティごと）で、その文書だけが指していた規則の Standard（method='rule'）と、
    他の文書から規則のエッジで指されるエンティティは残る（C8 の判定）。"""
    w = World(run, "rl10")
    other = World(run, "rl10o", 1)
    w.entity("Zzge RL10 Std", ["Standard"], chunks=(0,), method="rule")
    w.entity("Zzge RL10 Ref", ["Standard"], chunks=(1,))
    w.entity("Zzge RL10 Only", ["Concept"], chunks=(0,))
    run("MATCH (d:Document {fileName: $f}), (s:__Entity__ {id: 'Zzge RL10 Std'}) "
        "CREATE (d)-[:IS_VERSION_OF {method: 'rule'}]->(s)", f=w.doc)
    run("MATCH (d:Document {fileName: $f}), (s:__Entity__ {id: 'Zzge RL10 Ref'}) "
        "CREATE (d)-[:REFERS_TO {method: 'rule'}]->(s)", f=other.doc)
    graphDBdataAccess(graph).delete_file_from_graph(json.dumps([w.doc]), json.dumps(["local file"]), "true", str(tmp_path),
                                                     TEST_URI)
    std = w.node("Zzge RL10 Std")
    assert std is not None and std["props"]["method"] == "rule" and std["chunks"] == []
    assert std["props"]["_v"] == 1, "HAS_ENTITY を失った規則のエンティティの版が上がっていない"
    assert w.node("Zzge RL10 Ref") is not None, "他の文書から規則のエッジで指されるエンティティが消えた"
    assert w.node("Zzge RL10 Only") is None
    assert not run("MATCH (d:Document {fileName: $f}) RETURN d", f=w.doc)
    assert run("MATCH (:Document {fileName: $f})-[r:REFERS_TO]->() RETURN count(r) AS n", f=other.doc)[0]["n"] == 1
    assert lock_state(run)["until"] == 0


def test_delete_document_removes_relationship_to_rule_entity(graph, run, tmp_path):
    """文書の削除（エンティティごと）で、端が規則で残るエンティティ（method='rule' のもの、他の文書から規則のエッジで
    指されるもの）のリレーションも、出どころ（両端を一緒に指すチャンク）が消す文書のチャンクだけなら消える。
    リレーションの端が残るかの判定は、エンティティを残す判定と同じ（DESIGN.md の B2 の削除の手順の 2・3）。"""
    w = World(run, "rl11")
    other = World(run, "rl11o", 2)
    for name, labels, props in (("Zzge RL11 A", ["Concept"], {}), ("Zzge RL11 B", ["Concept"], {}),
                                ("Zzge RL11 Std", ["Standard"], {"method": "rule"}), ("Zzge RL11 Ref", ["Standard"], {})):
        w.entity(name, labels, chunks=(0,), **props)
    # A と B は残る文書の別々のチャンクからも指される（残るが、両端を一緒に指す残るチャンクは無い）
    for i, name in enumerate(("Zzge RL11 A", "Zzge RL11 B")):
        run("MATCH (c:Chunk {id: $c}), (e:__Entity__ {id: $e}) CREATE (c)-[:HAS_ENTITY]->(e)", c=other.chunks[i], e=name)
    run("MATCH (d:Document {fileName: $f}), (s:__Entity__ {id: 'Zzge RL11 Ref'}) "
        "CREATE (d)-[:REFERS_TO {method: 'rule'}]->(s)", f=other.doc)
    w.rel("Zzge RL11 A", "RELATED", "Zzge RL11 B")   # 対照: 両端が残るチャンクで残る
    w.rel("Zzge RL11 A", "CITES", "Zzge RL11 Std")   # 端が規則で作ったもの（method='rule'）
    w.rel("Zzge RL11 A", "CITES", "Zzge RL11 Ref")   # 端が他の文書から規則のエッジで指されるもの
    w.rel("Zzge RL11 Std", "NEAR", "Zzge RL11 B")    # 規則で作ったものから出る向き
    graphDBdataAccess(graph).delete_file_from_graph(json.dumps([w.doc]), json.dumps(["local file"]), "true", str(tmp_path),
                                                     TEST_URI)
    for name in ("Zzge RL11 A", "Zzge RL11 B", "Zzge RL11 Std", "Zzge RL11 Ref"):
        assert w.node(name) is not None, f"{name} は残る"
    left = [r for r in w.rels() if r[1] in ("RELATED", "CITES", "NEAR")]
    assert left == [], f"出どころの消えたリレーションが残った: {left}"
    assert run("MATCH (:Document {fileName: $f})-[r:REFERS_TO]->() RETURN count(r) AS n", f=other.doc)[0]["n"] == 1
    assert lock_state(run)["until"] == 0


# ---- (10) run_with_lease ----

def test_run_with_lease_holds_and_releases(graph, run):
    seen = {}

    def work(graph_):
        seen.update(lock_state(run))
        return "done"

    assert run_with_lease(graph, "zzge-doclayer", work, graph) == "done"
    assert seen["owner"].startswith("zzge-doclayer#") and seen["until"] > seen["now"]
    state = lock_state(run)
    assert state["until"] == 0 and state["owner"] is None

    def fail(graph_):
        raise RuntimeError("zzge: stop")

    with pytest.raises(RuntimeError):
        run_with_lease(graph, "zzge-doclayer", fail, graph)
    state = lock_state(run)
    assert state["until"] == 0 and state["owner"] is None, "失敗したときに貸し出しが返されていない"
