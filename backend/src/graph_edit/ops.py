"""編集の操作（設計メモ 3 章の M1〜M10）。

各操作は 1 つの書き込みトランザクションで行い、最初に排他のノード（__GraphLock__ {name:'graph'}）の seq を増やす
（ノードの書き込み鍵がトランザクションの間保たれ、編集どうしが直列になる。取り込みの保存の貸し出し（lease）の最中なら BUSY）。
トランザクションの中で、対象の名前（expected_id）を照合し、写しを取り、グラフと修正台帳を書き換え、操作の履歴を残す。
埋め込みはトランザクションの前に計算し、中で書く。文書の件数はトランザクションの後で数え直す。

入口から呼ぶ関数（rename など）は Form の値（JSON は文字列）をそのまま受ける。失敗は validate.EditError。
"""
import json
import logging
from uuid import uuid4

from src.graph_edit import history
from src.graph_edit import ledger as ledger_rows
from src.graph_edit.embedding import embed_texts, entity_text, index_dimension, vector_for, write_embedding
from src.graph_edit.ledger import LedgerTx, ensure_schema, now_iso
from src.graph_edit.normalize import norm_key
from src.graph_edit.validate import (
    BUSY, CONFLICT, ENTITY_LABEL, GRAPH_EDIT_OP_LABEL, GRAPH_LOCK_LABEL, INVALID, NOT_FOUND, STALE, EditError,
    check_entity, entity_block, is_reserved_relationship_type, optional_text, parse_bool, parse_expected_relationship,
    parse_int, parse_json, parse_node_refs, require_text, type_labels_of, validate_entity_props, validate_relationship_props,
    validate_relationship_type, validate_type_labels, RESERVED, RULE_METHOD, is_rule_made,
)
from src.graph_query import process_node
from src.graphDB_dataAccess import graphDBdataAccess

DESCRIPTION_MODES = ("target", "concat", "custom")
LOCK_QUERY = f"""
MERGE (l:{GRAPH_LOCK_LABEL} {{name: 'graph'}})
SET l.seq = coalesce(l.seq, 0) + 1
RETURN l.seq AS seq, coalesce(l.lease_until, 0) AS lease_until, l.lease_owner AS owner, timestamp() AS now
"""
MERGE_CONFIG = {"properties": "discard", "mergeRels": True, "produceSelfRel": False, "preserveExistingSelfRels": False}


# ---- 応答の形 ----

def node_dict(node):
    """graph_query.process_node と同じ形（element_id, labels（__Entity__ を除く）, properties（embedding/text/summary を除く））。"""
    out = process_node(node)
    out["labels"] = sorted(out["labels"])
    out["properties"] = history.jsonable(out["properties"])
    return out


def rel_dict(rel):
    return {"element_id": rel.element_id, "type": rel.type,
            "start_node_element_id": rel.start_node.element_id, "end_node_element_id": rel.end_node.element_id,
            "properties": history.jsonable(dict(rel))}


# ---- トランザクション ----

def _session(graph):
    return graph._driver.session(database=graph._database)


def run_read(graph, fn):
    with _session(graph) as session:
        return session.execute_read(fn)


def run_write(graph, fn):
    with _session(graph) as session:
        return session.execute_write(fn)


class Edit:
    """1 つの編集の操作（書き込みトランザクションの中）。"""

    def __init__(self, tx, op, user, payload):
        self.tx = tx
        self.op = op
        self.op_id = str(uuid4())
        self.user = user
        self.payload = payload
        self.lt = LedgerTx(tx, self.op_id)
        self.snapshots = []
        self.ids_after = {}
        self.versions_after = {}
        self.docs = set()
        self.seq = None
        self.undo_of = None

    def lock(self):
        record = self.tx.run(LOCK_QUERY).single()
        if record["lease_until"] > record["now"]:
            raise EditError(BUSY, f"the graph is being saved by {record['owner'] or 'an extraction'}; try again later")
        self.seq = record["seq"]

    def entity(self, element_id, expected_id=None, allow_rule_made=False):
        """要素 ID のエンティティ（無ければ NOT_FOUND、編集の対象でなければ RESERVED、名前が違えば STALE）。
        allow_rule_made: 規則で作ったノードも受け付ける（統合先・リレーションの端・別物の登録）"""
        record = self.tx.run("MATCH (n) WHERE elementId(n) = $eid RETURN n", eid=element_id).single()
        if record is None:
            raise EditError(NOT_FOUND, f"node {element_id} does not exist")
        node = record["n"]
        check_entity(node.labels, dict(node), element_id, allow_rule_made=allow_rule_made)
        if expected_id is not None and str(node.get("id")) != str(expected_id):
            raise EditError(STALE, f"node {element_id} is now {node.get('id')!r}, not {expected_id!r}")
        return node

    def snapshot(self, element_ids):
        snapshots = history.capture_snapshots(self.tx, element_ids)
        self.snapshots.extend(snapshots)
        return snapshots

    def bump(self, element_id, before_id):
        """手動の操作の印: _v を 1 増やし、_curated を true にする（設計メモ 2.3）。"""
        record = self.tx.run("MATCH (n) WHERE elementId(n) = $eid SET n._v = coalesce(n._v, 0) + 1, n._curated = true "
                             "RETURN n.id AS id, n._v AS v", eid=element_id).single()
        self.ids_after[before_id] = [record["id"], element_id]
        self.versions_after[record["id"]] = record["v"]
        return record["v"]

    def gone(self, before_id):
        self.ids_after[before_id] = None

    def finish(self, summary):
        before, after = self.lt.before_after()
        history.write_op(self.tx, op_id=self.op_id, seq=self.seq, op=self.op, user=self.user, at=now_iso(),
                         summary=summary, payload=self.payload, snapshots=self.snapshots, ledger_before=before,
                         ledger_after=after, ids_after=self.ids_after, versions_after=self.versions_after,
                         undo_of=self.undo_of)
        return self.op_id

    def fresh_node(self, element_id):
        return node_dict(self.tx.run("MATCH (n) WHERE elementId(n) = $eid RETURN n", eid=element_id).single()["n"])


