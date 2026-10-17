# backend コンテナの中で実行する（読み取りだけ）。問いごとに候補の取り方 5 通りの順位と、2 段（候補 20 → リランク 6）で届くかを出す。
#   docker exec -e NEO4J_URI=neo4j://database4:7687 -i backend python - < hybrid_probe.py
import json, math, os, re, sys, urllib.request
sys.path.insert(0, "/code")
os.environ.setdefault("CHUNK_TOKENIZER", "/code/tokenizers/ruri-v3-310m/tokenizer.json")
from neo4j import GraphDatabase
from src.create_chunks import chunk_length_function
length = chunk_length_function()
EMB = "http://host.docker.internal:8003/v1/embeddings"; RR = "http://host.docker.internal:8006/v1/rerank"
SPECIAL = list('+-&|!(){}[]^"~*?:\\/')
def lucene(t):
    for ch in SPECIAL: t = t.replace(ch, " ")
    return t
def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=600))
def embed(t): return post(EMB, {"model": "cl-nagoya/ruri-v3-310m", "input": [t]})["data"][0]["embedding"]
def rerank(q, docs):
    out = [0.0] * len(docs)
    for s in range(0, len(docs), 48):
        r = post(RR, {"model": "cl-nagoya/ruri-v3-reranker-310m", "query": q, "documents": [d[:1500] for d in docs[s:s + 48]], "truncate_prompt_tokens": -1})
        for x in r["results"]: out[s + x["index"]] = x["relevance_score"]
    return out
req = json.load(open("/tmp/required_chunks.json", encoding="utf-8"))
drv = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=("neo4j", "kgb-please-change"))
with drv.session(database="neo4j") as s:
    rows = s.run("MATCH (c:Chunk) WHERE c.embedding IS NOT NULL RETURN c.id AS id, c.text AS text, c.embedding AS emb").data()
    text = {r["id"]: r["text"] for r in rows}; emb = {r["id"]: r["emb"] for r in rows}
    tok = {i: length(t) for i, t in text.items()}
    def ft(index, q):
        return [(r["id"], r["score"]) for r in s.run("CALL db.index.fulltext.queryNodes($i, $q, {limit: 2000}) YIELD node, score RETURN node.id AS id, score", i=index, q=lucene(q)).data()]
    def cos(a, b): return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) + 1e-12)
    def maxnorm(vec, full, K):
        v = vec[:K]; f = full[:K]; sc = {}
        if v:
            mv = v[0][1]
            for i, x in v: sc[i] = max(sc.get(i, 0), x / mv)
        if f:
            mf = f[0][1]
            for i, x in f: sc[i] = max(sc.get(i, 0), x / mf)
        return sorted(sc.items(), key=lambda kv: -kv[1])[:K]
    def rrf(*lists, k=60):
        sc = {}
        for L in lists:
            for r, (i, _) in enumerate(L): sc[i] = sc.get(i, 0) + 1.0 / (k + r + 1)
        return sorted(sc.items(), key=lambda kv: -kv[1])
    def need_k(rank_fn, reqs, maxk=len(rows)):
        # 必要なチャンク（要求ごとにどれか 1 つ）が全部入る最小の件数
        lo = None
        for K in list(range(1, 101)) + list(range(110, maxk + 1, 10)) + [maxk]:
            ids = {i for i, _ in rank_fn(K)}
            if all(any(x in ids for x in r) for r in reqs if r):
                return K, sum(tok[i] for i, _ in rank_fn(K))
        return None, None
    out = {}
    for qid, q in req.items():
        qv = embed(q["question"])
        vec = sorted(((i, cos(qv, e)) for i, e in emb.items()), key=lambda kv: -kv[1])
        fts = ft("keyword", q["question"]); ftc = ft("keyword_cjk", q["question"])
        def rrf_window(a, b, window, k=60):
            # 普通の RRF: それぞれの上位 window 件だけを混ぜ、窓の外は 0 点
            sc = {}
            for L in (a[:window], b[:window]):
                for r, (i, _) in enumerate(L): sc[i] = sc.get(i, 0) + 1.0 / (k + r + 1)
            return sorted(sc.items(), key=lambda kv: -kv[1])
        methods = {
            "全順位のRRF(前回)": lambda K: rrf(vec, fts)[:K],
            "RRF窓20": lambda K: rrf_window(vec, fts, 20)[:K],
            "RRF窓50": lambda K: rrf_window(vec, fts, 50)[:K],
            "RRF窓20(cjk)": lambda K: rrf_window(vec, ftc, 20)[:K],
            "和集合": lambda K: [(i, 0.0) for i in dict.fromkeys([i for i, _ in vec[:K]] + [i for i, _ in fts[:K]])],
        }
        rec = {"type": q["type"], "twin": q["twin"]}
        for name, fn in methods.items():
            K, T = need_k(fn, q["required"])
            cand = [(i, sc) for i, sc in fn(20) if name not in ("本番ハイブリッド", "cjkハイブリッド") or sc >= 0.5]
            ids = [i for i, _ in cand]
            rs = rerank(q["question"], [text[i] for i in ids]) if ids else []
            top6 = [ids[j] for j in sorted(range(len(ids)), key=lambda j: -rs[j])[:6]]
            reached = sum(1 for r in q["required"] if r and any(x in top6 for x in r))
            rec[name] = {"need_k": K, "tokens": T, "reached": reached, "required": sum(1 for r in q["required"] if r),
                         "ctx_tokens": sum(tok[i] for i in top6)}
        out[qid] = rec
        print(qid, q["type"], " | ".join(f"{n}: K={rec[n]['need_k']} 届く{rec[n]['reached']}/{rec[n]['required']}" for n in methods), flush=True)
json.dump(out, open(os.environ.get("OUT", "/tmp/hybrid_probe.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
