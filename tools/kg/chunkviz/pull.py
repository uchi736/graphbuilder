import json, urllib.request, base64
def q(stmt):
    req = urllib.request.Request("http://localhost:7475/db/neo4j/tx/commit",
        data=json.dumps({"statements":[{"statement":stmt}]}).encode(),
        headers={"Content-Type":"application/json","Authorization":"Basic "+base64.b64encode(b"neo4j:kgb-please-change").decode()})
    r = json.load(urllib.request.urlopen(req, timeout=60))
    if r.get("errors"): raise SystemExit(r["errors"])
    res = r["results"][0]; return [dict(zip(res["columns"], d["row"])) for d in res["data"]]
out = {}
out["docs"] = q("MATCH (d:Document) WHERE d.fileName IS NOT NULL OPTIONAL MATCH (d)-[:FIRST_CHUNK]->(f:Chunk) RETURN d.fileName AS doc, f.id AS first ORDER BY doc")
out["chunks"] = q("MATCH (c:Chunk) OPTIONAL MATCH (c)-[:PART_OF]->(d:Document) WITH c, collect(d.fileName) AS docs OPTIONAL MATCH (c)-[:NEXT_CHUNK]->(n:Chunk) RETURN c.id AS id, c.position AS pos, c.text AS text, docs, collect(n.id) AS next")
out["has_entity"] = q("MATCH (c:Chunk)-[:HAS_ENTITY]->(e) RETURN c.id AS chunk, elementId(e) AS eid")
out["entities"] = q("MATCH (:Chunk)-[:HAS_ENTITY]->(e) WITH DISTINCT e RETURN elementId(e) AS eid, e.id AS name, [l IN labels(e) WHERE l <> '__Entity__'][0] AS label")
out["similar"] = q("MATCH (:Chunk)-[s:SIMILAR]-(:Chunk) RETURN count(s) AS n")[0]["n"]
out["ent_rel"] = q("MATCH (a:__Entity__)-[r]->(b:__Entity__) RETURN count(r) AS n")[0]["n"]
json.dump(out, open("raw.json","w",encoding="utf-8"), ensure_ascii=False)
print({k:(len(v) if isinstance(v,list) else v) for k,v in out.items()})