def _edit(graph, op, user, payload, body):
    """排他を取って body(edit) を 1 つの書き込みトランザクションで行い、文書の件数を数え直す。"""
    ensure_schema(graph)

    def work(tx):
        edit = Edit(tx, op, user, payload)
        edit.lock()
        result = body(edit)
        return result, edit.docs

    result, docs = run_write(graph, work)
    update_counts(graph, docs)
    return result


def update_counts(graph, docs):
    access = graphDBdataAccess(graph)
    for file_name in sorted(d for d in docs if d):
        try:
            access.update_node_relationship_count(file_name)
        except Exception as e:  # 件数の更新に失敗しても、編集そのものは済んでいる
            logging.warning(f"graph_edit: could not update the counts of {file_name}: {e}")


def _read_node(graph, element_id):
    """トランザクションの前の読み取り（埋め込みの文を作るため）。"""
    rows = graph.query("MATCH (n) WHERE elementId(n) = $eid RETURN n.id AS id, n.description AS description",
                       {"eid": element_id})
    return rows[0] if rows else None


def _embed_one(text):
    vectors, _ = embed_texts([text])
    return vectors


def _write_entity_embedding(edit, element_id, used_text, vectors):
    """計算した文が今のノードの文と同じなら書く。違えば（計算の後で変わった）消す。"""
    record = edit.tx.run("MATCH (n) WHERE elementId(n) = $eid RETURN n.id AS id, n.description AS description",
                         eid=element_id).single()
    current_text = entity_text(record["id"], record["description"])
    index_dim = index_dimension(edit.tx.run)
    vector = vector_for(vectors, used_text, index_dim) if current_text == used_text else None
    write_embedding(edit.tx, element_id, vector)


def _sync_aliases(edit, entity_id, element_id, removed=()):
    """エンティティの aliases（表示と検索用）を、台帳の別名（target がこのエンティティ）にそろえる。
    規則で作ったノードの aliases は文書層も書くので、置き換えずに台帳の別名を足す（removed の別名だけ外す）。"""
    aliases = {row.get("alias") for row in edit.lt.find("alias", "target", [entity_id])
               if row.get("alias") and row.get("alias") != entity_id}
    record = edit.tx.run("MATCH (n) WHERE elementId(n) = $eid RETURN n.method AS method, n.aliases AS aliases",
                         eid=element_id).single()
    if record is not None and record["method"] == RULE_METHOD:
        aliases |= {a for a in (record["aliases"] or []) if isinstance(a, str) and a != entity_id and a not in set(removed or ())}
    aliases = sorted(aliases, key=lambda a: (norm_key(a), a))
    if aliases:
        edit.tx.run("MATCH (n) WHERE elementId(n) = $eid SET n.aliases = $aliases", eid=element_id, aliases=aliases)
    else:
        edit.tx.run("MATCH (n) WHERE elementId(n) = $eid REMOVE n.aliases", eid=element_id)


def _key_hits(tx, names, exclude_element_ids):
    """{名前: [要素 ID]}: 名前が同じか、正規化すると同じになるエンティティ（exclude を除く）。"""
    by_key = {}
    for name in names:
        by_key.setdefault(norm_key(name), []).append(name)
    exclude = set(exclude_element_ids)
    hits = {name: [] for name in names}
    for record in tx.run(f"MATCH (e:{ENTITY_LABEL}) WHERE e.id IS NOT NULL RETURN elementId(e) AS eid, e.id AS id"):
        if record["eid"] in exclude:
            continue
        for name in by_key.get(norm_key(str(record["id"])), ()):
            hits[name].append(record["eid"])
    return hits


def find_conflicts(tx, name, exclude_element_ids):
    """名前が同じか、正規化すると同じになるエンティティ（exclude を除く）。[{node, chunk_count, documents}]"""
    return node_hits(tx, _key_hits(tx, [name], exclude_element_ids)[name])


def refuse_same_key_entities(edit, names, exclude_element_ids):
    """別名か削除の記録を書く名前（names）と正規化キーが同じ、操作の対象以外の生きたエンティティがあれば CONFLICT。

    別名と削除の記録は正規化キーで抽出の名前に当たるので、書くと、そのエンティティの今後の抽出を奪う（読み替える・落とす）。
    同じものなら、まとめて統合（または一緒に削除）するよう促す。
    """
    names = [name for name in dict.fromkeys(names) if name]
    if not names:
        return
    hits = _key_hits(edit.tx, names, exclude_element_ids)
    holders = [name for name in names if hits[name]]
    if holders:
        eids = list(dict.fromkeys(eid for name in holders for eid in hits[name]))
        shown = "」「".join(holders)
        raise EditError(CONFLICT, f"another entity has the same normalized name as {holders[0]!r}; merge them together first",
                        data={"conflicts": node_hits(edit.tx, eids), "names": holders,
                              "reason": f"「{shown}」と正規化後の名前が同一の別のエンティティがあります。この操作の記録により、"
                                        "そのエンティティの今後の抽出結果が読み替えられるか除外されるため、先に統合してください。"})


