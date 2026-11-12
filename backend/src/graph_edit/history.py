"""操作の写し・操作の履歴・取り消し（設計メモ 2.5、M9）。

写し（snapshot）は、操作が触れたエンティティごとに操作の前の状態を持つ:
    {id, labels, props(embedding を除く), rels: [{dir, type, other_id, other_is_chunk, other_labels, props}], chunk_ids}
相手はエンティティの id（文書は fileName）かチャンクの id で持つ（要素 ID は統合や削除で変わるため使わない）。
rels にはチャンクからの HAS_ENTITY を入れず、その元のチャンクの id を chunk_ids に持つ。

操作の履歴（__GraphEditOp__）には、設計メモ 2.5 の項目に加えて次を持つ:
    ids_after_json: {写しの id: [操作の後の id, 操作の後の要素 ID] または null（操作の後に無い）}
    touched: 触れたエンティティの id（操作の前と後）。後の操作が同じエンティティに触れたかを調べるため

取り消しは、写しの状態と台帳の前の行に戻す（どの操作でも同じ処理）:
    - 操作の後にあるエンティティはその場で戻す（ラベル・プロパティ。要素 ID は変わらない）。無ければ作り直す
    - リレーションと HAS_ENTITY は、写しの一覧と今の一覧を比べ、違うものだけを消す・作る・プロパティを戻す。
      相手は id で引き、無ければ台帳の別名をたどる（後で改名・統合された相手にもつなぐ）。相手もチャンクも無ければ戻さない
    - 戻したエンティティは _v を 1 増やす。埋め込みは作り直す
取り消せるのは、触れたエンティティの今の _v と要素 ID が操作の直後と同じで、後の操作が同じエンティティに触れておらず、
台帳の行が操作の直後のままで、戻す名前を他のエンティティが使っていないときだけ。
"""
import json
from collections import defaultdict

from neo4j.time import Date, DateTime, Duration, Time

from src.graph_edit.embedding import entity_text, vector_for, write_embedding
from src.graph_edit.ledger import alias_uid
from src.graph_edit.validate import (
    ENTITY_LABEL, GRAPH_EDIT_LABEL, GRAPH_EDIT_OP_LABEL, NOT_FOUND, NOT_UNDOABLE, EditError,
)

UNDO_OP = "undo"


def jsonable(value):
    """JSON に書ける形（Neo4j の日時は ISO の文字列にする）。"""
    if isinstance(value, (DateTime, Date, Time)):
        return value.isoformat()
    if isinstance(value, Duration):
        return str(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def dumps(value):
    return json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True)


# ---- 写し ----

SNAPSHOT_QUERY = """
UNWIND $eids AS eid
MATCH (e) WHERE elementId(e) = eid
RETURN eid, e.id AS id, labels(e) AS labels, apoc.map.removeKey(properties(e), 'embedding') AS props,
       [(e)-[r]->(o) | {dir: 'out', type: type(r), props: properties(r), other_id: coalesce(o.id, o.fileName),
                       other_labels: labels(o), other_is_chunk: o:Chunk}] AS outs,
       [(e)<-[r]-(o) WHERE o <> e | {dir: 'in', type: type(r), props: properties(r), other_id: coalesce(o.id, o.fileName),
                       other_labels: labels(o), other_is_chunk: o:Chunk}] AS ins
"""


def capture_snapshots(tx, element_ids):
    """要素 ID のエンティティの写し（並びは element_ids の順）。"""
    records = {record["eid"]: record for record in tx.run(SNAPSHOT_QUERY, eids=list(element_ids))}
    snapshots = []
    for eid in element_ids:
        record = records.get(eid)
        if record is None:
            raise EditError(NOT_FOUND, f"node {eid} does not exist")
        rels, chunk_ids = [], []
        for rel in list(record["outs"]) + list(record["ins"]):
            if rel["dir"] == "in" and rel["type"] == "HAS_ENTITY" and rel["other_is_chunk"]:
                chunk_ids.append(rel["other_id"])
                continue
            rels.append({"dir": rel["dir"], "type": rel["type"], "other_id": jsonable(rel["other_id"]),
                         "other_is_chunk": bool(rel["other_is_chunk"]), "other_labels": list(rel["other_labels"]),
                         "props": jsonable(dict(rel["props"]))})
        rels.sort(key=lambda r: (r["dir"], r["type"], str(r["other_id"]), dumps(r["props"])))
        snapshots.append({"id": record["id"], "labels": sorted(record["labels"]), "props": jsonable(dict(record["props"])),
                          "rels": rels, "chunk_ids": sorted({str(c) for c in chunk_ids if c is not None})})
    return snapshots


