# 直した問いについて、答えの文書の全チャンクを問いでリランカー（:8006）に並べ替えたときの答えチャンクの文書内順位を出す。
import sys, json, pathlib
HERE = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))
import probe_q
chunks = json.load(open(HERE / "chunks.json", encoding="utf-8"))
B = json.load(open(HERE / "out/_final/batch_fixed.json", encoding="utf-8"))
out = {}
for it in B:
    res = []
    for g in it["gold_ids"]:
        doc = chunks[g]["doc"]
        ids = sorted([i for i, c in chunks.items() if c["doc"] == doc], key=lambda i: chunks[i]["position"])
        sc = probe_q.rerank(it["question"], [chunks[i]["text"] for i in ids])
        order = sorted(range(len(ids)), key=lambda j: -sc[j])
        rank = [ids[j] for j in order].index(g) + 1
        res.append({"chunk": g, "doc": doc.replace(".pdf", ""), "position": chunks[g]["position"], "n_chunks_in_doc": len(ids),
                    "within_doc_rank": rank, "score": round(sc[ids.index(g)], 4),
                    "top3": [(chunks[ids[j]]["position"], round(sc[j], 4)) for j in order[:3]]})
    out[it["qid"]] = res
    print(it["qid"], [(r["doc"], r["position"], f"{r['within_doc_rank']}/{r['n_chunks_in_doc']}", r["score"]) for r in res], flush=True)
json.dump(out, open(HERE / "out/_final/within_doc_fixed.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