def refuse_alias_of_other(edit, name, own_element_ids):
    """名前 name が、操作の対象以外の生きたエンティティの別名（正規化キーが同じ）なら CONFLICT（黙って別名を奪わない）。"""
    row = edit.lt.get(ledger_rows.alias_uid(name))
    if row is None or not row.get("target"):
        return
    record = edit.tx.run(f"MATCH (e:{ENTITY_LABEL} {{id: $id}}) RETURN elementId(e) AS eid", id=row["target"]).single()
    if record is None or record["eid"] in set(own_element_ids):
        return
    raise EditError(CONFLICT, f"{name!r} is an alias of {row['target']!r}; merge into it instead",
                    data={"conflicts": node_hits(edit.tx, [record["eid"]]), "alias_of": row["target"],
                          "reason": f"「{name}」は「{row['target']}」の別名です（修正台帳）。同一のエンティティであれば「{row['target']}」に統合してください。"})


def node_hits(tx, element_ids):
    if not element_ids:
        return []
    result = tx.run("""
        UNWIND $eids AS eid
        MATCH (n) WHERE elementId(n) = eid
        CALL (n) {
            OPTIONAL MATCH (n)<-[:HAS_ENTITY]-(c:Chunk)
            OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
            RETURN count(DISTINCT c) AS chunk_count, [x IN collect(DISTINCT d.fileName) WHERE x IS NOT NULL] AS documents
        }
        RETURN n, chunk_count, documents""", eids=list(element_ids))
    return [{"node": node_dict(r["n"]), "chunk_count": r["chunk_count"], "documents": sorted(r["documents"])}
            for r in result]


def _user(user):
    return optional_text(user) or "unknown"


def _set_type_labels(tx, element_id, current_labels, labels):
    remove = [label for label in type_labels_of(current_labels) if label not in labels]
    add = [label for label in labels if label not in current_labels]
    if remove:
        tx.run("MATCH (n) WHERE elementId(n) = $eid CALL apoc.create.removeLabels(n, $labels) YIELD node RETURN count(node)",
               eid=element_id, labels=remove)
    if add:
        tx.run("MATCH (n) WHERE elementId(n) = $eid CALL apoc.create.addLabels(n, $labels) YIELD node RETURN count(node)",
               eid=element_id, labels=add)


# ---- M1 改名 ----

def rename(graph, element_id, expected_id, new_id, user=None):
    element_id = require_text(element_id, "element_id")
    expected_id = require_text(expected_id, "expected_id")
    new_id = require_text(new_id, "new_id")
    user = _user(user)
    before = _read_node(graph, element_id)
    used_text = entity_text(new_id, before["description"]) if before else None
    vectors = _embed_one(used_text) if before else {}

    def body(edit):
        node = edit.entity(element_id, expected_id)
        old_id = node.get("id")
        if new_id == old_id:
            raise EditError(INVALID, "new_id is the same as the current id")
        conflicts = find_conflicts(edit.tx, new_id, [element_id])
        if conflicts:
            raise EditError(CONFLICT, f"an entity named {new_id!r} (or the same after normalization) already exists",
                            data={"conflicts": conflicts})
        refuse_alias_of_other(edit, new_id, [element_id])
        refuse_same_key_entities(edit, [old_id], [element_id])  # 旧い名前は別名になる
        edit.snapshot([element_id])
        edit.docs |= history.entity_documents(edit.tx, [element_id])
        edit.tx.run("MATCH (n) WHERE elementId(n) = $eid SET n.id = $new_id", eid=element_id, new_id=new_id)
        ledger_rows.rename_entity(edit.lt, old_id, new_id)
        _sync_aliases(edit, new_id, element_id)
        edit.bump(element_id, old_id)
        _write_entity_embedding(edit, element_id, used_text, vectors)
        op_id = edit.finish(f"「{old_id}」を「{new_id}」に改名")
        return {"node": edit.fresh_node(element_id), "op_id": op_id}

    return _edit(graph, "rename", user, {"element_id": element_id, "expected_id": expected_id, "new_id": new_id}, body)


# ---- M2 種類の変更 ----

def set_labels(graph, element_id, expected_id, labels, user=None):
    element_id = require_text(element_id, "element_id")
    expected_id = require_text(expected_id, "expected_id")
    requested = parse_json(labels, "labels", list)
    user = _user(user)

    def body(edit):
        node = edit.entity(element_id, expected_id)
        current = list(node.labels)
        new_labels = validate_type_labels(requested, current)
        edit.snapshot([element_id])
        _set_type_labels(edit.tx, element_id, current, new_labels)
        entity_id = node.get("id")
        edit.lt.put("type_override", ledger_rows.type_uid(entity_id), entity=entity_id, labels=new_labels)
        edit.bump(element_id, entity_id)
        op_id = edit.finish(f"「{entity_id}」のラベルを {'・'.join(new_labels)} に変更")
        return {"node": edit.fresh_node(element_id), "op_id": op_id}

    return _edit(graph, "set_labels", user, {"element_id": element_id, "expected_id": expected_id, "labels": requested},
                 body)


# ---- M3 プロパティの修正 ----

