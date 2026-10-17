# 問いに対して、Graph Builder の検索（いまの設定）とその直し方を手元で再現し、答えのチャンクの順位を測る。
# Neo4j は使わない（chunks.json と埋め込みのキャッシュで計算）。埋め込みは ruri (:8003)、リランカーは ruri-reranker (:8006)。
# どちらも稼働中のサービスを呼ぶだけで、コンテナの起動はしない。
#
#   python3 probe_q.py --q "質問文" --gold <chunk_id>[,<chunk_id>...]            # 答えのチャンク ID で指定
#   python3 probe_q.py --q "質問文" --quote "本文の一節"[ --quote "..."]          # 本文の一節で指定（含むチャンクを答えとする）
#   python3 probe_q.py --batch questions.json                                     # [{"qid","question","gold_ids"|"gold_quotes"}] をまとめて
#   --json で機械可読にする
#
# 出す値（答えのチャンクごと）:
#   vec_rank    ベクトル検索（コサイン）での順位（1 始まり、全 612 チャンク）
#   bm25_rank   全文検索の近似（文字 1 字区切り、Lucene 標準アナライザの CJK の挙動に寄せた BM25）での順位
#   bm25cjk_rank 全文検索の近似（文字 2 字区切り、cjk アナライザ相当）での順位
#   in_now6     いまの Graph Builder の候補（ベクトルと全文をそれぞれ 1 位で割って最大値、上位 20）→ リランカーで 6 件、に入るか
#   in_union6   直した候補（ベクトル上位 20 ∪ 全文上位 20）→ リランカーで 6 件、に入るか
#   in_rrf6     直した候補（各上位 20 の RRF、k=60、上位 20）→ リランカーで 6 件、に入るか
#   rerank_all  全 612 チャンクをリランカーで並べたときの順位（候補の取り方に依らない上限）。--full で計算（遅い）
import argparse, json, math, pathlib, re, sys, unicodedata, urllib.request, collections

HERE = pathlib.Path(__file__).resolve().parent
EMB_URL = "http://localhost:8003/v1/embeddings"; EMB_MODEL = "cl-nagoya/ruri-v3-310m"
RR_URL = "http://localhost:8006/v1/rerank"; RR_MODEL = "cl-nagoya/ruri-v3-reranker-310m"
CACHE = HERE / "emb_cache.json"


def post(url, body, timeout=600):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def embed(texts):
    out = []
    for s in range(0, len(texts), 32):
        r = post(EMB_URL, {"model": EMB_MODEL, "input": texts[s:s + 32]})
        out.extend([x["embedding"] for x in sorted(r["data"], key=lambda x: x["index"])])
    return out


def rerank(q, docs):
    scores = [0.0] * len(docs)
    for s in range(0, len(docs), 48):
        r = post(RR_URL, {"model": RR_MODEL, "query": q, "documents": [d[:1500] for d in docs[s:s + 48]], "truncate_prompt_tokens": -1})
        for x in r["results"]:
            scores[s + x["index"]] = x["relevance_score"]
    return scores


def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) + 1e-12)


def norm(t):
    return unicodedata.normalize("NFKC", t or "").lower()


def toks_uni(t):
    # Lucene の標準アナライザは CJK を 1 字ずつ、英数字は語で切る
    t = norm(t)
    return re.findall(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*|[぀-ヿ㐀-鿿豈-﫿]", t)


def toks_bi(t):
    t = norm(t)
    out = re.findall(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*", t)
    for run in re.findall(r"[぀-ヿ㐀-鿿豈-﫿]+", t):
        out.extend(run[i:i + 2] for i in range(len(run) - 1)) if len(run) > 1 else out.append(run)
    return out


class BM25:
    def __init__(self, docs_tokens, k1=1.2, b=0.75):
        self.k1, self.b = k1, b
        self.N = len(docs_tokens)
        self.tf = [collections.Counter(t) for t in docs_tokens]
        self.dl = [len(t) for t in docs_tokens]
        self.avg = sum(self.dl) / max(1, self.N)
        df = collections.Counter()
        for c in self.tf:
            df.update(c.keys())
        self.idf = {w: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for w, n in df.items()}

    def scores(self, q_tokens):
        out = []
        for tf, dl in zip(self.tf, self.dl):
            s = 0.0
            for w in q_tokens:
                if w in tf:
                    f = tf[w]
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg))
            out.append(s)
        return out


def load():
    chunks = json.load(open(HERE / "chunks.json", encoding="utf-8"))
    ids = sorted(chunks, key=lambda i: (chunks[i]["doc"], chunks[i]["position"]))
    texts = [chunks[i]["text"] for i in ids]
    if CACHE.exists():
        emb = json.load(open(CACHE))
        if set(emb) != set(ids):
            emb = None
    else:
        emb = None
    if emb is None:
        vecs = embed(texts)
        emb = dict(zip(ids, vecs))
        json.dump(emb, open(CACHE, "w"))
    return chunks, ids, texts, emb


