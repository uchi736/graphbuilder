"""読み取りの入口（設計メモ 3 章の node_detail・search_entities・neighborhood・vocabulary・ledger）と、
重複の候補から別物の組を外すための要素 ID の組（M10）。"""
import glob
import os

from src.graph_edit import history
from src.graph_edit.ledger import check_kind, cur_uid, type_uid
from src.graph_edit.normalize import norm_key
from src.graph_edit.ops import ENTITY_IDS_QUERY, node_dict, node_hit_records, node_hits, rel_dict, run_read
from src.graph_edit.validate import (
    ENTITY_LABEL, GRAPH_EDIT_LABEL, NOT_FOUND, RESERVED, RESERVED_LABELS, RESERVED_RELATIONSHIP_TYPES, EditError,
    entity_block, parse_int, require_text, usable_label, usable_type,
)

DETAIL_RELATIONSHIP_LIMIT = 100
NEIGHBORHOOD_LIMIT = 1000

# 近傍・詳細で隣に数えないノード（チャンク・文書・コミュニティ・会話の履歴・管理用）
_NOT_NEIGHBOR = ("o:Chunk OR o:`__Community__` OR o.fileName IS NOT NULL OR "
                 + " OR ".join(f"o:`{label}`" for label in RESERVED_LABELS))


def _editable_where(var="e"):
    """編集の対象のエンティティ: __Entity__ を持ち、予約ラベルと fileName を持たない。"""
    reserved = " OR ".join(f"{var}:`{label}`" for label in RESERVED_LABELS)
    return f"{var}.fileName IS NULL AND {var}.id IS NOT NULL AND NOT ({reserved})"


def _neighbor_rels(tx, element_id, limit, order_by=""):
    """ノードの隣のリレーション（チャンク・文書・コミュニティ・管理用への線を除く）と相手のノード。
    ([rel_dict], {要素 ID: node_dict}（中心のノード自身は入れない）)"""
    rels, neighbors, seen = [], {}, set()
    for r in tx.run(f"""
            MATCH (n)-[r]-(o) WHERE elementId(n) = $eid AND NOT ({_NOT_NEIGHBOR})
            RETURN DISTINCT r, o {order_by} LIMIT $limit""", eid=element_id, limit=limit):
        if r["r"].element_id in seen:
            continue
        seen.add(r["r"].element_id)
        rels.append(rel_dict(r["r"]))
        if r["o"].element_id != element_id:
            neighbors.setdefault(r["o"].element_id, node_dict(r["o"]))
    return rels, neighbors


def node_detail(graph, element_id):
    element_id = require_text(element_id, "element_id")

    def work(tx):
        records = node_hit_records(tx, [element_id])
        if not records:
            raise EditError(NOT_FOUND, f"node {element_id} does not exist")
        record = records[0]
        node = record["n"]
        rels, neighbors = _neighbor_rels(tx, element_id, DETAIL_RELATIONSHIP_LIMIT, "ORDER BY type(r), o.id")
        total = tx.run(f"MATCH (n)-[r]-(o) WHERE elementId(n) = $eid AND NOT ({_NOT_NEIGHBOR}) "
                       "RETURN count(DISTINCT r) AS c", eid=element_id).single()["c"]
        entity_id = node.get("id")
        aliases, type_override, curated_keys = [], None, []
        if entity_id is not None:
            aliases = sorted({row["alias"] for row in tx.run(
                f"MATCH (g:{GRAPH_EDIT_LABEL} {{kind: 'alias'}}) WHERE g.target = $id RETURN g.alias AS alias", id=entity_id)
                if row["alias"] and row["alias"] != entity_id}, key=lambda a: (norm_key(a), a))
            type_row = tx.run(f"MATCH (g:{GRAPH_EDIT_LABEL} {{uid: $uid}}) RETURN g.labels AS labels",
                              uid=type_uid(entity_id)).single()
            type_override = list(type_row["labels"]) if type_row and type_row["labels"] else None
            cur_row = tx.run(f"MATCH (g:{GRAPH_EDIT_LABEL} {{uid: $uid}}) RETURN g.keys AS keys",
                             uid=cur_uid(entity_id)).single()
            curated_keys = list(cur_row["keys"]) if cur_row and cur_row["keys"] else []
        block = entity_block(node.labels, dict(node))
        return {"node": node_dict(node), "relationships": rels, "neighbors": list(neighbors.values()),
                "relationship_total": total, "chunk_count": record["chunk_count"], "documents": sorted(record["documents"]),
                "ledger": {"aliases": aliases, "type_override": type_override, "curated_keys": curated_keys},
                # 規則で作ったノード（統合先・リレーションの端・別物の登録にだけ使える）のときだけ True
                "merge_target_only": block is not None and entity_block(node.labels, dict(node), allow_rule_made=True) is None,
                "editable": block is None, "reason": "" if block is None else block[1]}

    return run_read(graph, work)


