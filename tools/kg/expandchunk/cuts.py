"""フォークで変えていない本家の足切りが、16 問でどれだけ効いているかを測る。"""
import json, collections, math
exec(open("run.py", encoding="utf-8").read().split("out = []")[0])
ENT = {r["e"]: r for r in q("MATCH (e:__Entity__) RETURN elementId(e) AS e, e.id AS id, coalesce(e.description,'') AS d, e.embedding AS emb")}
def cos(a, b): return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
def rel_text(r): return f'{r["aid"]} {r["ad"]} {r["t"]} {r["bid"]} {r["bd"]} {r["p"]}'
REL_Q = """MATCH (e) WHERE elementId(e)=$eid
CALL (e) { OPTIONAL MATCH path=(e)(()-[rels:!HAS_ENTITY&!PART_OF]-()){0,%d}(:!Chunk&!Document&!__Community__) RETURN path LIMIT %d }
UNWIND relationships(path) AS r
RETURN DISTINCT elementId(r) AS rid"""
ALL_Q = """MATCH (e) WHERE elementId(e)=$eid MATCH (e)-[r]-(m) WHERE NOT type(r) IN ['HAS_ENTITY','PART_OF'] AND NOT m:Chunk AND NOT m:Document
RETURN DISTINCT elementId(r) AS rid"""
RT = {r["rid"]: r for r in q("MATCH (a:__Entity__)-[r]->(b:__Entity__) RETURN elementId(r) AS rid, a.id AS aid, coalesce(a.description,'') AS ad, type(r) AS t, b.id AS bid, coalesce(b.description,'') AS bd, apoc.text.join([k IN keys(r) WHERE k<>'embedding' | k+':'+toString(r[k])], ' ') AS p")}
summary = collections.Counter(); detail = []
for qid, qq in QS.items():
    text = qq["q"]; qv = embed(text)
    vec = q("CALL db.index.vector.queryNodes('vector', 20, $qv) YIELD node, score RETURN node.id AS id, score", qv=qv)
    ft = q("CALL db.index.fulltext.queryNodes('keyword', $q, {limit:20}) YIELD node, score RETURN node.id AS id, score", q=lucene(text))
    cand = {}
    for rows in (vec, ft):
        mx = max([r["score"] for r in rows] or [1])
        for r in rows: cand[r["id"]] = max(cand.get(r["id"], 0), r["score"] / mx)
    top20 = sorted(cand.items(), key=lambda x: -x[1])[:20]
    kept = [c for c, s in top20 if s >= 0.5]; dropped = [c for c, s in top20 if s < 0.5]
    def fact_hits(cs): return [any(any(k in CH[c]["n"] for k in keys) for c in cs) for keys in FACTS[qid]]
    lost_by_thr = [i for i, (a, b) in enumerate(zip(fact_hits(kept), fact_hits([c for c, _ in top20]))) if b and not a]
    sc = rerank(text, [CH[c]["text"] for c in kept]); seeds = [c for c, _ in sorted(zip(kept, sc), key=lambda x: -x[1])[:6]]
    sc2 = rerank(text, [CH[c]["text"] for c, _ in top20]); seeds_nothr = [c for c, _ in sorted(zip([c for c, _ in top20], sc2), key=lambda x: -x[1])[:6]]
    seed_diff = [i for i, (a, b) in enumerate(zip(fact_hits(seeds), fact_hits(seeds_nothr))) if a != b]
    # 起点エンティティ（文書ごと）
    bydoc = collections.defaultdict(list)
    for c in seeds: bydoc[CH[c]["doc"]].append(c)
    starts = []; over40 = 0
    for doc, cs in bydoc.items():
        cnt = collections.Counter(e for c in cs for e in C2E[c])
        if len(cnt) > 40: over40 += 1
        starts += [e for e, _ in cnt.most_common(40)]
    sims = {e: cos(qv, ENT[e]["emb"]) for e in set(starts) if ENT[e]["emb"]}
    n_hi = sum(1 for s in sims.values() if s > 0.9); n_lo = sum(1 for s in sims.values() if s < 0.3)
    # 経路の上限: 実際の取り出し（LIMIT あり）と、1 ホップの全件
    got = set(); full = set(); capped = 0
    for e in set(starts):
        s = sims.get(e, 0.5)
        hops, lim = (2, 40) if s > 0.9 else ((1, 20) if s >= 0.3 else (0, 1))
        g = {r["rid"] for r in q(REL_Q % (hops, lim), eid=e)}; a = {r["rid"] for r in q(ALL_Q, eid=e)}
        got |= g; full |= a
        if len(a) > lim - 1: capped += 1
    def ans_rels(rids): return [[rid for rid in rids if rid in RT and any(k in norm(rel_text(RT[rid])) for k in keys)] for keys in FACTS[qid]]
    lost_rel_facts = [i for i, (gg, aa) in enumerate(zip(ans_rels(got), ans_rels(full))) if aa and not gg]
    rec = {"id": qid, "cand": len(top20), "below05": len(dropped), "lost_by_thr": lost_by_thr, "seed_diff": seed_diff, "docs": len(bydoc), "starts": len(set(starts)), "doc_over40": over40,
           "sim_hi": n_hi, "sim_lo": n_lo, "capped_starts": capped, "rels_got": len(got), "rels_1hop_all": len(full), "lost_rel_facts": lost_rel_facts}
    detail.append(rec); print(rec, flush=True)
json.dump(detail, open("cuts.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