def entity_documents(tx, element_ids):
    """エンティティを HAS_ENTITY で指すチャンクの文書名（件数の更新の対象）。"""
    if not element_ids:
        return set()
    result = tx.run("""
        UNWIND $eids AS eid
        MATCH (e) WHERE elementId(e) = eid
        MATCH (e)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d:Document)
        RETURN DISTINCT d.fileName AS fileName""", eids=list(element_ids))
    return {record["fileName"] for record in result if record["fileName"]}


# ---- 操作の履歴 ----

def write_op(tx, *, op_id, seq, op, user, at, summary, payload, snapshots, ledger_before, ledger_after,
             ids_after, versions_after, undo_of=None):
    touched = sorted({s["id"] for s in snapshots} | {v[0] for v in ids_after.values() if v})
    tx.run(f"""
        CREATE (o:{GRAPH_EDIT_OP_LABEL} {{op_id: $op_id}})
        SET o.seq = $seq, o.op = $op, o.user = $user, o.at = $at, o.summary = $summary,
            o.payload_json = $payload, o.snapshot_json = $snapshot, o.ledger_before_json = $ledger_before,
            o.ledger_after_json = $ledger_after, o.versions_after_json = $versions_after,
            o.ids_after_json = $ids_after, o.touched = $touched, o.undone = false, o.undo_of = $undo_of""",
           op_id=op_id, seq=seq, op=op, user=user, at=at, summary=summary, payload=dumps(payload),
           snapshot=dumps(snapshots), ledger_before=dumps(ledger_before), ledger_after=dumps(ledger_after),
           versions_after=dumps(versions_after), ids_after=dumps(ids_after), touched=touched, undo_of=undo_of)


def parse_op(props):
    """__GraphEditOp__ のプロパティを読みやすい形にする。"""
    def load(key, default):
        try:
            value = json.loads(props.get(key) or "null")
        except ValueError:
            value = None
        return default if value is None else value
    return {
        "op_id": props.get("op_id"), "seq": props.get("seq"), "op": props.get("op"), "user": props.get("user"),
        "at": props.get("at"), "summary": props.get("summary"), "undone": bool(props.get("undone")),
        "undo_of": props.get("undo_of"), "touched": list(props.get("touched") or []),
        "payload": load("payload_json", {}), "snapshot": load("snapshot_json", []),
        "ledger_before": load("ledger_before_json", {}), "ledger_after": load("ledger_after_json", {}),
        "versions_after": load("versions_after_json", {}), "ids_after": load("ids_after_json", {}),
    }


def lookup_states(run, entity_ids, uids):
    """今のエンティティ（{id: {eid, v}}）と台帳の行（{uid: 行}）。run は tx.run など。"""
    entity_state = {}
    if entity_ids:
        for record in run(f"""UNWIND $ids AS id MATCH (e:{ENTITY_LABEL} {{id: id}})
                             RETURN id, elementId(e) AS eid, e._v AS v""", ids=sorted(set(entity_ids))):
            entity_state[record["id"]] = {"eid": record["eid"], "v": record["v"]}
    ledger_state = {}
    if uids:
        for record in run(f"""UNWIND $uids AS uid MATCH (g:{GRAPH_EDIT_LABEL} {{uid: uid}})
                             RETURN uid, properties(g) AS p""", uids=sorted(set(uids))):
            ledger_state[record["uid"]] = jsonable(dict(record["p"]))
    return entity_state, ledger_state


