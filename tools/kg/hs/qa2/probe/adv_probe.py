# 擁護側の実測ツール。probe_q.py の関数を再利用し、グラフ無しの定石で答えのチャンクに届くかを測る。
#   python3 adv_probe.py --batch q.json --out result.json
# q.json: [{"qid","question","gold_ids"|"gold_quotes", "tag"(任意)}]
# 出す値（答えのチャンクごと）:
#   vec_rank / bm25_rank / in_now6 / in_union6 / in_rrf6 … probe_q.py と同じ
#   rerank_all          全 612 チャンクをリランカーで並べた順位（候補の取り方に依らない上限）
#   in_u50_6 / in_u50_10 候補をベクトル上位 50 ∪ 全文上位 50 に広げてリランカー → 6 件／10 件に入るか
#   in_nb6              いまの候補（now 20 件）に同一文書の前後 3 チャンクを足してリランカー → 6 件に入るか（隣接展開）
#   in_doc2_6 / in_doc3_6  文書→節の 2 段検索。候補チャンク（vec20 ∪ bm25 20 の RRF）から文書を上位 2／3 本選び、
#                       その文書の全チャンクをリランカー → 6 件に入るか。gold_doc_rank は文書の順位、within_doc は文書内のリランカー順位
#   in_ref6             いまの候補 6 件の本文から文書番号を正規表現で拾い、その文書（コーパス内）の全チャンクを候補に足してリランカー → 6 件に入るか
#                       （規則の索引。ref_hop の代替手段）
import sys, json, re, pathlib, collections, argparse
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # hs-qa-redesign
sys.path.insert(0, str(ROOT))
import probe_q as P  # noqa: E402

DOC_ID_RE = re.compile(r"HS[-‐‑–]?(?:RU|TP|VTR[-‐]?T|VTR[-‐]?P|VTR)[-‐‑–]?\s?(\d{3})|(?<![A-Z])(?:RU|TP)[-‐‑–](\d{3})|VTR[-‐‑–]T(\d{3})", re.I)


def doc_key(docname):
    # 'HS-RU-013B.pdf' -> 'HS-RU-013', 'VTR-T004C.pdf' -> 'HS-VTR-T004', 'HS-VTR-T001E.pdf' -> 'HS-VTR-T001'
    n = docname.replace(".pdf", "")
    m = re.match(r"(HS-)?(RU|TP)-(\d{3})", n)
    if m:
        return f"HS-{m.group(2)}-{m.group(3)}"
    m = re.match(r"(HS-)?VTR-(T|P)(\d{3})", n)
    if m:
        return f"HS-VTR-{m.group(2)}{m.group(3)}"
    return None


