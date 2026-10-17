# 正解データ(gold_graph.json)と Neo4j 上のグラフを突合する。backend コンテナ内で実行:
#   docker exec -i backend python - <gold.json をstdinに> ... の代わりに、gold JSON は引数パスで渡す
import os, re, sys, json, unicodedata, collections
from neo4j import GraphDatabase
gold = json.load(open(sys.argv[1], encoding="utf-8"))
EXCLUDE_DOC = "TS3-5.pdf"
def norm(s): 
    s = unicodedata.normalize("NFKC", s or ""); s = re.sub(r"[（(](最新版|旧版|旧|改|案|参考)[)）]", "", s)
    return re.sub(r"\s+", "", s).upper()
drv = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
with drv.session(database=os.environ.get("NEO4J_DATABASE","neo4j")) as s:
    nodes = s.run("MATCH (e:__Entity__)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d:Document) WHERE d.fileName IS NOT NULL AND d.fileName <> $x "
                  "WITH e, collect(DISTINCT d.fileName) AS docs RETURN elementId(e) AS eid, e.id AS id, [l IN labels(e) WHERE l<>'__Entity__'] AS labels, docs", x=EXCLUDE_DOC).data()
    rels = s.run("MATCH (a:__Entity__)-[r]->(b:__Entity__) RETURN elementId(a) AS a, type(r) AS t, elementId(b) AS b").data()
    docrefs = s.run("MATCH (a:Document)-[r:REFERENCES]->(b:Document) RETURN a.fileName AS f, b.fileName AS t").data()
    stdrefs = {(r["a"], r["b"]) for r in s.run("MATCH (a:Standard)-[:REFERENCES]->(b:Standard) RETURN a.id AS a, b.id AS b").data()}
    doc_ents = s.run("MATCH (e:__Entity__)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d:Document) WHERE d.fileName IS NOT NULL RETURN d.fileName AS f, collect(DISTINCT elementId(e)) AS es").data()
by_norm = collections.defaultdict(list)
for n in nodes: by_norm[norm(n["id"])].append(n)
def match(gent):
    keys = {norm(gent["id"])} | {norm(a) for a in gent.get("aliases", [])}
    hits = []
    for k in keys:
        hits += by_norm.get(k, [])
    # 部分一致（正解表記を含むノード）も拾う。ただし短い別名(3文字未満)は完全一致のみ
    # 部分一致は「正解表記で始まる/終わる」かつ伸びが3文字以内（形・過給機・（…）程度）に限定
    for k in keys:
        if len(k) >= 3:
            for nk, ns in by_norm.items():
                if nk in keys: continue
                if (nk.startswith(k) or nk.endswith(k)) and len(nk) - len(k) <= 3: hits += ns
    uniq = {h["eid"]: h for h in hits}
    return list(uniq.values())
ent_rows = []; matched_sets = {}
for g in gold["entities"]:
    m = match(g); matched_sets[g["id"]] = {x["eid"] for x in m}
    labels = collections.Counter(l for x in m for l in x["labels"])
    ent_rows.append({"gold": g["id"], "type": g["type"], "found": len(m) > 0, "nodes": len(m), "labels": dict(labels), "ids": [x["id"] for x in m][:6]})
found = sum(1 for r in ent_rows if r["found"]); frag = [r["nodes"] for r in ent_rows if r["found"]]
multi_label = sum(1 for r in ent_rows if len(r["labels"]) > 1)
type_ok = sum(1 for r in ent_rows if r["found"] and r["type"] in r["labels"])
adj = collections.defaultdict(list)
for r in rels: adj[(r["a"], r["b"])].append(r["t"]); adj[(r["b"], r["a"])].append(r["t"])
rel_rows = []
for gr in gold["relations"]:
    A = matched_sets.get(gr["s"], set()); B = matched_sets.get(gr["o"], set())
    types = set(t for a in A for b in B for t in adj.get((a, b), []))
    rel_rows.append({"gold": f'{gr["s"]} -[{gr["p"]}]-> {gr["o"]}', "any_edge": bool(types), "typed": bool(types & set(gr["accept"] + [gr["p"]])), "types": sorted(types)[:5]})
any_edge = sum(1 for r in rel_rows if r["any_edge"]); typed = sum(1 for r in rel_rows if r["typed"])
direct = {(d["f"], d["t"]) for d in docrefs}
DOCKEY={"HS-VTR-T001E.pdf":"VTR-T001","HS-VTR-T002F.pdf":"VTR-T002","VTR-T003D.pdf":"VTR-T003","VTR-T004C.pdf":"VTR-T004","VTR-T005B.pdf":"VTR-T005","VTR-T_発行目録.pdf":"VTR-T-INDEX"}
ents_by_doc = {d["f"]: set(d["es"]) for d in doc_ents}
dr_rows = []
for g in gold["doc_references"]:
    shared = len(ents_by_doc.get(g["from"], set()) & ents_by_doc.get(g["to"], set()))
    dr_rows.append({"gold": f'{g["from"]} → {g["to"]}', "direct_REFERENCES": (g["from"], g["to"]) in direct, "standard_REFERENCES": (DOCKEY.get(g["from"]), DOCKEY.get(g["to"])) in stdrefs, "shared_entities": shared})
print(json.dumps({
  "entities": {"gold": len(ent_rows), "found": found, "recall": round(found/len(ent_rows), 2),
               "avg_nodes_per_found": round(sum(frag)/max(1,len(frag)), 2), "fragmented(>1node)": sum(1 for f in frag if f > 1),
               "multi_label": multi_label, "type_match": type_ok, "missing": [r["gold"] for r in ent_rows if not r["found"]],
               "most_fragmented": sorted([(r["gold"], r["nodes"], r["ids"]) for r in ent_rows if r["nodes"] > 1], key=lambda x: -x[1])[:8]},
  "relations": {"gold": len(rel_rows), "any_edge": any_edge, "typed": typed, "recall_any": round(any_edge/len(rel_rows), 2), "recall_typed": round(typed/len(rel_rows), 2),
                "hits": [r["gold"] + "  ⇐ " + ",".join(r["types"]) for r in rel_rows if r["any_edge"]][:20],
                "misses": [r["gold"] for r in rel_rows if not r["any_edge"]]},
  "doc_references": {"gold": len(dr_rows), "direct": sum(1 for r in dr_rows if r["direct_REFERENCES"]), "standard_refs": sum(1 for r in dr_rows if r["standard_REFERENCES"]), "rows": dr_rows}
}, ensure_ascii=False, indent=1))
