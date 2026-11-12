"""取り込みの保存の直前と直後の、修正台帳の再適用（設計メモ 4 章、A2）。貸し出し（2.6）の関数は lease.py にあり、ここから使える。

main.processing_chunks の中で、標準の抽出と EDC の抽出の両方がここを通る:

    lease = acquire_lease(graph, owner=f"extract:{file_name}")
    ledger = load_ledger(graph)
    canon = lookup_canonical_labels(graph, alias_targets(cleaned_graph_documents, ledger))
    cleaned_graph_documents, stats = apply_ledger_before_save(cleaned_graph_documents, ledger, canon)
    save_graphDocuments_in_neo4j(...)
    merge_relationship_between_chunk_and_entites(...)
    post_save_ledger(graph, cleaned_graph_documents, ledger, stats)
    release_lease(graph, lease)

apply_ledger_before_save は純粋な関数（Neo4j を呼ばない）。入力の GraphDocument・Node・Relationship は書き換えず、
新しいものを返す（標準の抽出ではリレーションの端点がノードの一覧と別のオブジェクト、EDC の抽出では同じオブジェクトなので、
書き換えると片方にだけ効いたり二重に効いたりする）。
"""
import json
import logging
import time  # noqa: F401  (試験が time.sleep を差し替える)

from langchain_community.graphs.graph_document import GraphDocument, Node, Relationship

from src.graph_edit.lease import (  # noqa: F401  (main と試験はここから使う)
    LEASE_INTERVAL_SECONDS, LEASE_MAX_WAIT_SECONDS, LEASE_MS, acquire_lease, hold_lease, release_lease, renew_lease,
    start_heartbeat,
)
from src.graph_edit.ledger import Ledger, load_ledger  # noqa: F401  (load_ledger は main から使う)
from src.graph_edit.normalize import rel_type_key
from src.graph_edit.validate import ENTITY_LABEL

# 取り込みに書かせないプロパティ（システムのプロパティと台帳から作る別名）
SYSTEM_PROPERTY_KEYS = {"aliases", "embedding"}


# ---- 保存の直前（純粋な関数）----

def alias_targets(graph_documents, ledger):
    """別名で読み替える先の id（代表の型ラベルを引くため）。"""
    targets = set()
    for document in graph_documents:
        names = [node.id for node in document.nodes]
        for rel in document.relationships:
            names += [rel.source.id, rel.target.id]
        for name in names:
            mapped, aliased = ledger.map_id(name)
            if mapped is not None and aliased:
                targets.add(mapped)
    return sorted(targets)


def lookup_canonical_labels(graph, ids):
    """代表のエンティティの型ラベル（__Entity__ を除く。並びは Neo4j の labels() の順）。{id: [labels]}"""
    if not ids:
        return {}
    rows = graph.query(f"UNWIND $ids AS id MATCH (e:{ENTITY_LABEL} {{id: id}}) "
                       f"RETURN id, [l IN labels(e) WHERE l <> '{ENTITY_LABEL}'] AS labels", {"ids": list(ids)})
    return {row["id"]: list(row["labels"]) for row in rows if row["labels"]}


def _resolve_type(mapped, original_type, aliased, ledger, canon):
    """(型, 種類の固定で書き換えたか)。種類の固定があればその先頭、別名で読み替えたなら代表の型ラベルの先頭。"""
    override = ledger.type_override.get(mapped)
    if override:
        return override[0], override[0] != original_type
    if aliased and canon.get(mapped):
        return canon[mapped][0], False
    return original_type, False


