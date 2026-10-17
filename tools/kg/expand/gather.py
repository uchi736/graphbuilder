import json, urllib.request, base64, collections, re
K="/home/uchida/ai-stack/llm-graph-builder/tools/kg"
def q(stmt, **p):
    req=urllib.request.Request("http://localhost:7475/db/neo4j/tx/commit",data=json.dumps({"statements":[{"statement":stmt,"parameters":p}]}).encode(),headers={"Content-Type":"application/json","Authorization":"Basic "+base64.b64encode(b"neo4j:kgb-please-change").decode()})
    r=json.load(urllib.request.urlopen(req,timeout=60)); assert not r["errors"], r["errors"]; res=r["results"][0]; return [dict(zip(res["columns"],d["row"])) for d in res["data"]]
order=json.load(open(f"{K}/chunkviz/order.json"))["order"]
SHORT={"HS-VTR-T001E.pdf":"T001E","HS-VTR-T002F.pdf":"T002F","VTR-T003D.pdf":"T003D","VTR-T004C.pdf":"T004C","VTR-T005B.pdf":"T005B","VTR-T_発行目録.pdf":"発行目録"}
picked=[("VTR-T005B.pdf",8,0.761),("VTR-T_発行目録.pdf",2,0.370),("HS-VTR-T001E.pdf",12,0.248),("VTR-T004C.pdf",1,0.222),("VTR-T003D.pdf",2,0.206),("VTR-T004C.pdf",5,0.187)]
T={r["id"]:r for r in json.load(open(f"{K}/trace_p1b.json",encoding="utf-8"))}; run=T["M09"]["runs"]["graph_vector_fulltext"]
ctx=run["context"]
out={"picked":[], "docs":{}, "starts":[], "kept":{}, "context_blocks":[], "totals":{}}
clean=lambda s:" ".join(str(s).replace("�","").split())
# 段1: 選ばれたチャンク
for doc,pos,sc in picked:
    cid=order[doc][pos-1]
    t=q("MATCH (c:Chunk {id:$id}) RETURN c.text AS t", id=cid)[0]["t"]
    out["picked"].append({"doc":SHORT[doc],"pos":pos,"score":sc,"chars":len(clean(t)),"head":clean(t)[:80],"id":cid})
# 段2: 起点エンティティ（文書ごと、選ばれたチャンクに現れる数の多い順）
bydoc=collections.defaultdict(list)
for p in out["picked"]: bydoc[p["doc"]].append(p["id"])
starts=[]
for doc,cids in bydoc.items():
    rows=q("UNWIND $ids AS id MATCH (c:Chunk {id:id})-[:HAS_ENTITY]->(e:__Entity__) WITH e, count(DISTINCT c) AS numChunks OPTIONAL MATCH (e)-[r]-(m:__Entity__) WITH e, numChunks, count(r) AS deg RETURN e.id AS id, [l IN labels(e) WHERE l<>'__Entity__'][0] AS label, numChunks, deg, left(coalesce(e.description,''),60) AS desc ORDER BY numChunks DESC, deg DESC", ids=cids)
    out["docs"][doc]={"chunks":[p["pos"] for p in out["picked"] if p["doc"]==doc],"n_start":len(rows),"labels":dict(collections.Counter(r["label"] for r in rows))}
    for r in rows: r["doc"]=doc; starts.append(r)
# 段3: 取り出された行（trace の関係・エンティティ）で各起点に接する本数
rels=run["relationships"]; ents={e["id"] for e in run["entities"]}
touch=collections.Counter()
for r in rels:
    touch[r.get("s")]+=1; touch[r.get("o")]+=1
for s in starts:
    s["retrieved"]=touch.get(s["id"],0)
    s["expanded"]= s["deg"]>0 and s["retrieved"]>0
out["starts"]=starts
out["totals"]={"start_entities":len(starts),"retrieved_entities":len(run["entities"]),"retrieved_rels":len(rels),"lines_pooled":803,"lines_kept":120,"context_chars":len(ctx),
               "hubs":[{"id":s["id"],"label":s["label"],"doc":s["doc"],"deg":s["deg"],"retrieved":s["retrieved"]} for s in starts if s["deg"]>19],
               "not_expanded":[{"id":s["id"],"label":s["label"],"deg":s["deg"]} for s in starts if not s["expanded"] and s["deg"]>0]}
# 段4・5: 文脈のブロック
blocks=[b for b in ctx.split("Document start") if b.strip()]
kept_e=collections.Counter(); kept_r=collections.Counter(); kept_lines={"ents":[],"rels":[]}
for b in blocks:
    src=re.search(r"source (\S+)", b); src=src.group(1) if src else "?"
    text=b.split("Text Content:\n",1)[1] if "Text Content:\n" in b else ""
    body=text.split("\n----\nEntities:\n")[0]
    e_lines=[l for l in (text.split("\n----\nEntities:\n")[1].split("\n----\nRelationships:\n")[0].split("\n") if "\n----\nEntities:\n" in text else []) if l.strip()]
    r_part=text.split("\n----\nRelationships:\n")[1] if "\n----\nRelationships:\n" in text else ""
    r_lines=[l for l in r_part.split("\n") if l.strip() and not l.startswith("Document end")]
    out["context_blocks"].append({"doc":SHORT.get(src,src),"chunks":body.count("\n----\n")+1 if body.strip() else 0,"text_chars":len(body),"ent_lines":len(e_lines),"rel_lines":len(r_lines)})
    for l in e_lines: kept_e[l.split(":")[0]]+=1; kept_lines["ents"].append(l)
    for l in r_lines:
        m=re.search(r" ([A-Z_]+)(?: \{\{| )", l); kept_r[m.group(1) if m else "?"]+=1; kept_lines["rels"].append(l)
out["kept"]={"ent_by_label":dict(kept_e),"rel_by_type":dict(kept_r),"sample_ents":kept_lines["ents"][:6],"sample_rels":kept_lines["rels"][:6],
             "answer_lines":[l for l in kept_lines["ents"]+kept_lines["rels"] if "REQ-VTR-T001-p2-5" in l or "REQ-VTR-T005-4-1-1" in l]}
json.dump(out,open("m09.json","w"),ensure_ascii=False,indent=1)
print(json.dumps(out["totals"],ensure_ascii=False)[:1500]); print(out["docs"]); print(out["context_blocks"]); print(out["kept"]["ent_by_label"], out["kept"]["rel_by_type"]); print(len(out["kept"]["answer_lines"]))