def search_entities(graph, q, limit=20):
    """id の部分一致と正規化キーの一致（正規化キーどうしの部分一致も含む）。編集の対象のエンティティだけ。"""
    q = require_text(q, "q")
    limit = parse_int(limit, 20, minimum=1, maximum=200, name="limit")
    key = norm_key(q)
    lowered = q.casefold()

    def work(tx):
        candidates = []
        for r in tx.run(f"MATCH (e:{ENTITY_LABEL}) WHERE {_editable_where('e')} "
                        "RETURN elementId(e) AS eid, e.id AS id, COUNT { (e)<-[:HAS_ENTITY]-(:Chunk) } AS chunks"):
            entity_id = str(r["id"])
            entity_key = norm_key(entity_id)
            if entity_id == q:
                rank = 0
            elif entity_key == key:
                rank = 1
            elif entity_key.startswith(key) or entity_id.casefold().startswith(lowered):
                rank = 2
            elif q in entity_id or lowered in entity_id.casefold() or (key and key in entity_key):
                rank = 3
            else:
                continue
            candidates.append((rank, -r["chunks"], len(entity_id), entity_id, r["eid"]))
        candidates.sort()
        hits = {h["node"]["element_id"]: h for h in node_hits(tx, [c[4] for c in candidates[:limit]])}
        return [hits[c[4]] for c in candidates[:limit] if c[4] in hits]

    return run_read(graph, work)


def neighborhood(graph, element_id):
    """そのエンティティと 1 ホップ先（チャンク・文書・コミュニティを除く）。{nodes, relationships}"""
    element_id = require_text(element_id, "element_id")

    def work(tx):
        record = tx.run("MATCH (n) WHERE elementId(n) = $eid RETURN n", eid=element_id).single()
        if record is None:
            raise EditError(NOT_FOUND, f"node {element_id} does not exist")
        center = record["n"]
        if ENTITY_LABEL not in center.labels:
            raise EditError(RESERVED, f"node {element_id} is not an entity")
        rels, neighbors = _neighbor_rels(tx, element_id, NEIGHBORHOOD_LIMIT)
        nodes = {element_id: node_dict(center), **neighbors}
        return {"nodes": list(nodes.values()), "relationships": rels}

    return run_read(graph, work)


def _edc_vocabulary():
    """EDC の語彙（/edc_schemas の CSV の型名と述語名）。*_types.csv の 1 列目が型名、それ以外の CSV の 1 列目が述語名。"""
    from src.edc_extraction import read_csv_names, schemas_dir
    directory = schemas_dir()
    labels, rel_types = set(), set()
    for path in sorted(glob.glob(os.path.join(directory, "*.csv"))):
        try:
            names = read_csv_names(directory, path)
        except OSError:
            continue
        if path.endswith("_types.csv"):
            labels |= names
        else:
            rel_types |= names
    return labels, rel_types


def vocabulary(graph):
    def work(tx):
        labels = [r["label"] for r in tx.run("CALL db.labels() YIELD label RETURN label")]
        rel_types = [r["t"] for r in tx.run("CALL db.relationshipTypes() YIELD relationshipType AS t RETURN t")]
        return labels, rel_types

    labels, rel_types = run_read(graph, work)
    edc_labels, edc_rel_types = _edc_vocabulary()
    return {"labels": sorted(l for l in labels if usable_label(l)),
            "relationship_types": sorted(t for t in rel_types if usable_type(t)),
            "edc_labels": sorted(l for l in edc_labels if usable_label(l)),
            "edc_relationship_types": sorted(t for t in edc_rel_types if usable_type(t)),
            "reserved_labels": list(RESERVED_LABELS),
            "reserved_relationship_types": list(RESERVED_RELATIONSHIP_TYPES)}


def ledger(graph, kind=None, limit=100, offset=0):
    kind = check_kind((kind or "").strip() or None)
    limit = parse_int(limit, 100, minimum=1, maximum=1000, name="limit")
    offset = parse_int(offset, 0, minimum=0, name="offset")

    def work(tx):
        return [history.jsonable(dict(r["p"])) for r in tx.run(f"""
            MATCH (g:{GRAPH_EDIT_LABEL}) WHERE $kind IS NULL OR g.kind = $kind
            RETURN properties(g) AS p ORDER BY g.created_at DESC, g.uid SKIP $offset LIMIT $limit""",
            kind=kind, offset=offset, limit=limit)]

    return run_read(graph, work)


def distinct_element_pairs(graph):
    """別物の組（distinct）に当たるエンティティの要素 ID の組 [[小さい方, 大きい方]]。重複の候補から外すために使う。"""
    def work(tx):
        pairs = [tuple(sorted([norm_key(r["a"]), norm_key(r["b"])]))
                 for r in tx.run(f"MATCH (g:{GRAPH_EDIT_LABEL} {{kind: 'distinct'}}) RETURN g.a AS a, g.b AS b")]
        if not pairs:
            return []
        by_key = {}
        for r in tx.run(ENTITY_IDS_QUERY):
            by_key.setdefault(norm_key(r["id"]), []).append(r["eid"])
        out = set()
        for a, b in pairs:
            for x in by_key.get(a, []):
                for y in by_key.get(b, []):
                    if x != y:
                        out.add(tuple(sorted([x, y])))
        return [list(p) for p in sorted(out)]

    return run_read(graph, work)