def apply_ledger_before_save(graph_documents, ledger, canon=None):
    """修正台帳を抽出結果に当てる（設計メモ 4 章の 1〜5）。(新しい GraphDocument の一覧, 統計) を返す。

    1. ノードの名前を読み替える（削除の記録に当たれば落とす、別名なら代表の id）。種類の固定があればその型、
       別名で読み替えたノードは代表の型ラベルにそろえる。
    2. リレーションの始点と終点にも同じ読み替えをする。どちらかの端が落ちたら落とす。削除したリレーションの記録
       （正規化キーの始点・種類・終点）に当たれば落とす。読み替えで始点と終点が同じになったら落とす。
    3. 修正したプロパティ（curated）をノードから外す（システムのプロパティも外す）。
    4. 同じ文書の中で読み替えで同じ id になったノードは 1 つにまとめる。
    5. 統計 {aliased, dropped_nodes, dropped_rels, protected_props, type_overridden, merged_nodes}。
    """
    if ledger is None:
        ledger = Ledger()
    canon = canon or {}
    stats = {"aliased": 0, "dropped_nodes": 0, "dropped_rels": 0, "protected_props": 0, "type_overridden": 0,
             "merged_nodes": 0}
    out = []
    for document in graph_documents:
        nodes = {}  # 読み替えた id -> 新しい Node（並びは最初に出た順）
        for node in document.nodes:
            mapped, aliased = ledger.map_id(node.id)
            if mapped is None:
                stats["dropped_nodes"] += 1
                continue
            if aliased:
                stats["aliased"] += 1
            node_type, overridden = _resolve_type(mapped, node.type, aliased, ledger, canon)
            if overridden:
                stats["type_overridden"] += 1
            protected = ledger.curated.get(mapped, set())
            props = {}
            for key, value in (node.properties or {}).items():
                if key in protected:
                    stats["protected_props"] += 1
                elif key in SYSTEM_PROPERTY_KEYS or str(key).startswith("_") or key == "id":
                    continue
                else:
                    props[key] = value
            if mapped in nodes:
                stats["merged_nodes"] += 1
                for key, value in props.items():
                    nodes[mapped].properties.setdefault(key, value)
                continue
            nodes[mapped] = Node(id=mapped, type=node_type, properties=props)

        relationships = []
        for rel in document.relationships:
            source, source_aliased = ledger.map_id(rel.source.id)
            target, target_aliased = ledger.map_id(rel.target.id)
            if source is None or target is None:
                stats["dropped_rels"] += 1
                continue
            rel_type = rel_type_key(rel.type)
            if ledger.is_tombstoned_rel(source, rel_type, target) or ledger.is_tombstoned_rel(rel.source.id, rel_type, rel.target.id):
                stats["dropped_rels"] += 1
                continue
            if source == target and (source != rel.source.id or target != rel.target.id):
                stats["dropped_rels"] += 1
                continue
            ends = []
            for mapped, aliased, original in ((source, source_aliased, rel.source), (target, target_aliased, rel.target)):
                if mapped in nodes:
                    ends.append(nodes[mapped])
                else:
                    end_type, _ = _resolve_type(mapped, original.type, aliased, ledger, canon)
                    ends.append(Node(id=mapped, type=end_type))
            relationships.append(Relationship(source=ends[0], target=ends[1], type=rel.type,
                                              properties=dict(rel.properties or {})))
        out.append(GraphDocument(nodes=list(nodes.values()), relationships=relationships, source=document.source))
    return out, stats


# ---- 保存の直後 ----

def post_save_ledger(graph, graph_documents, ledger, stats=None):
    """このバッチのエンティティの _v を 1 増やし、端点がこのバッチに含まれる manual_rel のうち今グラフに無いものを作り直す。"""
    ids = sorted({node.id for document in graph_documents for node in document.nodes})
    result = {"versioned": 0, "manual_rels_recreated": 0}
    if ids:
        rows = graph.query(f"UNWIND $ids AS id MATCH (e:{ENTITY_LABEL} {{id: id}}) "
                           "SET e._v = coalesce(e._v, 0) + 1 RETURN count(e) AS n", {"ids": ids})
        result["versioned"] = rows[0]["n"] if rows else 0
    if ledger is not None and ids:
        id_set = set(ids)
        manual = [{"src": r["src"], "type": r["type"], "tgt": r["tgt"], "props": r["props"]}
                  for r in ledger.manual_rels if (r["src"] in id_set or r["tgt"] in id_set) and r["type"]]
        if manual:
            rows = graph.query(f"""
                UNWIND $rows AS row
                MATCH (a:{ENTITY_LABEL} {{id: row.src}})
                MATCH (b:{ENTITY_LABEL} {{id: row.tgt}})
                WITH a, b, row WHERE NOT EXISTS {{ (a)-[r]->(b) WHERE type(r) = row.type }}
                CALL apoc.create.relationship(a, row.type, row.props, b) YIELD rel
                RETURN count(rel) AS n""", {"rows": manual})
            result["manual_rels_recreated"] = rows[0]["n"] if rows else 0
    if stats is not None:
        stats.update(result)
    return result


def log_stats(file_name, stats):
    if any(stats.get(k) for k in ("aliased", "dropped_nodes", "dropped_rels", "protected_props", "type_overridden",
                                  "merged_nodes", "manual_rels_recreated")):
        logging.info(f"graph_edit: ledger applied to {file_name}: {json.dumps(stats, ensure_ascii=False)}")