def update_props(graph, element_id, expected_id, props, user=None):
    element_id = require_text(element_id, "element_id")
    expected_id = require_text(expected_id, "expected_id")
    changes = validate_entity_props(parse_json(props, "props", dict))
    user = _user(user)
    used_text = entity_text(expected_id, changes.get("description")) if "description" in changes else None
    vectors = _embed_one(used_text) if used_text is not None else {}

    def body(edit):
        node = edit.entity(element_id, expected_id)
        entity_id = node.get("id")
        edit.snapshot([element_id])
        to_set = {k: v for k, v in changes.items() if v is not None}
        to_remove = [k for k, v in changes.items() if v is None]
        if to_set:
            edit.tx.run("MATCH (n) WHERE elementId(n) = $eid SET n += $props", eid=element_id, props=to_set)
        if to_remove:
            edit.tx.run("MATCH (n) WHERE elementId(n) = $eid CALL apoc.create.removeProperties(n, $keys) YIELD node "
                        "RETURN count(node)", eid=element_id, keys=to_remove)
        current = dict(edit.tx.run("MATCH (n) WHERE elementId(n) = $eid RETURN apoc.map.removeKey(properties(n), 'embedding') AS p",
                                   eid=element_id).single()["p"])
        ledger_rows.update_curated(edit.lt, entity_id, list(changes), current)
        edit.bump(element_id, entity_id)
        if used_text is not None:
            _write_entity_embedding(edit, element_id, used_text, vectors)
        op_id = edit.finish(f"「{entity_id}」のプロパティ {'・'.join(sorted(changes))} を修正")
        return {"node": edit.fresh_node(element_id), "op_id": op_id}

    return _edit(graph, "update_props", user, {"element_id": element_id, "expected_id": expected_id, "props": changes},
                 body)


# ---- M4 統合 ----

def _merged_description(mode, descriptions, custom):
    """統合の後の説明。descriptions は [target]+sources の順。target は統合先の説明（無ければ統合で残る説明。
    apoc.refactor.mergeNodes の properties:'discard' と同じく、最初にある説明）、concat はすべての説明（重複を除く）を改行でつなぐ。"""
    present = [d for d in descriptions if d not in (None, "")]
    if mode == "custom":
        return custom
    if mode == "concat":
        unique = list(dict.fromkeys(str(d) for d in present))
        return "\n".join(unique) if unique else None
    return present[0] if present else None


def merge(graph, target_element_id, target_expected_id, sources, keep_id, labels, description_mode="target",
          description=None, user=None):
    target_element_id = require_text(target_element_id, "target_element_id")
    target_expected_id = require_text(target_expected_id, "target_expected_id")
    source_refs = parse_node_refs(sources, "sources")
    keep_id = require_text(keep_id, "keep_id")
    requested_labels = parse_json(labels, "labels", list)
    mode = optional_text(description_mode) or "target"
    if mode not in DESCRIPTION_MODES:
        raise EditError(INVALID, f"description_mode must be one of {', '.join(DESCRIPTION_MODES)}")
    custom = description.strip() if isinstance(description, str) else None
    if mode == "custom" and not custom:
        raise EditError(INVALID, "description is required when description_mode is custom")
    if not source_refs:
        raise EditError(INVALID, "at least one source is required")
    source_ids = [ref["element_id"] for ref in source_refs]
    if len(set(source_ids)) != len(source_ids) or target_element_id in source_ids:
        raise EditError(INVALID, "the target and the sources must be different nodes")
    user = _user(user)

    before = [_read_node(graph, eid) for eid in [target_element_id] + source_ids]
    used_text = None
    vectors = {}
    if all(before):
        used_text = entity_text(keep_id, _merged_description(mode, [b["description"] for b in before], custom))
        vectors = _embed_one(used_text)

    def body(edit):
        target = edit.entity(target_element_id, target_expected_id, allow_rule_made=True)
        nodes = [edit.entity(ref["element_id"], ref["expected_id"]) for ref in source_refs]
        all_nodes = [target] + nodes
        current_labels = {label for node in all_nodes for label in node.labels}
        new_labels = validate_type_labels(requested_labels, current_labels)
        if is_rule_made(dict(target)) and (keep_id != target.get("id")
                                           or sorted(new_labels) != sorted(type_labels_of(target.labels))):
            # 規則で作ったノードは統合先にだけでき、名前と種類はそのまま残す（後処理が同じ名前で作り直すため）
            raise EditError(RESERVED, "a rule-made entity keeps its name and labels when it is the merge target",
                            data={"reason": "規則により生成したノード（文書番号・型式）を統合先にする場合は、名前とラベルを変更できません。"})
        merged_eids = [target_element_id] + source_ids
        conflicts = find_conflicts(edit.tx, keep_id, merged_eids)
        if conflicts:
            raise EditError(CONFLICT, f"an entity named {keep_id!r} (or the same after normalization) already exists",
                            data={"conflicts": conflicts})
        final_description = _merged_description(mode, [n.get("description") for n in all_nodes], custom)
        target_old_id = target.get("id")
        source_entity_ids = [n.get("id") for n in nodes]
        refuse_alias_of_other(edit, keep_id, merged_eids)
        # 統合で使われなくなる名前は別名になる
        refuse_same_key_entities(edit, source_entity_ids + ([target_old_id] if keep_id != target_old_id else []), merged_eids)
        edit.snapshot(merged_eids)
        edit.docs |= history.entity_documents(edit.tx, merged_eids)
        edit.tx.run("""
            MATCH (t) WHERE elementId(t) = $target
            UNWIND $sources AS sid
            MATCH (s) WHERE elementId(s) = sid
            WITH t, collect(s) AS sources
            CALL apoc.refactor.mergeNodes([t] + sources, $config) YIELD node
            RETURN count(node)""", target=target_element_id, sources=source_ids, config=MERGE_CONFIG)
        edit.tx.run("MATCH (n)-[r]->(n) WHERE elementId(n) = $eid DELETE r", eid=target_element_id)
        if keep_id != target_old_id:
            edit.tx.run("MATCH (n) WHERE elementId(n) = $eid SET n.id = $id", eid=target_element_id, id=keep_id)
        merged = edit.tx.run("MATCH (n) WHERE elementId(n) = $eid RETURN n", eid=target_element_id).single()["n"]
        _set_type_labels(edit.tx, target_element_id, list(merged.labels), new_labels)
        if final_description in (None, ""):
            edit.tx.run("MATCH (n) WHERE elementId(n) = $eid REMOVE n.description", eid=target_element_id)
        else:
            edit.tx.run("MATCH (n) WHERE elementId(n) = $eid SET n.description = $d", eid=target_element_id,
                        d=final_description)
        current = dict(edit.tx.run("MATCH (n) WHERE elementId(n) = $eid RETURN apoc.map.removeKey(properties(n), 'embedding') AS p",
                                   eid=target_element_id).single()["p"])
        used_names = source_entity_ids + ([target_old_id] if keep_id != target_old_id else [])
        ledger_rows.merge_entities(edit.lt, used_names, keep_id, new_labels,
                                   ["description"] if mode in ("concat", "custom") else [], current)
        _sync_aliases(edit, keep_id, target_element_id)
        edit.bump(target_element_id, target_old_id)
        for entity_id in source_entity_ids:
            edit.gone(entity_id)
        if used_text is not None:
            _write_entity_embedding(edit, target_element_id, used_text, vectors)
        else:
            write_embedding(edit.tx, target_element_id, None)
        names = "」「".join(source_entity_ids)
        op_id = edit.finish(f"「{names}」を「{target_old_id}」に統合"
                            + (f"し「{keep_id}」に改名" if keep_id != target_old_id else "")
                            + f"（{len(all_nodes)} 件）")
        return {"node": edit.fresh_node(target_element_id), "op_id": op_id, "merged_ids": source_entity_ids}

    payload = {"target_element_id": target_element_id, "target_expected_id": target_expected_id, "sources": source_refs,
               "keep_id": keep_id, "labels": requested_labels, "description_mode": mode, "description": custom}
    return _edit(graph, "merge", user, payload, body)


