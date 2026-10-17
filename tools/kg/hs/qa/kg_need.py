"""問いごとに「ナレッジグラフが要るか」を数える。手元のデータだけで動く（埋め込み :8003 とリランカー :8006 は使う）。
  1. 届くのに要る top-K: ruri のベクトル検索の順で、必要なチャンクが全部入る件数と、そこまでのトークン数（分割時に ruri で数えた値）
  2. グラフの道: 56 本のグラフの書き出しで、起点のチャンクから答えのチャンクへ、同じエンティティ・リレーション 1 本・参照リンクのどれかで届くか
  3. 集約の照会: 質問の中の名前に一致するエンティティから、付いているチャンクの文書を集める（毎回同じ結果になる）
"""
import json, re, sys, math, collections, unicodedata, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from check_hs_questions import rerank, norm
import urllib.request
def embed(texts):
    out = []
    for st in range(0, len(texts), 32):
        body = json.dumps({"model": "cl-nagoya/ruri-v3-310m", "input": [t[:2000] for t in texts[st:st + 32]]}).encode()
        req = urllib.request.Request("http://localhost:8003/v1/embeddings", data=body, headers={"Content-Type": "application/json"})
        out += [x["embedding"] for x in sorted(json.load(urllib.request.urlopen(req, timeout=300))["data"], key=lambda x: x["index"])]
    return out
def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) + 1e-9)
S = "/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad"
HS = pathlib.Path(__file__).parent.parent
cc = json.load(open(f"{S}/frame-impl/out2/chunk_check.json", encoding="utf-8"))
docs = json.load(open(HS / "docs.json", encoding="utf-8")); rename = {pathlib.Path(x["src"]).name: x["name"] for x in docs}
pool = [{"doc": rename.get(n, n), "orig": n, "pos": i, "text": c["text"], "n": norm(c["text"]), "tok": c["tokens"]}
        for n, cs in cc["chunks"].items() if not n.endswith("::meta") for i, c in enumerate(cs)]
vecs = embed([c["text"] for c in pool])
dump = json.load(open(HS / "runs/full56_edc_quality/graph_dump_linked.json", encoding="utf-8"))
dchunks = {c["id"]: dict(c, n=norm(c["text"])) for c in dump["chunks"]}
ents = {e["id"]: e for e in dump["entities"]}
c2e = collections.defaultdict(set); e2c = collections.defaultdict(set)
for h in dump["has_entity"]: c2e[h["chunk"]].add(h["entity"]); e2c[h["entity"]].add(h["chunk"])
adj = collections.defaultdict(set)
for r in dump["relationships"]: adj[r["source"]].add(r["target"]); adj[r["target"]].add(r["source"])
cref = collections.defaultdict(set)
for r in dump["refers_to"]: cref[r["source"]].add(r["target"])
qs = json.load(open(pathlib.Path(__file__).parent / "questions_hs.json", encoding="utf-8"))["questions"]
def find_pool(doc, quote): return [k for k, c in enumerate(pool) if c["orig"] == doc and norm(quote) in c["n"]]
def find_dump(doc, quote):
    d = rename.get(doc, doc); return [i for i, c in dchunks.items() if c["fileName"] == d and norm(quote) in c["n"]]
HUB = {"HS-10": ["SACM645"], "HS-11": ["SCM440H"], "HS-15": ["CR3C2"], "HS-16": ["Y型B号"], "HS-17": ["JISZ2201"],
       "HS-18": ["FCD400"], "HS-19": ["窒化"], "HS-20": ["QC-KAI-027"], "HS-23": ["蛍光浸透", "ザイクロ"]}
DOCNUM = re.compile(r"(RU|TP)-?(\d{3})|VTR-?(T\d{3})")
def docnum(name):
    m = DOCNUM.search(unicodedata.normalize("NFKC", name).replace(" ", ""))
    return (f"{m.group(1)}-{m.group(2)}" if m.group(1) else m.group(3)) if m else name
out = []
for q in qs:
    if q.get("twin_of"): continue
    qv = embed([q["question"]])[0]
    order = sorted(range(len(pool)), key=lambda k: -cos(qv, vecs[k])); vrank = {k: r + 1 for r, k in enumerate(order)}
    keys = [norm(x) for x in q.get("answer_keys") or []]
    if q["type"] == "agg":
        reqs = [find_pool(e["doc"], e["quote"]) for e in q["evidence"]]
    else:
        ans = [k for k in find_pool(q["answer_doc"], q["answer_quote"]) if all(x in pool[k]["n"] for x in keys)] or find_pool(q["answer_doc"], q["answer_quote"])
        reqs = [ans]
    need_k = max(min(vrank[k] for k in ks) for ks in reqs if ks)
    tok_at = lambda K: sum(pool[k]["tok"] for k in order[:K])
    # 本番の文脈（候補 20 → リランク 6）のトークン数
    cand = order[:20]; rs = rerank(q["question"], [pool[k]["text"] for k in cand]); top6 = [cand[i] for i in sorted(range(20), key=lambda i: -rs[i])[:6]]
    rec = {"qid": q["qid"], "type": q["type"], "need_k": need_k, "tokens_need_k": tok_at(need_k), "tokens_top6": sum(pool[k]["tok"] for k in top6),
           "required_tokens": sum(pool[min(ks, key=lambda k: vrank[k])]["tok"] for ks in reqs if ks)}
    # グラフの道（起点 → 答え）
    if q["type"] in ("ref1", "ent1", "ent2"):
        E = find_dump(q["entry_doc"], q["entry_quote"]); A = find_dump(q["answer_doc"], q["answer_quote"])
        eE = set().union(*[c2e[i] for i in E]) if E else set(); eA = set().union(*[c2e[i] for i in A]) if A else set()
        shared = eE & eA
        one_rel = {(a, b) for a in eE for b in adj[a] if b in eA}
        link = any(set(A) & cref[i] for i in E)
        two = set()
        if not shared and not one_rel:
            for a in eE:
                for m in adj[a]:
                    for b in adj[m]:
                        if b in eA: two.add((a, m, b))
        nm = lambda e: ents[e]["props"].get("id")
        rec["route"] = {"shared_entity": sorted({nm(e) for e in shared})[:6], "one_relation": sorted({f"{nm(a)}→{nm(b)}" for a, b in one_rel})[:4],
                        "two_relations": sorted({f"{nm(a)}→{nm(m)}→{nm(b)}" for a, m, b in two})[:3], "chunk_link": link}
    if q["type"] == "agg":
        hub = [norm(h).upper() for h in HUB[q["qid"]]]
        hit_e = [e for e in ents if any(h in norm(ents[e]["props"].get("id") or "").upper() for h in hub)]
        hit_docs = sorted({docnum(dchunks[c]["fileName"]) for e in hit_e for c in e2c[e] if c in dchunks})
        want = [alts[0] for alts in q["answer_items"]]
        got = [w for w in want if any(w in d for d in hit_docs)]
        rec["graph_query"] = {"hub": HUB[q["qid"]], "entities": len(hit_e), "docs": hit_docs, "found": f"{len(got)}/{len(want)}",
                              "extra": [d for d in hit_docs if not any(w in d for w in want)]}
    out.append(rec)
    print(json.dumps(rec, ensure_ascii=False))
json.dump(out, open(pathlib.Path(__file__).parent / "kg_need.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
