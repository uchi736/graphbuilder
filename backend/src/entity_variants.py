# -*- coding: utf-8 -*-
"""表記ゆれのエンティティを規則で 1 つにまとめる後処理（merge_entity_variants）。LLM は使わない。何度流しても同じ結果になる。

対象: __Entity__ のうち、次の 2 つがともに同じエンティティの組。
  名前の正規形  NFKC → 空白・ハイフンの異体・下線を除く → 英字を小文字に（variant_key）。
                先頭の負号（「-5℃」の「-」）は残す。中黒・ピリオド・スラッシュ・括弧は残す（「1.5」と「15」を同じにしない）
  種類          __Entity__ 以外のラベルが 1 つでも共通する（V (m3/s)〔Parameter〕と V(m3/s)〔Term〕は別物として扱う）。
                EDC は 1 つのエンティティに 2 つのラベルを付けることがあり（吐出圧力(静圧)〔Term,MeasurementItem〕）、
                labels() の並びに意味は無いので「最初のラベル」ではなく共通のラベルの有無で見る
対象外: 管理ラベル（Document・Chunk・Section・__Community__ と PROTECTED_MANAGEMENT_LABELS）を持つノード、
        文書層の文書番号（規則で作ったノード method = 'rule'、および Standard の型で名前が文書層の規則で文書番号・外部の識別子と
        判定されるもの。名寄せは文書層が受け持つ）、グラフの手動修正で「別物」と登録した名前の組を含む組、
        手動で修正したノード（_curated）が 2 つ以上ある組。
残す側: 手動で修正したノード → 言及（HAS_ENTITY の本数）の多いもの → 名前の短いもの → 名前の辞書順。
統合: apoc.refactor.mergeNodes（属性は残す側を優先、関係は付け替え、同じ相手・同じ種類の関係は 1 本にまとめる。
      組の中どうしの関係は自己ループになるので消す）。統合した側の名前（とその aliases）は残す側の aliases に足す。
      残す側の _v を 1 増やす（docs/graph-edit/DESIGN.md の 2.3）。説明が変わった時だけ埋め込みを消す（後処理「エンティティの埋め込み」で作り直す）。
統合前の状態は台帳 logs/entity_variants/<時刻>.json に書く（restore_entity_variants で戻せる）。
使い方: merge_entity_variants(graph, dry_run=False) -> {"summary": {...}, "groups": [...], "ledger": パス or None}
        restore_entity_variants(graph, ledger_path) -> {"summary": {...}}
        コマンド: python -m src.entity_variants [--apply | --restore 台帳]（接続は環境変数 NEO4J_URI など。既定は dry_run）
"""
import collections
import datetime
import json
import logging
import os
import re
import unicodedata

from src.shared.common_fn import execute_graph_query
from src.shared.constants import PROTECTED_MANAGEMENT_LABELS

ENTITY_LABEL = "__Entity__"
RULE_METHOD = "rule"
# 管理ラベル（このラベルを 1 つでも持つノードは対象外）。Standard は EDC の抽出の型でもあるので、文書番号かどうかで分ける（is_document_number）
EXCLUDED_LABELS = sorted({"Document", "Chunk", "Section", "__Community__", "Session", "Message"}
                         | set(PROTECTED_MANAGEMENT_LABELS))
LEDGER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "entity_variants")
MERGE_CONFIG = {"properties": "discard", "mergeRels": True, "produceSelfRel": False, "preserveExistingSelfRels": False}

# ハイフンの異体（NFKC の後に残るもの。全角の「－」は NFKC で「-」になる）。長音符「ー」は含めない
HYPHENS = "-‐‑‒–—―⁃−﹘﹣"
_HYPHEN_RE = re.compile("[" + re.escape(HYPHENS) + "]")
_LEADING_MINUS = re.compile("^-(?=[0-9])")
_DROP = re.compile(r"[\s_\-]+")


def variant_key(name):
    """名前の正規形。NFKC → ハイフンの異体を「-」に → 空白・「-」・「_」を除く（先頭の負号は残す）→ casefold。"""
    s = unicodedata.normalize("NFKC", "" if name is None else str(name)).strip()
    s = _HYPHEN_RE.sub("-", s)
    s = re.sub(r"\s+", "", s)
    head = "-" if _LEADING_MINUS.match(s) else ""
    if head:
        s = s[1:]
    return head + _DROP.sub("", s).casefold()


def type_key(labels):
    return tuple(sorted(l for l in (labels or []) if l != ENTITY_LABEL))


def _norm_key(name):
    from src.graph_edit.normalize import norm_key
    return norm_key(name)