# ---- M5 エンティティの削除 ----

def delete_nodes(graph, nodes, suppress=True, user=None):
    refs = parse_node_refs(nodes, "nodes")
    if not refs:
        raise EditError(INVALID, "at least one node is required")
    element_ids = [ref["element_id"] for ref in refs]
    if len(set(element_ids)) != len(element_ids):
        raise EditError(INVALID, "the same node is listed twice")
    suppress = parse_bool(suppress, True)
    user = _user(user)

    def body(edit):
        found = [edit.entity(ref["element_id"], ref["expected_id"]) for ref in refs]
        ids = [node.get("id") for node in found]
        if suppress:  # 名前と別名が削除の記録になる
            alias_names = [row.get("alias") for row in edit.lt.find("alias", "target", ids)]
            refuse_same_key_entities(edit, ids + alias_names, element_ids)
        edit.snapshot(element_ids)
        edit.docs |= history.entity_documents(edit.tx, element_ids)
        edit.tx.run("UNWIND $eids AS eid MATCH (n) WHERE elementId(n) = eid DETACH DELETE n", eids=element_ids)
        for entity_id in ids:
            ledger_rows.delete_entity(edit.lt, entity_id, suppress)
            edit.gone(entity_id)
        shown = "、".join(ids[:5]) + (f" ほか {len(ids) - 5} 件" if len(ids) > 5 else "")
        op_id = edit.finish(f"エンティティ {len(ids)} 件を削除（{shown}）" + ("。再抽出されても作成しない" if suppress else ""))
        return {"deleted_ids": ids, "op_id": op_id}

    return _edit(graph, "delete_nodes", user, {"nodes": refs, "suppress": suppress}, body)


# ---- M6〜M8 リレーション ----

def _relationship(edit, element_id, expected):
    record = edit.tx.run("MATCH (a)-[r]->(b) WHERE elementId(r) = $rid RETURN r, a, b", rid=element_id).single()
    if record is None:
        raise EditError(NOT_FOUND, f"relationship {element_id} does not exist")
    rel, a, b = record["r"], record["a"], record["b"]
    if is_reserved_relationship_type(rel.type):
        raise EditError(RESERVED, f"relationship type {rel.type} is reserved")
    if rel.get("method") == RULE_METHOD:
        raise EditError(RESERVED, f"relationship {element_id} was made by a rule",
                        data={"reason": "規則により生成したリレーションは手動で変更できません（後処理で再生成されます）。"})
    for node in (a, b):
        check_entity(node.labels, dict(node), node.element_id, allow_rule_made=True)
    if (str(a.get("id")), rel.type, str(b.get("id"))) != (expected["src"], expected["type"], expected["tgt"]):
        raise EditError(STALE, f"relationship {element_id} is now ({a.get('id')})-[{rel.type}]->({b.get('id')})")
    return rel, a, b


def _rel_text(src, rel_type, tgt):
    return f"{src} -[{rel_type}]-> {tgt}"


