"""起点チャンク → エンティティ → 1 ホップ先 → その出典チャンク を候補に足し、ruri でリランクして答えのチャンクが上位に来るかを調べる。"""
import json, urllib.request, base64, unicodedata, re, collections
NEO = "http://localhost:7475/db/neo4j/tx/commit"; AUTH = "Basic " + base64.b64encode(b"neo4j:kgb-please-change").decode()
def q(stmt, **p):
    req = urllib.request.Request(NEO, data=json.dumps({"statements": [{"statement": stmt, "parameters": p}]}).encode(), headers={"Content-Type": "application/json", "Authorization": AUTH})
    r = json.load(urllib.request.urlopen(req, timeout=120)); assert not r["errors"], r["errors"]
    res = r["results"][0]; return [dict(zip(res["columns"], d["row"])) for d in res["data"]]
def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=300))
def embed(t): return post("http://localhost:8003/v1/embeddings", {"model": "cl-nagoya/ruri-v3-310m", "input": t})["data"][0]["embedding"]
def rerank(query, docs):
    sc = [0.0] * len(docs)
    for i in range(0, len(docs), 200):
        r = post("http://localhost:8006/v1/rerank", {"model": "cl-nagoya/ruri-v3-reranker-310m", "query": query, "documents": [d[:1200] for d in docs[i:i + 200]]})
        for x in r["results"]: sc[i + x["index"]] = x["relevance_score"]
    return sc
norm = lambda s: unicodedata.normalize("NFKC", " ".join(str(s).replace("�", "").split()))
def lucene(s):
    for ch in '+-&|!(){}[]^"~*?:\\/': s = s.replace(ch, " ")
    return " ".join(s.split())
FACTS = {"M01": [["50kPa", "50 kPa"]], "M02": [["10分"]], "M03": [["0.8"]], "M04": [["±3%", "3%以内"]], "M05": [["02313"]], "M06": [["00862"]],
         "M07": [["-10%", "−10%", "0~-10", "0〜-10", "0~−10"]], "M08": [["6点"], ["02313"]], "M09": [["2点"], ["4台"]], "M10": [["検査部門長"]],
         "M11": [["027"], ["00862"], ["02313"]], "M12": [["+45"]], "M13": [["回転機事業部", "過給機部検査課"]], "M14": [["取付ない", "取り付けない", "取付けない"], ["6点"]],
         "M15": [["±2%", "2%以内"], ["C側"]], "M16": [["T側"]]}
QS = {x["id"]: x for x in json.load(open("../eval_multihop.json", encoding="utf-8"))}
CH = {r["id"]: r for r in q("MATCH (c:Chunk)-[:PART_OF]->(d:Document) RETURN c.id AS id, d.fileName AS doc, c.position AS pos, c.text AS text")}
for c in CH.values(): c["n"] = norm(c["text"])
SIX = {i for i, c in CH.items() if c["doc"] != "TS3-5.pdf"}
# エンティティとチャンクの対応、エンティティ間の隣
E2C = collections.defaultdict(set); C2E = collections.defaultdict(set)
for r in q("MATCH (c:Chunk)-[:HAS_ENTITY]->(e:__Entity__) RETURN c.id AS c, elementId(e) AS e"): E2C[r["e"]].add(r["c"]); C2E[r["c"]].add(r["e"])
NB = collections.defaultdict(set)
for r in q("MATCH (a:__Entity__)-[r]-(b:__Entity__) WHERE NOT type(r) IN ['HAS_ENTITY','PART_OF'] RETURN elementId(a) AS a, elementId(b) AS b"): NB[r["a"]].add(r["b"])
out = []
for qid, qq in QS.items():
    text = qq["q"]; qv = embed(text)
    vec = q("CALL db.index.vector.queryNodes('vector', 20, $qv) YIELD node, score RETURN node.id AS id, score", qv=qv)
    ft = q("CALL db.index.fulltext.queryNodes('keyword', $q, {limit:20}) YIELD node, score RETURN node.id AS id, score", q=lucene(text))
    cand = {}
    for rows in (vec, ft):
        mx = max([r["score"] for r in rows] or [1])
        for r in rows: cand[r["id"]] = max(cand.get(r["id"], 0), r["score"] / mx)
    cids = list(cand); sc = rerank(text, [CH[c]["text"] for c in cids])
    seeds = [c for c, _ in sorted(zip(cids, sc), key=lambda x: -x[1])[:6]]
    # 展開: 起点チャンクのエンティティ（0 ホップ）と 1 ホップ先、それぞれの出典チャンク
    ents0 = set().union(*[C2E[c] for c in seeds]); ents1 = set().union(*[NB[e] for e in ents0]) - ents0
    def chunks_of(ents, maxdeg=None): return set().union(*[E2C[e] for e in ents if maxdeg is None or len(E2C[e]) <= maxdeg]) if ents else set()
    pools = {"起点 6 件のみ": set(seeds),
             "0 ホップの出典まで": set(seeds) | chunks_of(ents0),
             "1 ホップの出典まで": set(seeds) | chunks_of(ents0) | chunks_of(ents1),
             "1 ホップ、ハブ除外": set(seeds) | chunks_of(ents0, 5) | chunks_of(ents1, 5),
             "全チャンク": set(CH)}
    rec = {"id": qid, "pool": {}, "seed_hit": []}
    for name, pool in pools.items():
        pool = list(pool); s2 = rerank(text, [CH[c]["text"] for c in pool]); order = [c for c, _ in sorted(zip(pool, s2), key=lambda x: -x[1])]
        ranks = []
        for keys in FACTS[qid]:
            hit = [i + 1 for i, c in enumerate(order) if any(k in CH[c]["n"] for k in keys)]
            ranks.append(hit[0] if hit else None)
        rec["pool"][name] = {"size": len(pool), "six": len([c for c in pool if c in SIX]), "ranks": ranks}
    out.append(rec)
    print(qid, {k: (v["size"], v["ranks"]) for k, v in rec["pool"].items()}, flush=True)
json.dump(out, open("result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
