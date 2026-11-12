"""修正台帳（設計メモ 2.4）。

ラベル __GraphEdit__ の管理用ノードに 1 行ずつ置く。エンティティともリレーションともつながない。

| kind           | uid                                             | プロパティ                          |
|----------------|-------------------------------------------------|-------------------------------------|
| alias          | alias: + norm_key(alias)                        | alias, target（代表のエンティティの id） |
| tombstone_node | tnode: + norm_key(name)                         | name                                |
| tombstone_rel  | trel: + norm_key(src)|TYPE|norm_key(tgt)        | src, type, tgt                      |
| type_override  | type: + entity_id                               | entity, labels                      |
| curated        | cur: + entity_id                                | entity, keys, values_json           |
| manual_rel     | mrel: + src_id|TYPE|tgt_id                      | src, type, tgt, props_json          |
| distinct       | dist: + 2 つの norm_key を辞書順に | で連結       | a, b                                |

共通: kind, uid, created_at, op_id。

ここには次を置く:
- uid の作り方
- 制約（graph_edit_uid、graph_edit_op_id、graph_lock_name）と排他のノード（__GraphLock__ {name:'graph'}）の用意
- LedgerTx: 編集のトランザクションの中で行を読み書きし、触れた行の前後（ledger_before / ledger_after）を残す
- 不変条件（2.4 の 1〜4）を守る書き換え: keep_name・rename_entity・merge_entities・delete_entity と、リレーションの操作の分
- Ledger: 取り込みの再適用（A2）で使う、台帳の全行の索引
"""
import json
import logging
from datetime import datetime, timezone

from src.graph_edit.normalize import norm_key, rel_key, rel_type_key
from src.graph_edit.validate import GRAPH_EDIT_LABEL, GRAPH_EDIT_OP_LABEL, GRAPH_LOCK_LABEL, INVALID, EditError

LEDGER_KINDS = ["alias", "tombstone_node", "tombstone_rel", "type_override", "curated", "manual_rel", "distinct"]

CONSTRAINTS = {
    "graph_edit_uid": f"CREATE CONSTRAINT graph_edit_uid IF NOT EXISTS FOR (g:{GRAPH_EDIT_LABEL}) REQUIRE g.uid IS UNIQUE",
    "graph_edit_op_id": f"CREATE CONSTRAINT graph_edit_op_id IF NOT EXISTS FOR (o:{GRAPH_EDIT_OP_LABEL}) REQUIRE o.op_id IS UNIQUE",
    # 排他のノードを MERGE で 1 つだけ作るため（初めて使うときに同時に来ても 2 つできない）
    "graph_lock_name": f"CREATE CONSTRAINT graph_lock_name IF NOT EXISTS FOR (l:{GRAPH_LOCK_LABEL}) REQUIRE l.name IS UNIQUE",
}
LOCK_NAME = "graph"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- uid ----

def alias_uid(alias):
    return "alias:" + norm_key(alias)


def tnode_uid(name):
    return "tnode:" + norm_key(name)


def trel_uid(src, rel_type, tgt):
    return "trel:" + rel_key(src, rel_type, tgt)


def type_uid(entity_id):
    return "type:" + str(entity_id)


def cur_uid(entity_id):
    return "cur:" + str(entity_id)


def mrel_uid(src, rel_type, tgt):
    return "mrel:" + str(src) + "|" + rel_type_key(rel_type) + "|" + str(tgt)


def dist_uid(a, b):
    return "dist:" + "|".join(sorted([norm_key(a), norm_key(b)]))


# ---- 制約と排他のノード ----

def ensure_schema(graph):
    """制約と排他のノードを、無ければ作る（初めて使うとき）。graph は Neo4jGraph（または query を持つもの）。"""
    existing = {row["name"] for row in graph.query("SHOW CONSTRAINTS YIELD name RETURN name")}
    for name, statement in CONSTRAINTS.items():
        if name not in existing:
            logging.info(f"graph_edit: creating constraint {name}")
            graph.query(statement)
    graph.query(f"MERGE (l:{GRAPH_LOCK_LABEL} {{name: $name}}) "
                "ON CREATE SET l.seq = 0, l.lease_until = 0", {"name": LOCK_NAME})


# ---- トランザクションの中の行の読み書き ----

def _clean_row(row):
    return dict(row) if row is not None else None