def create_relationship(graph, source_element_id, source_expected_id, target_element_id, target_expected_id, type,
                        props=None, user=None):
    source_element_id = require_text(source_element_id, "source_element_id")
    source_expected_id = require_text(source_expected_id, "source_expected_id")
    target_element_id = require_text(target_element_id, "target_element_id")
    target_expected_id = require_text(target_expected_id, "target_expected_id")
    rel_type = validate_relationship_type(type)
    rel_props = validate_relationship_props(parse_json(props, "props", dict, required=False), allow_null=True)
    rel_props = {k: v for k, v in rel_props.items() if v is not None}
    user = _user(user)
    if source_element_id == target_element_id:
        raise EditError(INVALID, "a relationship from an entity to itself cannot be created")

    def body(edit):
        a = edit.entity(source_element_id, source_expected_id, allow_rule_made=True)
        b = edit.entity(target_element_id, target_expected_id, allow_rule_made=True)
        edit.snapshot([source_element_id, target_element_id])
        rel = edit.tx.run("""
            MATCH (a) WHERE elementId(a) = $a
            MATCH (b) WHERE elementId(b) = $b
            CALL apoc.merge.relationship(a, $type, {}, $props, b, $props) YIELD rel
            RETURN rel""", a=source_element_id, b=target_element_id, type=rel_type, props=rel_props).single()["rel"]
        ledger_rows.add_manual_relationship(edit.lt, a.get("id"), rel_type, b.get("id"), rel_props)
        edit.bump(source_element_id, a.get("id"))
        edit.bump(target_element_id, b.get("id"))
        edit.docs |= history.entity_documents(edit.tx, [source_element_id, target_element_id])
        op_id = edit.finish(f"リレーション {_rel_text(a.get('id'), rel_type, b.get('id'))} を追加")
        fresh = edit.tx.run("MATCH ()-[r]->() WHERE elementId(r) = $rid RETURN r", rid=rel.element_id).single()["r"]
        return {"relationship": rel_dict(fresh), "op_id": op_id}

    payload = {"source_element_id": source_element_id, "source_expected_id": source_expected_id,
               "target_element_id": target_element_id, "target_expected_id": target_expected_id, "type": rel_type,
               "props": rel_props}
    return _edit(graph, "create_relationship", user, payload, body)


def delete_relationship(graph, element_id, expected, suppress=True, user=None):
    element_id = require_text(element_id, "element_id")
    expected = parse_expected_relationship(expected)
    suppress = parse_bool(suppress, True)
    user = _user(user)

    def body(edit):
        rel, a, b = _relationship(edit, element_id, expected)
        ends = list(dict.fromkeys([a.element_id, b.element_id]))
        edit.snapshot(ends)
        edit.docs |= history.entity_documents(edit.tx, ends)
        edit.tx.run("MATCH ()-[r]->() WHERE elementId(r) = $rid DELETE r", rid=element_id)
        ledger_rows.remove_relationship(edit.lt, a.get("id"), rel.type, b.get("id"), suppress)
        for node in ([a, b] if a.element_id != b.element_id else [a]):
            edit.bump(node.element_id, node.get("id"))
        op_id = edit.finish(f"リレーション {_rel_text(a.get('id'), rel.type, b.get('id'))} を削除"
                            + ("。再抽出されても作成しない" if suppress else ""))
        return {"op_id": op_id}

    return _edit(graph, "delete_relationship", user, {"element_id": element_id, "expected": expected, "suppress": suppress},
                 body)


def update_relationship(graph, element_id, expected, new_type=None, reverse=False, props=None, user=None):
    element_id = require_text(element_id, "element_id")
    expected = parse_expected_relationship(expected)
    new_type = optional_text(new_type)
    if new_type is not None:
        new_type = validate_relationship_type(new_type)
    reverse = parse_bool(reverse, False)
    changes = parse_json(props, "props", dict, required=False)
    if changes is not None:
        changes = validate_relationship_props(changes, allow_null=True)
    user = _user(user)

    def body(edit):
        rel, a, b = _relationship(edit, element_id, expected)
        self_loop = a.element_id == b.element_id
        type_change = new_type is not None and new_type != rel.type
        flip = reverse and not self_loop
        if not type_change and not flip and not changes:
            raise EditError(INVALID, "nothing to change")
        ends = list(dict.fromkeys([a.element_id, b.element_id]))
        edit.snapshot(ends)
        edit.docs |= history.entity_documents(edit.tx, ends)
        merged_props = history.jsonable(dict(rel))
        for key, value in (changes or {}).items():
            if value is None:
                merged_props.pop(key, None)
            else:
                merged_props[key] = value
        old_text = _rel_text(a.get("id"), rel.type, b.get("id"))
        if type_change or flip:
            start, end = (b, a) if flip else (a, b)
            rel_type = new_type or rel.type
            created = edit.tx.run("""
                MATCH (s) WHERE elementId(s) = $s
                MATCH (e) WHERE elementId(e) = $e
                CALL apoc.merge.relationship(s, $type, {}, $props, e, $props) YIELD rel
                RETURN rel""", s=start.element_id, e=end.element_id, type=rel_type, props=merged_props).single()["rel"]
            edit.tx.run("MATCH ()-[r]->() WHERE elementId(r) = $rid DELETE r", rid=element_id)
            ledger_rows.remove_relationship(edit.lt, a.get("id"), rel.type, b.get("id"), suppress=True)
            ledger_rows.add_manual_relationship(edit.lt, start.get("id"), rel_type, end.get("id"), merged_props)
            new_rid = created.element_id
            summary = f"リレーション {old_text} を {_rel_text(start.get('id'), rel_type, end.get('id'))} に変更"
        else:
            edit.tx.run("MATCH ()-[r]->() WHERE elementId(r) = $rid SET r = $props", rid=element_id, props=merged_props)
            ledger_rows.update_manual_relationship_props(edit.lt, a.get("id"), rel.type, b.get("id"), merged_props)
            new_rid = element_id
            summary = f"リレーション {old_text} のプロパティ {'・'.join(sorted(changes))} を修正"
        for node in ([a, b] if not self_loop else [a]):
            edit.bump(node.element_id, node.get("id"))
        op_id = edit.finish(summary)
        fresh = edit.tx.run("MATCH ()-[r]->() WHERE elementId(r) = $rid RETURN r", rid=new_rid).single()["r"]
        return {"relationship": rel_dict(fresh), "op_id": op_id}

    payload = {"element_id": element_id, "expected": expected, "new_type": new_type, "reverse": reverse, "props": changes}
    return _edit(graph, "update_relationship", user, payload, body)


