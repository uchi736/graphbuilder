"""新しいチャンクで、先頭の見出し（文書名・表題・項の見出し）の付け方がチャンクの選択に与える影響を調べる。"""
import json, re, collections, urllib.request, base64, unicodedata, subprocess
def post(url, body):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}), timeout=300))
def embed(t): return post("http://localhost:8003/v1/embeddings", {"model": "cl-nagoya/ruri-v3-310m", "input": t})["data"][0]["embedding"]
def rerank(query, docs):
    r = post("http://localhost:8006/v1/rerank", {"model": "cl-nagoya/ruri-v3-reranker-310m", "query": query, "documents": [d[:1200] for d in docs]})
    sc = [0.0] * len(docs)
    for x in r["results"]: sc[x["index"]] = x["relevance_score"]
    return sc
def cy(stmt):
    out = subprocess.run(["docker", "exec", "neo4j-kgb2", "cypher-shell", "-u", "neo4j", "-p", "kgb-please-change", "--format", "plain", stmt], capture_output=True, text=True).stdout
    return out
norm = lambda s: unicodedata.normalize("NFKC", " ".join(str(s).split()))
rows = json.loads(cy("MATCH (c:Chunk)-[:PART_OF]->(d:Document) WITH collect({id:c.id, doc:d.fileName, pos:c.position, text:c.text, kind:c.chunk_kind}) AS r RETURN apoc.convert.toJson(r)").strip().split("\n", 1)[1].strip().strip('"').replace('\\"', '"').replace("\\\\", "\\"))
CH = {r["id"]: r for r in rows}
exec(open("run.py", encoding="utf-8").read().split("QS = ")[0].split("FACTS = ")[1].join(["FACTS = ", ""]) if False else "FACTS = " + open("run.py", encoding="utf-8").read().split("FACTS = ")[1].split("\nQS = ")[0])
QS = {x["id"]: x for x in json.load(open("../eval_multihop.json", encoding="utf-8"))}
def variant(c, v):
    head, _, body = c["text"].partition("\n")
    m = re.match(r"^【([^｜】]*)(?:｜[^】]*)?】(.*)$", head)
    if v == "full" or not m: return c["text"]
    if v == "docno": return f"【{m.group(1)}】{m.group(2)}\n{body}"
    if v == "body": return body if body.strip() else c["text"]
ids = list(CH)
res = collections.defaultdict(lambda: {"hit": 0, "tot": 0, "docs": 0})
for qid, qq in QS.items():
    text = qq["q"]
    for v in ("full", "docno", "body"):
        sc = rerank(text, [variant(CH[i], v) for i in ids]); order = [i for i, _ in sorted(zip(ids, sc), key=lambda x: -x[1])]
        for cap in (None, 2):
            if cap:
                seen = collections.Counter(); top = []
                for i in order:
                    if seen[CH[i]["doc"]] < cap: top.append(i); seen[CH[i]["doc"]] += 1
                    if len(top) == 6: break
            else: top = order[:6]
            key = f"{v}{'・1文書2件まで' if cap else ''}"
            hits = [any(any(k in norm(CH[i]['text']) for k in ks) for i in top) for ks in FACTS[qid]]
            res[key]["hit"] += sum(hits); res[key]["tot"] += len(hits); res[key]["docs"] += len({CH[i]["doc"] for i in top})
for k, v in res.items(): print(f"{k:22s} 事実 {v['hit']}/{v['tot']}  上位6件の文書数 平均 {v['docs']/16:.1f}")