class LedgerTx:
    """編集のトランザクションの中で台帳の行を読み書きする。触れた行の前（初めて見たとき）と後を残す。"""

    def __init__(self, tx, op_id):
        self.tx = tx
        self.op_id = op_id
        self._before = {}   # uid -> 行（無ければ None）
        self._order = []    # 触れた順

    def _remember(self, uid, row):
        if uid not in self._before:
            self._before[uid] = _clean_row(row)
            self._order.append(uid)

    def get(self, uid):
        record = self.tx.run(f"MATCH (g:{GRAPH_EDIT_LABEL} {{uid: $uid}}) RETURN properties(g) AS p", uid=uid).single()
        row = dict(record["p"]) if record else None
        self._remember(uid, row)
        return row

    def find(self, kind, prop=None, values=None):
        """kind の行（prop が values のどれかに等しいものだけ）。読んだ行は前の状態として残す。"""
        if prop is None:
            result = self.tx.run(f"MATCH (g:{GRAPH_EDIT_LABEL} {{kind: $kind}}) RETURN properties(g) AS p", kind=kind)
        else:
            result = self.tx.run(f"MATCH (g:{GRAPH_EDIT_LABEL} {{kind: $kind}}) WHERE g[$prop] IN $values "
                                 "RETURN properties(g) AS p", kind=kind, prop=prop, values=list(values))
        rows = [dict(record["p"]) for record in result]
        for row in rows:
            self._remember(row["uid"], row)
        return rows

    def put(self, kind, uid, **fields):
        """行を書く（無ければ作る）。既にあれば created_at は残し、op_id は今の操作にする。"""
        current = self.get(uid) if uid not in self._before else self._current(uid)
        row = {"kind": kind, "uid": uid, "created_at": (current or {}).get("created_at") or now_iso(), "op_id": self.op_id}
        row.update({k: v for k, v in fields.items() if v is not None})
        self.tx.run(f"MERGE (g:{GRAPH_EDIT_LABEL} {{uid: $uid}}) SET g = $row", uid=uid, row=row)
        return row

    def delete(self, uid):
        if uid not in self._before:
            self.get(uid)
        self.tx.run(f"MATCH (g:{GRAPH_EDIT_LABEL} {{uid: $uid}}) DELETE g", uid=uid)

    def restore(self, uid, row):
        """取り消しで、行をそのままの形に戻す（row が None なら消す）。"""
        if uid not in self._before:
            self.get(uid)
        if row is None:
            self.tx.run(f"MATCH (g:{GRAPH_EDIT_LABEL} {{uid: $uid}}) DELETE g", uid=uid)
        else:
            self.tx.run(f"MERGE (g:{GRAPH_EDIT_LABEL} {{uid: $uid}}) SET g = $row", uid=uid, row=dict(row))

    def _current(self, uid):
        record = self.tx.run(f"MATCH (g:{GRAPH_EDIT_LABEL} {{uid: $uid}}) RETURN properties(g) AS p", uid=uid).single()
        return dict(record["p"]) if record else None

    def before_after(self):
        """触れた行の前と後（{uid: 行または None}）。"""
        after = {uid: None for uid in self._order}
        if self._order:
            result = self.tx.run(f"UNWIND $uids AS uid MATCH (g:{GRAPH_EDIT_LABEL} {{uid: uid}}) "
                                 "RETURN uid, properties(g) AS p", uids=self._order)
            for record in result:
                after[record["uid"]] = dict(record["p"])
        before = {uid: self._before[uid] for uid in self._order}
        # 前と後が同じ行（読んだだけの行）は記録に残さない
        changed = [uid for uid in self._order if before[uid] != after[uid]]
        return {uid: before[uid] for uid in changed}, {uid: after[uid] for uid in changed}


# ---- 不変条件（設計メモ 2.4）----

def keep_name(lt, name, entity_id):
    """エンティティ entity_id が名前 name で残る（改名・統合）ときの台帳の整理（不変条件 1）。

    - 正規化キーが name と同じ別名は、このエンティティを指すようにする。別名の文字列が name そのものなら消す
      （別名と同じ id のエンティティを作らない。表記だけが違う別名、例えば「Cc By 4.0」→「CC BY 4.0」は、
      抽出がその表記を出したときに読み替えるために残す）。
    - その名前の削除の記録（tombstone_node）は消す（同じ名前のエンティティがあるのに抽出で落とさない）。
    """
    row = lt.get(alias_uid(name))
    if row is not None:
        if row.get("alias") == name:
            lt.delete(row["uid"])
        elif row.get("target") != entity_id:
            lt.put("alias", row["uid"], alias=row.get("alias"), target=entity_id)
    if lt.get(tnode_uid(name)) is not None:
        lt.delete(tnode_uid(name))