def later_ops(run, ops):
    """各操作より後に、同じエンティティに触れた操作の seq（無ければ None）。{op_id: seq}"""
    rows = [{"op_id": op["op_id"], "seq": op["seq"], "ids": op["touched"]} for op in ops if op["touched"]]
    out = {op["op_id"]: None for op in ops}
    if not rows:
        return out
    result = run(f"""
        UNWIND $rows AS row
        OPTIONAL MATCH (o:{GRAPH_EDIT_OP_LABEL})
        WHERE o.seq > row.seq AND any(x IN coalesce(o.touched, []) WHERE x IN row.ids)
        RETURN row.op_id AS op_id, min(o.seq) AS later""", rows=rows)
    for record in result:
        out[record["op_id"]] = record["later"]
    return out


def ids_to_check(op):
    ids = {s["id"] for s in op["snapshot"]}
    ids |= {v[0] for v in op["ids_after"].values() if v}
    return ids


def undo_block_reason(op, entity_state, ledger_state, later_seq):
    """取り消せないときの日本語の理由。取り消せるなら None。"""
    if op["undone"]:
        return "取り消し済みです。"
    if op["op"] == UNDO_OP:
        return "取り消しの操作は取り消せません。元に戻すには、同じ操作をもう一度行ってください。"
    if later_seq is not None:
        return f"後の操作（番号 {later_seq}）が同じエンティティを変更しました（後の操作を取り消しても、この操作は取り消せません）。"
    versions = op["versions_after"]
    # 操作の後のノード（その場で戻すもの）。統合で source の名前を残したときは、その名前をこのノードが持っている
    after_eids = {after[1] for after in op["ids_after"].values() if after}
    for snapshot in op["snapshot"]:
        after = op["ids_after"].get(snapshot["id"])
        if after:
            id_after, eid_after = after
            state = entity_state.get(id_after)
            if state is None:
                return f"「{id_after}」が見つかりません（その後に削除・統合・改名されたか、文書の削除に伴い削除されました）。"
            if state["eid"] != eid_after or state["v"] != versions.get(id_after):
                return f"その後に「{id_after}」が変更されました（取り込みの保存などで版が {versions.get(id_after)} から {state['v']} になりました）。"
        holder = entity_state.get(snapshot["id"])
        if (not after or after[0] != snapshot["id"]) and holder is not None and holder["eid"] not in after_eids:
            return f"同じ名前「{snapshot['id']}」のエンティティがその後に作成されました。"
    for uid, row in op["ledger_after"].items():
        if ledger_state.get(uid) != row:
            return f"修正台帳の行（{uid}）がその後に変更されました。"
    return None


def load_op(run, op_id):
    record = run(f"MATCH (o:{GRAPH_EDIT_OP_LABEL} {{op_id: $op_id}}) RETURN properties(o) AS p", op_id=op_id).single()
    if record is None:
        raise EditError(NOT_FOUND, f"operation {op_id} does not exist")
    return parse_op(dict(record["p"]))


def check_undoable(run, op):
    entity_state, ledger_state = lookup_states(run, ids_to_check(op), list(op["ledger_after"]))
    later = later_ops(run, [op])[op["op_id"]]
    reason = undo_block_reason(op, entity_state, ledger_state, later)
    if reason is not None:
        raise EditError(NOT_UNDOABLE, f"operation {op['op_id']} cannot be undone", data={"reason": reason})
    return entity_state


def list_history(tx, limit, before_seq):
    """新しい順の操作の履歴。各行に取り消せるかと、取り消せないときの理由。"""
    ops = [parse_op(dict(record["p"])) for record in tx.run(f"""
        MATCH (o:{GRAPH_EDIT_OP_LABEL}) WHERE $before IS NULL OR o.seq < $before
        RETURN properties(o) AS p ORDER BY o.seq DESC LIMIT $limit""", limit=limit, before=before_seq)]
    entity_ids, uids = set(), set()
    for op in ops:
        entity_ids |= ids_to_check(op)
        uids |= set(op["ledger_after"])
    entity_state, ledger_state = lookup_states(tx.run, entity_ids, uids)
    later = later_ops(tx.run, ops)
    out = []
    for op in ops:
        reason = undo_block_reason(op, entity_state, ledger_state, later.get(op["op_id"]))
        out.append({"op_id": op["op_id"], "seq": op["seq"], "op": op["op"], "user": op["user"], "at": op["at"],
                    "summary": op["summary"], "undone": op["undone"], "undo_of": op["undo_of"],
                    "undoable": reason is None, "reason": reason or ""})
    return out


