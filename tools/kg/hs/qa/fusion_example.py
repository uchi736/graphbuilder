import json, math, os, urllib.request
from neo4j import GraphDatabase
req = json.load(open("/tmp/required_chunks.json", encoding="utf-8"))
def post(url, body):
    r = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}); return json.load(urllib.request.urlopen(r, timeout=300))
q = req["HS-20"]; need = {x for r in q["required"] for x in r}
qv = post("http://host.docker.internal:8003/v1/embeddings", {"model": "cl-nagoya/ruri-v3-310m", "input": [q["question"]]})["data"][0]["embedding"]
drv = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=("neo4j", "kgb-please-change"))
SPECIAL = list('+-&|!(){}[]^"~*?:\\/')
t = q["question"]
for ch in SPECIAL: t = t.replace(ch, " ")
with drv.session(database="neo4j") as s:
    rows = s.run("MATCH (c:Chunk)-[:PART_OF]->(d:Document) RETURN c.id AS id, c.embedding AS e, d.fileName AS f").data()
    cos = lambda a, b: sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
    vec = sorted(((r["id"], cos(qv, r["e"]), r["f"]) for r in rows), key=lambda x: -x[1])[:20]
    ft = s.run("CALL db.index.fulltext.queryNodes('keyword', $q, {limit: 20}) YIELD node, score MATCH (node)-[:PART_OF]->(d:Document) RETURN node.id AS id, score, d.fileName AS f", q=t).data()
mv = vec[0][1]; mf = ft[0]["score"]
print("ベクトル: 生の点数", round(vec[0][1], 3), "〜", round(vec[-1][1], 3), "| 1 位で割ると", 1.0, "〜", round(vec[-1][1] / mv, 3), "| 答えの文書", sum(1 for i, _, _ in vec if i in need))
print("全文: 生の点数", round(ft[0]["score"], 2), "〜", round(ft[-1]["score"], 2))
for r, x in enumerate(ft[:10], 1):
    print(f"  全文 {r} 位 {x['f'][:16]:16s} 割った値 {x['score'] / mf:.3f} {'答え' if x['id'] in need else ''}")
cut = sorted([s / mv for _, s, _ in vec])[0]
print("本番の混ぜ方で 20 件に残る下限（ベクトル 20 位の割った値）:", round(cut, 3))