def _retarget_alias_rows(lt, old_id, new_id):
    for row in lt.find("alias", "target", [old_id]):
        lt.put("alias", row["uid"], alias=row.get("alias"), target=new_id)


def _rewrite_manual_rels(lt, mapping):
    """manual_rel の端点を mapping（旧い id → 新しい id）で書き換える。自分自身へのリレーションになる行は消す。"""
    old_ids = list(mapping)
    rows = {row["uid"]: row for row in lt.find("manual_rel", "src", old_ids) + lt.find("manual_rel", "tgt", old_ids)}
    for row in rows.values():
        src = mapping.get(row.get("src"), row.get("src"))
        tgt = mapping.get(row.get("tgt"), row.get("tgt"))
        lt.delete(row["uid"])
        if src != tgt:
            lt.put("manual_rel", mrel_uid(src, row.get("type"), tgt), src=src, type=row.get("type"), tgt=tgt,
                   props_json=row.get("props_json") or "{}")


def _rewrite_tombstone_rels(lt, mapping):
    """削除したリレーションの記録の端点を、元の名前の完全一致で照合して書き換える（端点の名前の変更に追従する）。
    正規化キーで照合すると、正規化キーが同じ別のエンティティ（例「N=1台目」と「n=1台目」）の記録まで書き換える。"""
    for row in lt.find("tombstone_rel"):
        src, tgt = row.get("src"), row.get("tgt")
        new_src = mapping.get(src, src)
        new_tgt = mapping.get(tgt, tgt)
        if (new_src, new_tgt) == (src, tgt):
            continue
        lt.delete(row["uid"])
        lt.put("tombstone_rel", trel_uid(new_src, row.get("type"), new_tgt), src=new_src, type=row.get("type"), tgt=new_tgt)


def _rewrite_distinct(lt, mapping):
    """別物の組の名前を、元の名前の完全一致で照合して書き換える。同じ名前どうしの組になったら消す（人が統合したので）。"""
    for row in lt.find("distinct"):
        a, b = row.get("a"), row.get("b")
        new_a = mapping.get(a, a)
        new_b = mapping.get(b, b)
        if (new_a, new_b) == (a, b):
            continue
        lt.delete(row["uid"])
        if new_a != new_b:
            first, second = sorted([new_a, new_b], key=norm_key)
            lt.put("distinct", dist_uid(first, second), a=first, b=second)


def rename_entity(lt, old_id, new_id):
    """改名 old→new（不変条件 2）。"""
    _retarget_alias_rows(lt, old_id, new_id)
    lt.put("alias", alias_uid(old_id), alias=old_id, target=new_id)
    type_row = lt.get(type_uid(old_id))
    if type_row is not None:
        lt.delete(type_row["uid"])
        lt.put("type_override", type_uid(new_id), entity=new_id, labels=list(type_row.get("labels") or []))
    cur_row = lt.get(cur_uid(old_id))
    if cur_row is not None:
        lt.delete(cur_row["uid"])
        lt.put("curated", cur_uid(new_id), entity=new_id, keys=list(cur_row.get("keys") or []),
               values_json=cur_row.get("values_json") or "{}")
    mapping = {old_id: new_id}
    _rewrite_manual_rels(lt, mapping)
    _rewrite_tombstone_rels(lt, mapping)
    _rewrite_distinct(lt, mapping)
    keep_name(lt, new_id, new_id)


def merge_entities(lt, names, final_id, labels, extra_curated_keys, current_props):
    """統合（不変条件 3）。names は統合で使われなくなる名前（各 source の id と、改名するなら target の旧い id）。

    - 各名前の別名を final に付け替え、名前→final の別名を足す
    - manual_rel・tombstone_rel・distinct の名前を final に書き換える
    - type_override: 統合するどれかにあれば、統合後のラベルで final に 1 行（無ければ作らない）
    - curated: 統合するもの全部の項目に extra_curated_keys を足して final に 1 行
    """
    all_ids = list(dict.fromkeys(list(names) + [final_id]))
    for name in names:
        _retarget_alias_rows(lt, name, final_id)
        if name != final_id:
            lt.put("alias", alias_uid(name), alias=name, target=final_id)
    had_type = False
    keys = []
    for entity_id in all_ids:
        type_row = lt.get(type_uid(entity_id))
        if type_row is not None:
            had_type = True
            lt.delete(type_row["uid"])
        cur_row = lt.get(cur_uid(entity_id))
        if cur_row is not None:
            keys.extend(cur_row.get("keys") or [])
            lt.delete(cur_row["uid"])
    if had_type:
        lt.put("type_override", type_uid(final_id), entity=final_id, labels=list(labels))
    keys = sorted(set(keys) | set(extra_curated_keys or ()))
    if keys:
        lt.put("curated", cur_uid(final_id), entity=final_id, keys=keys,
               values_json=json.dumps({k: current_props.get(k) for k in keys}, ensure_ascii=False, default=str))
    mapping = {name: final_id for name in names if name != final_id}
    if mapping:
        _rewrite_manual_rels(lt, mapping)
        _rewrite_tombstone_rels(lt, mapping)
        _rewrite_distinct(lt, mapping)
    keep_name(lt, final_id, final_id)