def ids_in_text(text):
    out = set()
    t = P.norm(text).upper()
    for m in re.finditer(r"HS[-‐]?(RU|TP)[-‐]?(\d{3})", t):
        out.add(f"HS-{m.group(1)}-{m.group(2)}")
    for m in re.finditer(r"HS[-‐ ]?VTR[-‐ ]?(T|P)?[-‐]?(\d{3})", t):
        out.add(f"HS-VTR-{(m.group(1) or '')}{m.group(2)}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-full", action="store_true")
    a = ap.parse_args()
    chunks, ids, texts, emb = P.load()
    bm_uni = P.BM25([P.toks_uni(t) for t in texts])
    bm_bi = P.BM25([P.toks_bi(t) for t in texts])
    docs_of = collections.defaultdict(list)  # doc -> [idx] sorted by position
    for i, cid in enumerate(ids):
        docs_of[chunks[cid]["doc"]].append(i)
    key2doc = collections.defaultdict(list)
    for d in docs_of:
        k = doc_key(d)
        if k:
            key2doc[k].append(d)
    items = json.load(open(a.batch, encoding="utf-8"))
    results = []
    for it in items:
        qid, q = it.get("qid", "?"), it["question"]
        gold = P.resolve_gold(",".join(it.get("gold_ids", [])) or None, it.get("gold_quotes"), chunks)
        rows, top = P.probe(q, gold, chunks, ids, texts, emb, bm_uni, bm_bi, full=not a.no_full)
        qv = P.embed([q])[0]
        vec = [P.cos(qv, emb[i]) for i in ids]
        v_order, v_rank = P.ranks_from_scores(vec)
        bu = bm_uni.scores(P.toks_uni(q)); bu_order, bu_rank = P.ranks_from_scores(bu)
        # いまの候補（probe_q と同じ）
        vmax = max(vec) or 1.0; bmax = max(bu) or 1.0
        now = {i: s / vmax for i, s in enumerate(vec)}
        for i, s in enumerate(bu):
            if s > 0:
                now[i] = max(now[i], s / bmax)
        now_c = [i for i, _ in sorted(now.items(), key=lambda kv: -kv[1])[:20]]
        now_c = [i for i in now_c if now[i] >= 0.5]

        def topk(cands, k):
            if not cands:
                return []
            sc = P.rerank(q, [texts[i] for i in cands])
            order = sorted(range(len(cands)), key=lambda j: -sc[j])
            return [cands[j] for j in order[:k]], {cands[j]: r + 1 for r, j in enumerate(order)}

        # 1) 窓を 50 に
        u50 = list(dict.fromkeys(v_order[:50] + [i for i in bu_order[:50] if bu[i] > 0]))
        u50_10, _ = topk(u50, 10); u50_6 = u50_10[:6]
        # 2) 隣接展開 ±3
        nb = set(now_c)
        for i in now_c:
            d = chunks[ids[i]]["doc"]; pos = docs_of[d].index(i)
            for j in docs_of[d][max(0, pos - 3):pos + 4]:
                nb.add(j)
        nb6, _ = topk(sorted(nb), 6)
        # 3) 文書→節の 2 段
        rrf = collections.Counter()
        for L in (v_order[:20], [i for i in bu_order[:20] if bu[i] > 0]):
            for r, i in enumerate(L):
                rrf[i] += 1.0 / (60 + r + 1)
        docscore = collections.Counter()
        for i, s in rrf.items():
            d = chunks[ids[i]]["doc"]
            docscore[d] = max(docscore[d], s)
        doc_rank = [d for d, _ in docscore.most_common()]
        doc2 = [j for d in doc_rank[:2] for j in docs_of[d]]
        doc3 = [j for d in doc_rank[:3] for j in docs_of[d]]
        doc2_6, doc2_pos = topk(doc2, 6)
        doc3_6, doc3_pos = topk(doc3, 6)
        # 4) 文書番号の正規表現で参照先を引き込む（起点は now6）
        now6 = [ids.index(c) for c in top["now6"]]
        refs = set()
        for i in now6:
            for k in ids_in_text(texts[i]):
                for d in key2doc.get(k, []):
                    if d != chunks[ids[i]]["doc"]:
                        refs.add(d)
        ref_c = set(now_c)
        for d in refs:
            ref_c.update(docs_of[d])
        ref6, _ = topk(sorted(ref_c), 6)
        for r in rows:
            i = ids.index(r["chunk"]); d = r["doc"]
            r["in_u50_6"] = i in u50_6; r["in_u50_10"] = i in u50_10
            r["in_nb6"] = i in nb6
            r["gold_doc_rank"] = (doc_rank.index(d) + 1) if d in doc_rank else None
            r["in_doc2_6"] = i in doc2_6; r["in_doc3_6"] = i in doc3_6
            r["within_doc3"] = None
            if i in doc3_pos:
                same = [j for j in docs_of[d]]
                r["within_doc3"] = sorted(same, key=lambda j: doc3_pos.get(j, 10 ** 6)).index(i) + 1
            r["in_ref6"] = i in ref6
            r["n_doc_chunks"] = len(docs_of[d])
        results.append({"qid": qid, "tag": it.get("tag"), "question": q, "gold": rows,
                        "doc_rank_top3": doc_rank[:3], "refs_followed": sorted(refs),
                        "top": {k: [f"{chunks[c]['doc']}#{chunks[c]['position']}" for c in v] for k, v in top.items() if k in ("now6", "union6")},
                        "u50_6": [f"{chunks[ids[i]]['doc']}#{chunks[ids[i]]['position']}" for i in u50_6],
                        "doc3_6": [f"{chunks[ids[i]]['doc']}#{chunks[ids[i]]['position']}" for i in doc3_6],
                        "ref6": [f"{chunks[ids[i]]['doc']}#{chunks[ids[i]]['position']}" for i in ref6]})
        g = rows
        print(f"== {qid} [{it.get('tag','')}] {q[:70]}", flush=True)
        for r in g:
            print(f"   {r['doc']}#{chunks[r['chunk']]['position']} vec {r['vec_rank']} bm25 {r['bm25_rank']} all {r['rerank_all']} | now6 {int(r['in_now6'])} union6 {int(r['in_union6'])} rrf6 {int(r['in_rrf6'])} | u50_6 {int(r['in_u50_6'])} u50_10 {int(r['in_u50_10'])} nb6 {int(r['in_nb6'])} | docrank {r['gold_doc_rank']} doc2_6 {int(r['in_doc2_6'])} doc3_6 {int(r['in_doc3_6'])} within {r['within_doc3']} | ref6 {int(r['in_ref6'])} (refs {','.join(sorted(refs)) or '-'})", flush=True)
        json.dump(results, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