def _keep_order(row):
    return (not row.get("curated"), -int(row.get("mentions") or 0), len(str(row["id"])), str(row["id"]))


def is_document_number(row, cfg=None):
    """文書層の文書番号のノードか（規則で作ったもの、または Standard の型で、名前が文書層の規則で文書番号・外部の識別子と判定されるもの）。
    文書番号の名寄せは文書層（document_layer.py）が受け持つので、この後処理では触らない。"""
    if row.get("method") == RULE_METHOD:
        return True
    if "Standard" not in set(row.get("labels") or []):
        return False
    from src import document_layer_rules as R
    cfg = cfg if cfg is not None else R.load_config()
    name = str(row.get("id") or "")
    return bool(R.normalize_number(name, cfg, strict=True)[0] or R.is_external_identifier(name, cfg))


def _components(members):
    """種類のラベルが 1 つでも重なるものどうしをつなぐ（ラベルの無いものはラベルの無いものとだけ）。"""
    parent = list(range(len(members)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    types = [set(type_key(m.get("labels"))) for m in members]
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            if (types[i] & types[j]) or (not types[i] and not types[j]):
                parent[find(i)] = find(j)
    comps = collections.defaultdict(list)
    for i, m in enumerate(members):
        comps[find(i)].append(m)
    return list(comps.values())


def plan_groups(rows, distinct_pairs=(), cfg=None):
    """組を作る（純粋な関数）。rows: [{eid, id, labels, method, mentions, curated, aliases}]。
    種類が同じ = __Entity__ 以外のラベルが 1 つでも共通する（多くは 1 つずつで、そのときは同じラベルであること）。
    3 つ以上が鎖状にだけ重なる（A〔X〕・B〔X,Y〕・C〔Y〕のように全員に共通のラベルが無い）組は統合しない。
    distinct_pairs: 手動修正の台帳の「別物」の組（norm_key の 2 つ組）。cfg: 文書番号の規則（無ければ読み込む）。
    返り値 {"groups": [{key, type, keep, merge}], "skipped": [{key, type, members, reason}], "cross_type": [{key, members}]}"""
    distinct = {tuple(sorted(p)) for p in distinct_pairs}
    if cfg is None and any("Standard" in (r.get("labels") or []) for r in rows):
        from src import document_layer_rules as R
        cfg = R.load_config()
    by_key = collections.defaultdict(list)
    for r in rows:
        if r.get("id") is None or str(r["id"]).strip() == "":
            continue
        if set(r.get("labels") or []) & set(EXCLUDED_LABELS) or is_document_number(r, cfg):
            continue
        k = variant_key(r["id"])
        if k:
            by_key[k].append(r)
    groups, skipped, cross_type = [], [], []
    for k in sorted(by_key):
        if len(by_key[k]) < 2:
            continue
        comps = _components(by_key[k])
        if len(comps) > 1:
            cross_type.append({"key": k, "members": sorted("%s〔%s〕" % (m["id"], ",".join(type_key(m.get("labels"))))
                                                          for m in by_key[k])})
        for members in sorted(comps, key=lambda c: sorted(str(m["id"]) for m in c)):
            if len(members) < 2:
                continue
            members = sorted(members, key=_keep_order)
            names = [str(m["id"]) for m in members]
            union = sorted(set().union(*[set(type_key(m.get("labels"))) for m in members]))
            common = set.intersection(*[set(type_key(m.get("labels"))) for m in members])
            reason = None
            if union and not common:
                reason = "種類のラベルが鎖状にしか重ならない（全員に共通のラベルが無い）"
            elif sum(1 for m in members if m.get("curated")) > 1:
                reason = "手動で修正したノードが 2 つ以上ある"
            else:
                nks = [_norm_key(n) for n in names]
                if any(tuple(sorted((a, b))) in distinct for i, a in enumerate(nks) for b in nks[i + 1:]):
                    reason = "手動修正の台帳で別物として登録されている"
            if reason:
                skipped.append({"key": k, "type": union, "members": names, "reason": reason})
                continue
            groups.append({"key": k, "type": union, "keep": members[0], "merge": members[1:]})
    return {"groups": groups, "skipped": skipped, "cross_type": cross_type}


def merged_aliases(keep, others):
    """残す側の aliases（既存 + 統合した側の名前と aliases。残す側の名前は除く。graph_edit と同じ並び）。"""
    names = set(a for a in (keep.get("aliases") or []) if isinstance(a, str))
    for o in others:
        names.add(str(o["id"]))
        names |= set(a for a in (o.get("aliases") or []) if isinstance(a, str))
    names.discard(str(keep["id"]))
    return sorted(names, key=lambda a: (_norm_key(a), a))


def _candidates(graph):
    return execute_graph_query(graph, """
        MATCH (e:__Entity__) WHERE e.id IS NOT NULL AND (e.method IS NULL OR e.method <> $m)
          AND NOT any(l IN labels(e) WHERE l IN $excluded)
        RETURN elementId(e) AS eid, e.id AS id, labels(e) AS labels, e.method AS method,
               COUNT { (e)<-[:HAS_ENTITY]-() } AS mentions, coalesce(e._curated, false) AS curated, e.aliases AS aliases""",
        params={"m": RULE_METHOD, "excluded": EXCLUDED_LABELS})


def _distinct_pairs(graph):
    try:
        from src.graph_edit.ledger import load_ledger
        return load_ledger(graph).distinct
    except Exception as e:  # 台帳が無い DB（手動修正を使ったことが無い）
        logging.info("entity variants: no graph-edit ledger (%s)", e)
        return set()


def _entity_count(graph):
    return execute_graph_query(graph, """
        MATCH (e:__Entity__) WHERE NOT any(l IN labels(e) WHERE l IN $excluded) RETURN count(e) AS n""",
        params={"excluded": EXCLUDED_LABELS})[0]["n"]


def _show(row):
    return {"id": row["id"], "labels": type_key(row.get("labels")), "mentions": row.get("mentions"), "eid": row["eid"]}


def _snapshot(graph, eid):
    node = execute_graph_query(graph, """
        MATCH (e) WHERE elementId(e) = $eid
        RETURN labels(e) AS labels, apoc.map.removeKey(properties(e), 'embedding') AS props, e.embedding IS NOT NULL AS has_embedding""",
        params={"eid": eid})
    if not node:
        return None
    # 関係は 1 行に 1 本で読む（Neo4jGraph の sanitize が長いリストを落とすため collect しない）
    rels = execute_graph_query(graph, """
        MATCH (e) WHERE elementId(e) = $eid
        MATCH (e)-[r]-(x)
        RETURN type(r) AS type, startNode(r) = e AS outgoing, elementId(x) AS other_eid, labels(x) AS other_labels,
               coalesce(x.id, x.fileName) AS other_id, properties(r) AS props""", params={"eid": eid})
    return {"eid": eid, "labels": node[0]["labels"], "props": node[0]["props"], "has_embedding": node[0]["has_embedding"],
            "relationships": [dict(r) for r in rels]}


def _session(graph):
    return graph._driver.session(database=graph._database)


def _merge_group(tx, keep_eid, other_eids, aliases, before_description):
    tx.run("""
        MATCH (k) WHERE elementId(k) = $keep
        UNWIND $others AS oid
        MATCH (o) WHERE elementId(o) = oid
        WITH k, collect(o) AS others
        CALL apoc.refactor.mergeNodes([k] + others, $config) YIELD node
        RETURN count(node)""", keep=keep_eid, others=other_eids, config=MERGE_CONFIG).consume()
    tx.run("MATCH (n)-[r]->(n) WHERE elementId(n) = $eid DELETE r", eid=keep_eid).consume()
    rec = tx.run("""
        MATCH (n) WHERE elementId(n) = $eid
        SET n.aliases = $aliases, n._v = coalesce(n._v, 0) + 1
        RETURN n.description AS description, COUNT { (n)--() } AS rels""", eid=keep_eid, aliases=aliases).single()
    if (rec["description"] or "") != (before_description or ""):
        tx.run("MATCH (n) WHERE elementId(n) = $eid REMOVE n.embedding", eid=keep_eid).consume()
    return rec["rels"]


def merge_entity_variants(graph, dry_run=False, ledger_dir=None):
    rows = _candidates(graph)
    plan = plan_groups(rows, _distinct_pairs(graph))
    groups = plan["groups"]
    summary = {"entities": _entity_count(graph), "candidates": len(rows), "groups": len(groups),
               "nodes_in_groups": sum(1 + len(g["merge"]) for g in groups), "to_merge": sum(len(g["merge"]) for g in groups),
               "skipped_groups": len(plan["skipped"]), "cross_type_names": len(plan["cross_type"]), "dry_run": bool(dry_run)}
    shown = [{"key": g["key"], "type": g["type"], "keep": _show(g["keep"]), "merge": [_show(m) for m in g["merge"]]} for g in groups]
    result = {"summary": summary, "groups": shown, "skipped": plan["skipped"], "cross_type": plan["cross_type"], "ledger": None}
    if dry_run or not groups:
        return result

    # ---- 台帳（統合前の状態）----
    ledger_dir = ledger_dir or LEDGER_DIR
    os.makedirs(ledger_dir, exist_ok=True)
    ledger_path = os.path.join(ledger_dir, datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    ledger = {"created_at": datetime.datetime.now().isoformat(timespec="seconds"), "groups": []}
    for g in groups:
        ledger["groups"].append({"key": g["key"], "type": g["type"], "keep": _snapshot(graph, g["keep"]["eid"]),
                                 "merged": [_snapshot(graph, m["eid"]) for m in g["merge"]]})
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=1, default=str)

    # ---- 書き込み（組ごとに 1 つのトランザクション）----
    from src.graph_edit.history import entity_documents
    merged = rels_before = rels_after = 0
    docs = set()
    for g, lg in zip(groups, ledger["groups"]):
        keep, others = lg["keep"], [m for m in lg["merged"] if m]
        if keep is None or not others:
            continue
        member_eids = {keep["eid"]} | {m["eid"] for m in others}
        # 統合前の関係の本数（組の中どうしの関係は 1 本を両端で 2 回数えるので、組の外への関係と分けて数える）
        outside = sum(1 for s in [keep] + others for r in s["relationships"] if r["other_eid"] not in member_eids)
        inside = sum(1 for s in [keep] + others for r in s["relationships"] if r["other_eid"] in member_eids) // 2
        aliases = merged_aliases({"id": keep["props"].get("id"), "aliases": keep["props"].get("aliases")},
                                 [{"id": m["props"].get("id"), "aliases": m["props"].get("aliases")} for m in others])
        with _session(graph) as session:
            docs |= session.execute_read(lambda tx: entity_documents(tx, list(member_eids)))
            after = session.execute_write(lambda tx: _merge_group(tx, keep["eid"], [m["eid"] for m in others], aliases,
                                                                 keep["props"].get("description")))
        merged += len(others)
        rels_before += outside + inside
        rels_after += after
    summary.update({"merged": merged, "relationships_before": rels_before, "relationships_after": rels_after,
                    "entities_after": _entity_count(graph), "ledger": ledger_path})
    try:
        from src.graph_edit.ops import update_counts
        update_counts(graph, docs)
    except Exception as e:  # 件数の更新に失敗しても統合そのものは済んでいる
        logging.warning("entity variants: could not update the document counts: %s", e)
    logging.info("entity variants: %s", json.dumps(summary, ensure_ascii=False))
    result["summary"] = summary
    result["ledger"] = ledger_path
    return result


# ---- 戻す ----

def _match_other(tx, rel, eid_map):
    """関係の相手を探す。台帳の要素 ID（戻したノードは新しい要素 ID に読み替える）→ id・fileName の順。"""
    eid = eid_map.get(rel["other_eid"], rel["other_eid"])
    rec = tx.run("MATCH (x) WHERE elementId(x) = $eid RETURN elementId(x) AS eid, coalesce(x.id, x.fileName) AS key",
                 eid=eid).single()
    # 要素 ID は消えたノードのものが使い回されることがあるので、名前でも確かめる
    if rec and (rel.get("other_id") is None or rec["key"] == rel["other_id"] or rel["other_eid"] in eid_map):
        return rec["eid"]
    if rel.get("other_id") is None:
        return None
    labels = [l for l in (rel.get("other_labels") or []) if l in ("Chunk", "Document", ENTITY_LABEL)]
    if not labels:
        return None
    key = "fileName" if labels[0] == "Document" else "id"
    rec = tx.run(f"MATCH (x:`{labels[0]}`) WHERE x[$key] = $v RETURN elementId(x) AS eid LIMIT 1", key=key, v=rel["other_id"]).single()
    return rec["eid"] if rec else None


def _restore_group(tx, lg):
    keep = lg["keep"]
    eid_map = {}
    # 統合した側のノードを作り直す（同じ id のノードが既にあればそれに書く）
    for m in lg["merged"]:
        if not m:
            continue
        rec = tx.run("""
            MERGE (n:__Entity__ {id: $id})
            SET n += $props
            WITH n CALL apoc.create.addLabels(n, $labels) YIELD node
            RETURN elementId(node) AS eid""", id=m["props"]["id"], props=m["props"], labels=m["labels"]).single()
        eid_map[m["eid"]] = rec["eid"]
    keep_eid = _match_other(tx, {"other_eid": keep["eid"], "other_id": keep["props"].get("id"),
                                 "other_labels": [ENTITY_LABEL]}, {})
    if keep_eid is None:
        raise RuntimeError("the kept entity %r is gone" % keep["props"].get("id"))
    eid_map[keep["eid"]] = keep_eid
    # 残した側の関係を統合前のものだけにする（統合で付いた関係を外す）
    original = {}
    for r in keep["relationships"]:
        other = _match_other(tx, r, eid_map)
        if other:
            original[(r["type"], bool(r["outgoing"]), other)] = r["props"] or {}
    for rec in list(tx.run("""
            MATCH (k) WHERE elementId(k) = $eid MATCH (k)-[r]-(x)
            RETURN elementId(r) AS rid, type(r) AS type, startNode(r) = k AS outgoing, elementId(x) AS other""", eid=keep_eid)):
        key = (rec["type"], bool(rec["outgoing"]), rec["other"])
        if key not in original:
            tx.run("MATCH ()-[r]-() WHERE elementId(r) = $rid DELETE r", rid=rec["rid"]).consume()
        else:  # 重複のまとめで統合した側の関係のプロパティが入っていれば戻す
            tx.run("MATCH ()-[r]-() WHERE elementId(r) = $rid SET r = $props", rid=rec["rid"], props=original[key]).consume()
    # 統合した側の関係を作り直す
    missing = 0
    for m in lg["merged"]:
        if not m:
            continue
        me = eid_map[m["eid"]]
        for r in m["relationships"]:
            other = _match_other(tx, r, eid_map)
            if other is None:
                missing += 1
                continue
            start, end = (me, other) if r["outgoing"] else (other, me)
            tx.run("""
                MATCH (a) WHERE elementId(a) = $a MATCH (b) WHERE elementId(b) = $b
                CALL apoc.merge.relationship(a, $type, {}, $props, b, {}) YIELD rel
                RETURN count(rel)""", a=start, b=end, type=r["type"], props=r["props"] or {}).consume()
    # 残した側の属性を統合前の値に戻す（統合した側から引き継いだ属性は消す。埋め込みは残っていれば残す。_v は 1 増やす）
    props = {k: v for k, v in keep["props"].items() if k != "_v"}
    tx.run("""
        MATCH (k) WHERE elementId(k) = $eid
        WITH k, k.embedding AS emb, coalesce(k._v, 0) + 1 AS v
        SET k = $props
        SET k._v = v
        FOREACH (_ IN CASE WHEN emb IS NULL THEN [] ELSE [1] END | SET k.embedding = emb)""", eid=keep_eid, props=props).consume()
    return missing


def restore_entity_variants(graph, ledger_path):
    """台帳の統合を戻す（新しい順）。統合の後にグラフが変わっていれば、戻せる範囲で戻す（相手が見つからない関係は数えて飛ばす）。
    埋め込みは台帳に無いので、戻したノードの埋め込みは後処理「エンティティの埋め込み」で作り直す。"""
    with open(ledger_path, encoding="utf-8") as f:
        ledger = json.load(f)
    restored = missing = 0
    for lg in reversed(ledger["groups"]):
        if not lg.get("keep"):
            continue
        with _session(graph) as session:
            missing += session.execute_write(lambda tx: _restore_group(tx, lg))
        restored += sum(1 for m in lg["merged"] if m)
    summary = {"restored": restored, "missing_relationships": missing, "entities_after": _entity_count(graph)}
    logging.info("entity variants restored: %s", json.dumps(summary, ensure_ascii=False))
    return {"summary": summary}


def _main():
    import argparse
    from langchain_neo4j import Neo4jGraph
    ap = argparse.ArgumentParser(description="表記ゆれのエンティティの統合（既定は dry_run）")
    ap.add_argument("--apply", action="store_true", help="統合する（貸し出しを取って書き込む）")
    ap.add_argument("--restore", help="台帳の統合を戻す")
    args = ap.parse_args()
    graph = Neo4jGraph(url=os.environ["NEO4J_URI"], username=os.environ.get("NEO4J_USERNAME", "neo4j"),
                       password=os.environ["NEO4J_PASSWORD"], database=os.environ.get("NEO4J_DATABASE", "neo4j"),
                       refresh_schema=False, sanitize=True)
    from src.graph_edit.lease import run_with_lease
    if args.restore:
        out = run_with_lease(graph, "entity_variants", restore_entity_variants, graph, args.restore)
    elif args.apply:
        out = run_with_lease(graph, "entity_variants", merge_entity_variants, graph)
    else:
        out = merge_entity_variants(graph, dry_run=True)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    _main()