def delete_entity(lt, entity_id, suppress):
    """エンティティの削除（不変条件 4）。suppress なら名前と別名を削除の記録に残す。"""
    for row in lt.find("alias", "target", [entity_id]):
        lt.delete(row["uid"])
        if suppress and row.get("alias"):
            lt.put("tombstone_node", tnode_uid(row["alias"]), name=row["alias"])
    if suppress:
        lt.put("tombstone_node", tnode_uid(entity_id), name=entity_id)
    for uid in (type_uid(entity_id), cur_uid(entity_id)):
        if lt.get(uid) is not None:
            lt.delete(uid)
    rows = {row["uid"]: row for row in lt.find("manual_rel", "src", [entity_id]) + lt.find("manual_rel", "tgt", [entity_id])}
    for uid in rows:
        lt.delete(uid)


def add_manual_relationship(lt, src, rel_type, tgt, props):
    """手動で追加したリレーション（M6・M8 の新しい組）。同じ組の削除の記録は消す。"""
    if lt.get(trel_uid(src, rel_type, tgt)) is not None:
        lt.delete(trel_uid(src, rel_type, tgt))
    lt.put("manual_rel", mrel_uid(src, rel_type, tgt), src=src, type=rel_type, tgt=tgt,
           props_json=json.dumps(props or {}, ensure_ascii=False, sort_keys=True))


def remove_relationship(lt, src, rel_type, tgt, suppress):
    """リレーションの削除（M7・M8 の旧い組）。suppress なら削除の記録を残す。同じ組の manual_rel は消す。"""
    if suppress:
        lt.put("tombstone_rel", trel_uid(src, rel_type, tgt), src=src, type=rel_type, tgt=tgt)
    if lt.get(mrel_uid(src, rel_type, tgt)) is not None:
        lt.delete(mrel_uid(src, rel_type, tgt))


def update_manual_relationship_props(lt, src, rel_type, tgt, props):
    """プロパティだけを変えたリレーションが manual_rel にあれば、記録のプロパティも合わせる。"""
    row = lt.get(mrel_uid(src, rel_type, tgt))
    if row is not None:
        lt.put("manual_rel", row["uid"], src=src, type=rel_type, tgt=tgt,
               props_json=json.dumps(props or {}, ensure_ascii=False, sort_keys=True))


def update_curated(lt, entity_id, changed_keys, current_props):
    """M3: 手で修正した項目を curated に足す（値は今の値。消した項目は null）。"""
    row = lt.get(cur_uid(entity_id))
    keys = sorted(set((row or {}).get("keys") or []) | set(changed_keys))
    lt.put("curated", cur_uid(entity_id), entity=entity_id, keys=keys,
           values_json=json.dumps({k: current_props.get(k) for k in keys}, ensure_ascii=False, default=str))


# ---- スキーマの統合（post_processing.graph_schema_consolidation）----