# ---- M9 取り消し ----

def undo(graph, op_id, user=None):
    op_id = require_text(op_id, "op_id")
    user = _user(user)
    op = run_read(graph, lambda tx: history.load_op(tx.run, op_id))
    texts = [entity_text(s["id"], s["props"].get("description")) for s in op["snapshot"]]
    vectors, _ = embed_texts(texts)

    def body(edit):
        target = history.load_op(edit.tx.run, op_id)
        entity_state = history.check_undoable(edit.tx.run, target)
        edit.undo_of = op_id
        current_eids = [after[1] for after in target["ids_after"].values() if after]
        if current_eids:
            edit.snapshot(current_eids)  # 取り消しの直前の状態（記録用）
        index_dim = index_dimension(edit.tx.run)
        restored_ids, restored, docs, skipped = history.restore(edit.tx, target, vectors, index_dim, edit.lt)
        _refuse_restored_names_taken(edit, restored)
        edit.docs |= docs
        for snapshot in target["snapshot"]:
            after = target["ids_after"].get(snapshot["id"])
            record = edit.tx.run("MATCH (n) WHERE elementId(n) = $eid RETURN n._v AS v", eid=restored[snapshot["id"]]).single()
            before_id = after[0] if after else snapshot["id"]
            edit.ids_after[before_id] = [snapshot["id"], restored[snapshot["id"]]]
            edit.versions_after[snapshot["id"]] = record["v"]
        edit.tx.run(f"MATCH (o:{GRAPH_EDIT_OP_LABEL} {{op_id: $op_id}}) SET o.undone = true", op_id=op_id)
        undo_id = edit.finish(f"取り消し: {target['summary']}")
        if skipped:
            logging.info(f"graph_edit: undo {op_id} could not restore {skipped} relationships whose other end is gone")
        return {"op_id": undo_id, "restored_ids": restored_ids, "skipped_relationships": skipped}

    return _edit(graph, "undo", user, {"op_id": op_id}, body)


def _refuse_restored_names_taken(edit, restored):
    """戻したエンティティの名前（正規化キー）が、他の生きたエンティティの別名か、削除の記録に当たれば取り消さない
    （戻しても、そのエンティティの抽出が読み替えられるか落とされる）。トランザクションは取り消される。"""
    for entity_id, element_id in restored.items():
        alias = edit.lt.get(ledger_rows.alias_uid(entity_id))
        if alias is not None and alias.get("target") and alias["target"] != entity_id:
            record = edit.tx.run(f"MATCH (e:{ENTITY_LABEL} {{id: $id}}) RETURN elementId(e) AS eid", id=alias["target"]).single()
            if record is not None and record["eid"] != element_id and record["eid"] not in restored.values():
                raise EditError(NOT_UNDOABLE, f"{entity_id!r} is now an alias of {alias['target']!r}",
                                data={"reason": f"「{entity_id}」はその後「{alias['target']}」の別名として登録されました（修正台帳）。"})
        tomb = edit.lt.get(ledger_rows.tnode_uid(entity_id))
        if tomb is not None:
            raise EditError(NOT_UNDOABLE, f"{entity_id!r} is suppressed by {tomb.get('name')!r}",
                            data={"reason": f"「{entity_id}」と正規化後の名前が同一の削除の記録「{tomb.get('name')}」があります（修正台帳）。"})


def history_rows(graph, limit=50, before_seq=None):
    limit = parse_int(limit, 50, minimum=1, maximum=500, name="limit")
    before_seq = parse_int(before_seq, None, name="before_seq")
    return run_read(graph, lambda tx: history.list_history(tx, limit, before_seq))


# ---- M10 修正台帳の行・別物・既存の入口の置き換え ----

def ledger_delete(graph, uid, user=None):
    uid = require_text(uid, "uid")
    user = _user(user)

    def body(edit):
        row = edit.lt.get(uid)
        if row is None:
            raise EditError(NOT_FOUND, f"ledger row {uid} does not exist")
        holder = None
        if row.get("kind") == "alias" and row.get("target"):
            # 別名を消したら、代表のエンティティの aliases（表示と検索用）もそろえる。写しを取り、版を上げる
            holder = edit.tx.run(f"MATCH (e:{ENTITY_LABEL} {{id: $id}}) RETURN elementId(e) AS eid",
                                 id=row["target"]).single()
            if holder is not None:
                edit.snapshot([holder["eid"]])
        edit.lt.delete(uid)
        if holder is not None:
            _sync_aliases(edit, row["target"], holder["eid"], removed=[row.get("alias")])
            edit.bump(holder["eid"], row["target"])
        op_id = edit.finish(f"修正台帳の行（{uid}）を削除")
        return {"uid": uid, "op_id": op_id}

    return _edit(graph, "ledger_delete", user, {"uid": uid}, body)