# ---- 取り消し（写しに戻す）----

def _other_kind(rel):
    labels = rel.get("other_labels") or []
    if rel.get("other_is_chunk") or "Chunk" in labels:
        return "chunk"
    if ENTITY_LABEL in labels:
        return "entity"
    if "__Community__" in labels:
        return "community"
    if "Document" in labels:
        return "document"
    return None


_RESOLVE_QUERIES = {
    "chunk": "UNWIND $ids AS id MATCH (o:Chunk {id: id}) RETURN id, elementId(o) AS eid",
    "community": "UNWIND $ids AS id MATCH (o:`__Community__` {id: id}) RETURN id, elementId(o) AS eid",
    "document": "UNWIND $ids AS id MATCH (o:Document {fileName: id}) RETURN id, elementId(o) AS eid",
    "entity": f"UNWIND $ids AS id MATCH (o:{ENTITY_LABEL} {{id: id}}) RETURN id, elementId(o) AS eid",
}


def _resolve(tx, kind, ids):
    if not ids:
        return {}
    found = {}
    for record in tx.run(_RESOLVE_QUERIES[kind], ids=sorted(ids)):
        found.setdefault(record["id"], record["eid"])
    if kind == "entity":
        # 見つからない相手は、台帳の別名をたどる（後で改名・統合された相手）
        missing = {i: i for i in ids if i not in found}
        for _ in range(5):
            if not missing:
                break
            uids = {alias_uid(name): original for original, name in missing.items()}
            targets = {}
            for record in tx.run(f"UNWIND $uids AS uid MATCH (g:{GRAPH_EDIT_LABEL} {{uid: uid, kind: 'alias'}}) "
                                 "RETURN uid, g.target AS target", uids=sorted(uids)):
                targets[uids[record["uid"]]] = record["target"]
            if not targets:
                break
            hits = {}
            for record in tx.run(_RESOLVE_QUERIES["entity"], ids=sorted(set(targets.values()))):
                hits[record["id"]] = record["eid"]
            next_missing = {}
            for original, target in targets.items():
                if target in hits:
                    found[original] = hits[target]
                elif target != missing[original]:
                    next_missing[original] = target
            missing = next_missing
    return found