def rename_schema_types_in_ledger(graph, label_mapping, type_mapping):
    """スキーマの統合で替えた種類名を台帳にも当てる。type_override の labels、manual_rel と tombstone_rel の type
    （uid も作り直す）。1 つの書き込みトランザクションで行う。書き換えた行の数を返す。"""
    label_mapping = dict(label_mapping or {})
    type_mapping = {rel_type_key(old): new for old, new in (type_mapping or {}).items()}
    if not label_mapping and not type_mapping:
        return 0

    def work(tx):
        rows = [dict(record["p"]) for record in tx.run(
            f"MATCH (g:{GRAPH_EDIT_LABEL}) WHERE g.kind IN ['type_override', 'manual_rel', 'tombstone_rel'] "
            "RETURN properties(g) AS p")]
        deletes, puts = [], []
        for row in rows:
            kind = row.get("kind")
            if kind == "type_override":
                labels = list(dict.fromkeys(label_mapping.get(label, label) for label in (row.get("labels") or [])))
                if labels != list(row.get("labels") or []):
                    puts.append({**row, "labels": labels})
            elif row.get("type") is not None and rel_type_key(row["type"]) in type_mapping:
                new_type = type_mapping[rel_type_key(row["type"])]
                uid = (mrel_uid if kind == "manual_rel" else trel_uid)(row.get("src"), new_type, row.get("tgt"))
                deletes.append(row["uid"])
                puts.append({**row, "type": new_type, "uid": uid})
        if deletes:
            tx.run(f"UNWIND $uids AS uid MATCH (g:{GRAPH_EDIT_LABEL} {{uid: uid}}) DELETE g", uids=deletes)
        if puts:
            tx.run(f"UNWIND $rows AS row MERGE (g:{GRAPH_EDIT_LABEL} {{uid: row.uid}}) SET g = row", rows=puts)
        return len(puts)

    with graph._driver.session(database=graph._database) as session:
        changed = session.execute_write(work)
    if changed:
        logging.info(f"graph_edit: schema consolidation rewrote {changed} ledger rows")
    return changed


# ---- 取り込みの再適用（A2）で使う台帳の索引 ----

class Ledger:
    """台帳の全行の索引。純粋な関数（replay.apply_ledger_before_save）が読む。"""

    def __init__(self, rows=()):
        self.rows = [dict(row) for row in rows]
        self.alias = {}             # norm_key(alias) -> 代表の id
        self.tombstone_nodes = set()  # norm_key(name)
        self.tombstone_rels = set()   # rel_key(src, TYPE, tgt)
        self.type_override = {}     # entity id -> [labels]
        self.curated = {}           # entity id -> set(keys)
        self.manual_rels = []       # [{src, type, tgt, props}]
        self.distinct = set()       # (norm_key(a), norm_key(b)) を辞書順に
        for row in self.rows:
            kind = row.get("kind")
            if kind == "alias" and row.get("alias") is not None and row.get("target") is not None:
                self.alias[norm_key(row["alias"])] = row["target"]
            elif kind == "tombstone_node" and row.get("name") is not None:
                self.tombstone_nodes.add(norm_key(row["name"]))
            elif kind == "tombstone_rel":
                self.tombstone_rels.add(rel_key(row.get("src"), row.get("type"), row.get("tgt")))
            elif kind == "type_override" and row.get("entity") is not None:
                labels = [label for label in (row.get("labels") or []) if label]
                if labels:
                    self.type_override[row["entity"]] = labels
            elif kind == "curated" and row.get("entity") is not None:
                self.curated[row["entity"]] = set(row.get("keys") or [])
            elif kind == "manual_rel" and row.get("src") is not None and row.get("tgt") is not None:
                try:
                    props = json.loads(row.get("props_json") or "{}")
                except ValueError:
                    props = {}
                self.manual_rels.append({"src": row["src"], "type": row.get("type"), "tgt": row["tgt"],
                                         "props": props if isinstance(props, dict) else {}})
            elif kind == "distinct":
                self.distinct.add(tuple(sorted([norm_key(row.get("a")), norm_key(row.get("b"))])))

    def __len__(self):
        return len(self.rows)

    def is_tombstoned_node(self, name):
        return norm_key(name) in self.tombstone_nodes

    def is_tombstoned_rel(self, src, rel_type, tgt):
        return rel_key(src, rel_type, tgt) in self.tombstone_rels

    def map_id(self, name):
        """抽出の名前を読み替える。(読み替えた id または None（落とす）, 別名で読み替えたか)。

        削除の記録に当たれば落とす。別名に当たれば代表の id にする（代表がさらに別名を持つときもたどる）。
        """
        key = norm_key(name)
        if key in self.tombstone_nodes:
            return None, False
        target = self.alias.get(key)
        if target is None:
            return name, False
        seen = {key}
        current = target
        while True:
            current_key = norm_key(current)
            if current_key in self.tombstone_nodes:
                return None, True
            following = self.alias.get(current_key)
            if following is None or following == current or current_key in seen:
                break
            seen.add(current_key)
            current = following
        return current, current != name


def load_ledger(graph):
    """台帳の全行を読む（数千行まで想定）。"""
    rows = graph.query(f"MATCH (g:{GRAPH_EDIT_LABEL}) RETURN properties(g) AS p")
    return Ledger(row["p"] for row in rows)


def check_kind(kind):
    if kind is not None and kind not in LEDGER_KINDS:
        raise EditError(INVALID, f"unknown ledger kind {kind!r} (allowed: {', '.join(LEDGER_KINDS)})")
    return kind