def ranks_from_scores(scores):
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    rank = [0] * len(scores)
    for r, i in enumerate(order):
        rank[i] = r + 1
    return order, rank


def probe(question, gold_set, chunks, ids, texts, emb, bm_uni, bm_bi, full=False):
    qv = embed([question])[0]
    vec = [cos(qv, emb[i]) for i in ids]
    v_order, v_rank = ranks_from_scores(vec)
    bu = bm_uni.scores(toks_uni(question)); bu_order, bu_rank = ranks_from_scores(bu)
    bb = bm_bi.scores(toks_bi(question)); bb_order, bb_rank = ranks_from_scores(bb)
    # いまの Graph Builder: 全文はヒットしたものだけ、各 1 位で割って max、上位 20
    vmax = max(vec) or 1.0; bmax = max(bu) or 1.0
    now = {}
    for i, s in enumerate(vec):
        now[i] = s / vmax
    for i, s in enumerate(bu):
        if s > 0:
            now[i] = max(now[i], s / bmax)
    now_c = [i for i, _ in sorted(now.items(), key=lambda kv: -kv[1])[:20]]
    now_c = [i for i in now_c if now[i] >= 0.5]
    union_c = list(dict.fromkeys(v_order[:20] + bu_order[:20]))
    rrf = collections.Counter()
    for L in (v_order[:20], bu_order[:20]):
        for r, i in enumerate(L):
            rrf[i] += 1.0 / (60 + r + 1)
    rrf_c = [i for i, _ in rrf.most_common(20)]

    def top6(cands):
        if not cands:
            return []
        sc = rerank(question, [texts[i] for i in cands])
        return [cands[j] for j in sorted(range(len(cands)), key=lambda j: -sc[j])[:6]]

    now6, union6, rrf6 = top6(now_c), top6(union_c), top6(rrf_c)
    rr_all = None
    if full:
        sc = rerank(question, texts); _, rr_all = ranks_from_scores(sc)
    rows = []
    for gid in gold_set:
        i = ids.index(gid)
        rows.append({"chunk": gid, "doc": chunks[gid]["doc"], "section": chunks[gid]["section"],
                     "vec_rank": v_rank[i], "bm25_rank": bu_rank[i] if bu[i] > 0 else None, "bm25cjk_rank": bb_rank[i] if bb[i] > 0 else None,
                     "in_now6": i in now6, "in_union6": i in union6, "in_rrf6": i in rrf6,
                     "rerank_all": rr_all[i] if rr_all else None})
    top = {"now6": [ids[i] for i in now6], "union6": [ids[i] for i in union6], "rrf6": [ids[i] for i in rrf6],
           "vec_top6": [ids[i] for i in v_order[:6]], "bm25_top6": [ids[i] for i in bu_order[:6]]}
    return rows, top


def resolve_gold(args_gold, quotes, chunks):
    gold = []
    if args_gold:
        for g in args_gold.split(","):
            g = g.strip()
            if g not in chunks:
                sys.exit(f"unknown chunk id: {g}")
            gold.append(g)
    for qt in quotes or []:
        key = re.sub(r"\s+", "", norm(qt))
        hits = [i for i, c in chunks.items() if key in re.sub(r"\s+", "", norm(c["text"]))]
        if not hits:
            sys.exit(f"quote not found in any chunk: {qt[:40]}")
        gold.extend(hits)
    return list(dict.fromkeys(gold))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q"); ap.add_argument("--gold"); ap.add_argument("--quote", action="append")
    ap.add_argument("--batch"); ap.add_argument("--json", action="store_true"); ap.add_argument("--full", action="store_true")
    a = ap.parse_args()
    chunks, ids, texts, emb = load()
    bm_uni = BM25([toks_uni(t) for t in texts]); bm_bi = BM25([toks_bi(t) for t in texts])
    items = []
    if a.batch:
        for it in json.load(open(a.batch, encoding="utf-8")):
            gold = resolve_gold(",".join(it.get("gold_ids", [])) or None, it.get("gold_quotes"), chunks)
            items.append((it.get("qid", "?"), it["question"], gold))
    else:
        items.append(("q", a.q, resolve_gold(a.gold, a.quote, chunks)))
    results = []
    for qid, q, gold in items:
        rows, top = probe(q, gold, chunks, ids, texts, emb, bm_uni, bm_bi, full=a.full)
        results.append({"qid": qid, "question": q, "gold": rows, "top": top})
        if not a.json:
            print(f"== {qid} {q}")
            for r in rows:
                print(f"  {r['doc']} | {r['section'][:60]} | vec {r['vec_rank']} | bm25 {r['bm25_rank']} | cjk {r['bm25cjk_rank']} | "
                      f"now6 {'o' if r['in_now6'] else 'x'} union6 {'o' if r['in_union6'] else 'x'} rrf6 {'o' if r['in_rrf6'] else 'x'}"
                      + (f" | rerank_all {r['rerank_all']}" if r['rerank_all'] else ""))
            print("  union6:", "; ".join(f"{chunks[i]['doc']}#{chunks[i]['position']}" for i in top["union6"]))
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