def restore(tx, op, vectors, index_dim, lt):
    """操作 op の写しと台帳の前の行に戻す。戻したエンティティの id、件数の更新の対象の文書、戻せなかった数を返す。"""
    versions = op["versions_after"]
    snapshots = op["snapshot"]
    restored = {}  # 写しの id -> 要素 ID
    docs = set()
    # その場で戻すもの（操作の後にあるノード）を先に、作り直すものを後にする（統合で source の名前を残したとき、
    # 先に統合先の名前を戻さないと、作り直す source の名前が一意制約に当たる）
    ordered = sorted(snapshots, key=lambda s: 0 if op["ids_after"].get(s["id"]) else 1)
    for snapshot in ordered:
        after = op["ids_after"].get(snapshot["id"])
        props = dict(snapshot["props"])
        previous_v = versions.get(after[0]) if after else None
        props["_v"] = max(int(previous_v or 0), int(props.get("_v") or 0)) + 1
        labels = list(snapshot["labels"])
        if after:
            eid = after[1]
            docs |= entity_documents(tx, [eid])
            current = tx.run("MATCH (e) WHERE elementId(e) = $eid RETURN labels(e) AS labels", eid=eid).single()
            if current is None:
                raise EditError(NOT_UNDOABLE, f"node {eid} disappeared", data={"reason": "対象のエンティティが見つかりません。"})
            tx.run("MATCH (e) WHERE elementId(e) = $eid SET e = $props", eid=eid, props=props)
            remove = [label for label in current["labels"] if label not in labels]
            add = [label for label in labels if label not in current["labels"]]
            if remove:
                tx.run("MATCH (e) WHERE elementId(e) = $eid CALL apoc.create.removeLabels(e, $labels) YIELD node RETURN count(node)",
                       eid=eid, labels=remove)
            if add:
                tx.run("MATCH (e) WHERE elementId(e) = $eid CALL apoc.create.addLabels(e, $labels) YIELD node RETURN count(node)",
                       eid=eid, labels=add)
        else:
            record = tx.run("CREATE (e) SET e = $props WITH e CALL apoc.create.addLabels(e, $labels) YIELD node "
                            "RETURN elementId(node) AS eid", props=props, labels=labels).single()
            eid = record["eid"]
        restored[snapshot["id"]] = eid
        write_embedding(tx, eid, vector_for(vectors, entity_text(snapshot["id"], props.get("description")), index_dim))

    # 写しのリレーションの相手を引く
    wanted_refs = defaultdict(set)
    for snapshot in snapshots:
        for rel in snapshot["rels"]:
            kind = _other_kind(rel)
            if kind == "entity" and rel["other_id"] in restored:
                continue
            if kind is not None and rel["other_id"] is not None:
                wanted_refs[kind].add(rel["other_id"])
        wanted_refs["chunk"] |= set(snapshot["chunk_ids"])
    resolved = {kind: _resolve(tx, kind, ids) for kind, ids in wanted_refs.items()}

    desired = []  # (start, type, end, props)
    skipped = 0
    for snapshot in snapshots:
        own = restored[snapshot["id"]]
        for rel in snapshot["rels"]:
            kind = _other_kind(rel)
            if kind == "entity" and rel["other_id"] in restored:
                if rel["dir"] == "in":
                    continue  # 相手の写しの out で戻す
                other = restored[rel["other_id"]]
            else:
                other = resolved.get(kind, {}).get(rel["other_id"]) if kind else None
            if other is None:
                skipped += 1
                continue
            if rel["dir"] == "out":
                desired.append((own, rel["type"], other, rel["props"]))
            else:
                desired.append((other, rel["type"], own, rel["props"]))
        for chunk_id in snapshot["chunk_ids"]:
            chunk = resolved.get("chunk", {}).get(chunk_id)
            if chunk is None:
                skipped += 1
                continue
            desired.append((chunk, "HAS_ENTITY", own, {}))

    current = defaultdict(list)
    for record in tx.run("""
            UNWIND $eids AS eid
            MATCH (e)-[r]-(o) WHERE elementId(e) = eid
              AND (o:__Entity__ OR o:Chunk OR o:`__Community__` OR o:Document)
            WITH DISTINCT r
            RETURN elementId(r) AS rid, elementId(startNode(r)) AS s, type(r) AS t, elementId(endNode(r)) AS en,
                   properties(r) AS p""", eids=list(restored.values())):
        current[(record["s"], record["t"], record["en"])].append((record["rid"], jsonable(dict(record["p"]))))
    wanted = defaultdict(list)
    for start, rel_type, end, props in desired:
        wanted[(start, rel_type, end)].append(props)

    to_delete, to_update, to_create = [], [], []
    for key in set(current) | set(wanted):
        have = list(current.get(key, []))
        missing = []
        for props in wanted.get(key, []):
            index = next((i for i, (_, p) in enumerate(have) if p == props), None)
            if index is None:
                missing.append(props)
            else:
                have.pop(index)
        while have and missing:
            rid, _ = have.pop(0)
            to_update.append({"rid": rid, "props": missing.pop(0)})
        to_delete.extend(rid for rid, _ in have)
        to_create.extend({"s": key[0], "t": key[1], "e": key[2], "props": props} for props in missing)
    if to_delete:
        tx.run("UNWIND $rids AS rid MATCH ()-[r]->() WHERE elementId(r) = rid DELETE r", rids=to_delete)
    if to_update:
        tx.run("UNWIND $rows AS row MATCH ()-[r]->() WHERE elementId(r) = row.rid SET r = row.props", rows=to_update)
    if to_create:
        tx.run("""UNWIND $rows AS row
                  MATCH (a) WHERE elementId(a) = row.s
                  MATCH (b) WHERE elementId(b) = row.e
                  CALL apoc.create.relationship(a, row.t, row.props, b) YIELD rel
                  RETURN count(rel)""", rows=to_create)

    for uid, row in op["ledger_before"].items():
        lt.restore(uid, row)
    docs |= entity_documents(tx, list(restored.values()))
    return list(restored), restored, docs, skipped