def mark_distinct(graph, a_element_id, b_element_id, user=None):
    a_element_id = require_text(a_element_id, "a_element_id")
    b_element_id = require_text(b_element_id, "b_element_id")
    if a_element_id == b_element_id:
        raise EditError(INVALID, "a and b must be different nodes")
    user = _user(user)

    def body(edit):
        a = edit.entity(a_element_id, allow_rule_made=True)
        b = edit.entity(b_element_id, allow_rule_made=True)
        first, second = sorted([a.get("id"), b.get("id")], key=lambda x: (norm_key(x), x))
        uid = ledger_rows.dist_uid(first, second)
        edit.lt.put("distinct", uid, a=first, b=second)
        op_id = edit.finish(f"「{first}」と「{second}」を非同一として登録")
        return {"uid": uid, "op_id": op_id}

    return _edit(graph, "mark_distinct", user, {"a_element_id": a_element_id, "b_element_id": b_element_id}, body)


def merge_duplicate_rows_report(graph, duplicate_nodes_list, user=None):
    """/merge_duplicate_nodes の置き換え。各行を merge（target=first、keep_id=first の id、labels=first のラベル、
    description_mode=target）で処理する。(data, skipped) を返す。data は今と同じ [{"totalMerged": 統合したノードの数
    （first を含む）}]。skipped は飛ばした行 [{row（1 始まり）, first_element_id, reason（日本語）, error}]。

    行が重なる（A←B、B←C）と、前の行の統合で first か source が消える。そういう行は統合せずに飛ばして続ける
    （残りの source だけを統合しない。画面で選んだ組と違う統合になるため）。統合が名前の衝突などで断られた行も飛ばす。
    """
    rows = parse_json(duplicate_nodes_list, "duplicate_nodes_list", list)
    total = 0
    skipped = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise EditError(INVALID, f"row {index + 1} must be an object")
        first_eid = require_text(row.get("firstElementId"), "firstElementId")
        similar = list(dict.fromkeys(eid for eid in (row.get("similarElementIds") or [])
                                     if isinstance(eid, str) and eid != first_eid))

        def skip(reason, error=None):
            skipped.append({"row": index + 1, "first_element_id": first_eid, "reason": reason, "error": error})

        first = graph.query("MATCH (n) WHERE elementId(n) = $eid RETURN n.id AS id, labels(n) AS labels", {"eid": first_eid})
        if not first or first[0]["id"] is None:
            skip("統合先のエンティティが見つかりません（前の行で統合済み、または削除済みです）。")
            continue
        others = graph.query("UNWIND $eids AS eid MATCH (n) WHERE elementId(n) = eid RETURN eid, n.id AS id",
                             {"eids": similar})
        sources = [{"element_id": r["eid"], "expected_id": str(r["id"])} for r in others if r["id"] is not None]
        if len(sources) < len(similar):
            skip(f"類似エンティティ {len(similar) - len(sources)} 件が見つかりません（前の行で統合済み、または削除済みです）。")
            continue
        if not sources:
            continue
        try:
            merge(graph, first_eid, str(first[0]["id"]), sources, str(first[0]["id"]), type_labels_of(first[0]["labels"]),
                  "target", None, user)
        except EditError as e:
            if e.code == BUSY:  # 取り込みの保存の最中。以後の行も同じなので止める
                raise EditError(e.code, f"{e.detail} (row {index + 1} of {len(rows)}, {total} nodes merged before)", e.data)
            skip((e.data or {}).get("reason") or f"統合できませんでした（{e.code}）。", e.error_text())
            continue
        total += 1 + len(sources)
    if skipped:
        logging.info(f"graph_edit: merge_duplicate_rows skipped {len(skipped)} of {len(rows)} rows: {skipped}")
    return [{"totalMerged": total}], skipped


def merge_duplicate_rows(graph, duplicate_nodes_list, user=None):
    """merge_duplicate_rows_report の data だけを返す。"""
    data, _ = merge_duplicate_rows_report(graph, duplicate_nodes_list, user)
    return data


def delete_unconnected(graph, unconnected_entities_list, user=None):
    """/delete_unconnected_nodes の置き換え。delete_nodes（suppress=false）で消す。編集の対象でないノード
    （予約ラベル・文書・__Entity__ の無いノード・規則で作ったノード）は消さずに skipped に返す。"""
    element_ids = [str(eid).strip() for eid in parse_json(unconnected_entities_list, "unconnected_entities_list", list)]
    rows = graph.query("UNWIND $eids AS eid MATCH (n) WHERE elementId(n) = eid "
                       "RETURN eid, labels(n) AS labels, n.id AS id, n.fileName AS fileName, n.method AS method",
                       {"eids": element_ids})
    refs, skipped = [], []
    found = {r["eid"] for r in rows}
    for r in rows:
        block = entity_block(r["labels"], {"id": r["id"], "fileName": r["fileName"], "method": r["method"]})
        if block is None:
            refs.append({"element_id": r["eid"], "expected_id": str(r["id"])})
        else:
            skipped.append({"element_id": r["eid"], "id": r["id"], "reason": block[1]})
    skipped.extend({"element_id": eid, "id": None, "reason": "ノードが見つかりません。"} for eid in element_ids if eid not in found)
    if not refs:
        return {"deleted_ids": [], "skipped": skipped, "op_id": None}
    result = delete_nodes(graph, refs, False, user)
    return {"deleted_ids": result["deleted_ids"], "skipped": skipped, "op_id": result["op_id"]}
