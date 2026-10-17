#!/usr/bin/env python3
"""本番の検索の近似で、各問いの答え（集約は証拠）のチャンクが文脈に入るかを数える。標準ライブラリのみ。
近似: ruri-v3-310m（:8003、プレフィックスなし。backend と同じ呼び方）のベクトル検索で候補 20 件 → リランカー（:8006）で並べ替えて上位 6 件。
本番は全文索引も足す hybrid で、点数 0.5 未満を捨てる。ここでは全文索引を足していない。
使い方: python3 prod_like_reach.py <chunk_check.json> <questions_hs.json> <check_v1.json> <出力>
"""
import json, re, sys, math, unicodedata, urllib.request
from check_hs_questions import rerank, norm
EMB_URL = "http://localhost:8003/v1/embeddings"; EMB_MODEL = "cl-nagoya/ruri-v3-310m"
CAND_K, TOP_N = 20, 6

def embed(texts):
    out = []
    for s in range(0, len(texts), 32):
        body = json.dumps({"model": EMB_MODEL, "input": [t[:2000] for t in texts[s:s + 32]]}).encode()
        req = urllib.request.Request(EMB_URL, data=body, headers={"Content-Type": "application/json"})
        out += [x["embedding"] for x in sorted(json.load(urllib.request.urlopen(req, timeout=300))["data"], key=lambda x: x["index"])]
    return out

def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) + 1e-9)

d = json.load(open(sys.argv[1], encoding="utf-8"))
pool = [{"doc": n, "pos": i, "text": c["text"], "n": norm(c["text"])} for n, cs in d["chunks"].items() if not n.endswith("::meta") for i, c in enumerate(cs)]
vecs = embed([c["text"] for c in pool])
qs = json.load(open(sys.argv[2], encoding="utf-8"))["questions"]
prev = {r["qid"]: r for r in json.load(open(sys.argv[3], encoding="utf-8"))["results"]}
res = []
for q in qs:
    qv = embed([q["question"]])[0]
    sims = [cos(qv, v) for v in vecs]
    order = sorted(range(len(pool)), key=lambda k: -sims[k])
    vrank = {k: r + 1 for r, k in enumerate(order)}
    cand = order[:CAND_K]
    rs = rerank(q["question"], [pool[k]["text"] for k in cand])
    top = [cand[i] for i in sorted(range(len(cand)), key=lambda i: -rs[i])[:TOP_N]]
    find = lambda doc, quote: [k for k, c in enumerate(pool) if c["doc"] == doc and norm(quote) in c["n"]]
    rec = {"qid": q["qid"], "type": q["type"]}
    if q["type"] == "agg":
        ev = [find(e["doc"], e["quote"]) for e in q["evidence"]]
        rec["docs"] = len(ev)
        rec["vector_ranks"] = [min((vrank[k] for k in ks), default=None) for ks in ev]
        rec["docs_in_context"] = sum(1 for ks in ev if set(ks) & set(top))
        rec["kg_needed"] = rec["docs_in_context"] < rec["docs"]
    else:
        keys = [norm(x) for x in q.get("answer_keys", [])]
        ans = [k for k in find(q["answer_doc"], q["answer_quote"]) if all(x in pool[k]["n"] for x in keys)]
        rec["answer_vector_rank"] = min((vrank[k] for k in ans), default=None)
        rec["answer_in_context"] = bool(set(ans) & set(top))
        if q["type"] in ("ref1", "ent1", "ent2"):
            ent = find(q["entry_doc"], q["entry_quote"])
            rec["entry_vector_rank"] = min((vrank[k] for k in ent), default=None)
            rec["entry_in_context"] = bool(set(ent) & set(top))
            rec["kg_needed"] = rec["entry_in_context"] and not rec["answer_in_context"]
        if q["type"] == "confusable":
            trap = find(q["trap_doc"], q["trap_quote"])
            rec["trap_in_context"] = bool(set(trap) & set(top))
    rec["context_top6"] = [f"{pool[k]['doc'][:16]}#{pool[k]['pos']}" for k in top]
    res.append(rec)
    print(rec["qid"], rec["type"], {k: v for k, v in rec.items() if k not in ("qid", "type", "context_top6")})
json.dump(res, open(sys.argv[4], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
