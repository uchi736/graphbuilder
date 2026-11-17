# -*- coding: utf-8 -*-
"""文書の取り込み直しと削除の後始末（C8）。LLM は使わない。

取り込み直し（同じ名前のファイルで抽出し直す）: clear_previous_chunks(graph, file_name) -> {chunks, entities, sections, rule_edges}
  消す: その文書のチャンク、その文書のチャンクからしか HAS_ENTITY で結ばれていないエンティティ、
        規則が作った節（:Section {method: "rule"}）と文書層のエッジ（その文書から出る IS_VERSION_OF・REFERS_TO・LISTS・
        APPLIES_TO・ABOLISHES・REPLACES と、その文書へ入る REPLACES・HAS_CURRENT_VERSION。どれも method = "rule"）
  残す: 他の文書と共有のエンティティ、規則が作った共有のエンティティ（Standard・Model など method = "rule" のもの、
        他の文書から規則のエッジで指されるもの）、手動で修正したエンティティ（_curated）、抽出された型 Section のエンティティ、
        文書ノード（状態と文書カード。カードは build_document_layer が書き直す）
  残すエンティティのうち HAS_ENTITY を失うものと消すエンティティの隣のものは、版（_v）を 1 上げる（グラフの手動修正。C6）。
  貸し出し（lease）は呼び出し側（main.processing_source の run_with_lease）で取る。このモジュールは graph_edit を import しない。
文書の削除（graphDB_dataAccess.delete_file_from_graph）: 貸し出しの中で、削除の前に delete_rule_layer を呼ぶ。
文書層と節の層は後処理（build_document_layer・build_section_layer）で作り直す。
"""
import logging

from src.shared.common_fn import execute_graph_query

RULE = "rule"
# 規則が文書ノードから張るエッジ（document_layer.py の書き込みと揃える）。REPLACES は 新しい版 → 古い版 なので入る向きも消す。
# 現行版の印 HAS_CURRENT_VERSION は Standard → Document なので、入る向きとして消す
RULE_DOCUMENT_EDGES = ["IS_VERSION_OF", "REFERS_TO", "LISTS", "APPLIES_TO", "ABOLISHES", "REPLACES"]


def _count(graph, query, params):
    rows = execute_graph_query(graph, query, params=params)
    return rows[0]["n"] if rows else 0


def delete_rule_layer(graph, file_names):
    """規則が作った文書層のエッジと節を消し、{rule_edges, sections} を返す。抽出された型 Section のエンティティは消さない。"""
    p = {"files": list(file_names), "types": RULE_DOCUMENT_EDGES, "m": RULE}
    edges = _count(graph, """
        MATCH (d:Document)-[r]->() WHERE d.fileName IN $files AND type(r) IN $types AND r.method = $m
        DELETE r RETURN count(*) AS n""", p)
    edges += _count(graph, """
        MATCH ()-[r:REPLACES|HAS_CURRENT_VERSION]->(d:Document) WHERE d.fileName IN $files AND r.method = $m
        DELETE r RETURN count(*) AS n""", p)
    sections = _count(graph, """
        MATCH (s:Section) WHERE s.fileName IN $files AND s.method = $m AND NOT s:__Entity__
        DETACH DELETE s RETURN count(*) AS n""", p)
    return {"rule_edges": edges, "sections": sections}


def clear_previous_chunks(graph, file_name):
    """同じ名前の文書を取り込み直すとき、前の版を消して {chunks, entities, sections, rule_edges} を返す。
    チャンク ID は 文書名＋位置＋本文 なので、本文が変わった節は別の ID になり、消さないと前の版の節が残る。
    通るのは新規の抽出と再処理の「最初から」（ファイルを読み直す）。「エンティティを消して最初から」「続きから」は前のチャンクを使うので通らない。
    処理中（Processing）の文書は消さない（走っている抽出のチャンクとエンティティを消してしまう）。
    貸し出しは呼び出し側（main.processing_source）で取る。"""
    counts = {"chunks": 0, "entities": 0, "sections": 0, "rule_edges": 0}
    p = {"f": file_name, "m": RULE}
    doc = execute_graph_query(graph, "MATCH (d:Document {fileName: $f}) RETURN d.status AS status", params=p)
    if not doc:
        return counts
    if doc[0]["status"] == "Processing":
        logging.warning(f"{file_name}: 処理中なので前の版を消さない")
        return counts
    counts.update(delete_rule_layer(graph, [file_name]))
    # エンティティはチャンクより先に消す（チャンクからたどって選ぶ）。この文書の規則のエッジは上で消したので、残る規則のエッジは他の文書のもの
    # その文書のチャンクからだけ指されるエンティティを消す（他の文書と共有のもの・規則が作ったもの・手動で修正したものは残す）
    # 残すエンティティのうち、この文書のチャンクから指されていたもの（HAS_ENTITY を失う）と消すエンティティの隣のものは版（_v）を 1 上げる（C6、設計メモ 2.3）
    rows = execute_graph_query(graph, """
        MATCH (:Document {fileName: $f})<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e)
        WITH collect(DISTINCT e) AS touched
        WITH touched, [e IN touched WHERE
              NOT EXISTS { MATCH (e)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d2:Document) WHERE d2.fileName <> $f }
              AND coalesce(e.method, '') <> $m AND NOT coalesce(e._curated, false)
              AND NOT EXISTS { MATCH (:Document)-[r]->(e) WHERE r.method = $m }] AS entities
        WITH touched, entities, apoc.map.fromPairs([e IN entities | [elementId(e), true]]) AS deleted
        CALL (touched, entities, deleted) {
            WITH [k IN touched WHERE deleted[elementId(k)] IS NULL AND k:__Entity__] AS kept,
                 apoc.coll.flatten([e IN entities | [(e)--(k:__Entity__) WHERE deleted[elementId(k)] IS NULL | k]]) AS neighbours
            UNWIND kept + neighbours AS k
            WITH DISTINCT k
            SET k._v = coalesce(k._v, 0) + 1
            RETURN count(k) AS versioned
        }
        FOREACH (e IN entities | DETACH DELETE e)
        RETURN size(entities) AS n, versioned""", params=p)
    counts["entities"] = rows[0]["n"] if rows else 0
    # 他の文書とも共有のチャンク（過去に本文だけのハッシュで取り込んだ DB に残る共有のチャンク）は消さず、この文書との PART_OF・FIRST_CHUNK だけ外す
    execute_graph_query(graph, """
        MATCH (d:Document {fileName: $f})<-[r:PART_OF]-(c:Chunk)
        WHERE EXISTS { (c)-[:PART_OF]->(o:Document) WHERE o.fileName <> $f }
        OPTIONAL MATCH (d)-[fr:FIRST_CHUNK]->(c)
        DELETE r, fr""", params=p)
    counts["chunks"] = _count(graph, """
        MATCH (:Document {fileName: $f})<-[:PART_OF]-(c:Chunk)
        DETACH DELETE c RETURN count(*) AS n""", p)
    if any(counts.values()):
        logging.info(f"{file_name}: 前の版を消してから分割し直す {counts}")
    return counts

