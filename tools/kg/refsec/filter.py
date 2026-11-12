"""評価用の問いの候補を、事前に決めた 3 条件で機械的に判定する（X18 の段 0）。LLM は呼ばない。

使い方: python3 filter.py candidates.json filtered.json
前提: 本番の backend（:8440、/retrieve_context）と埋め込み・リランカーが稼働中。接続情報は送らない（backend の既定の接続先＝本番グラフ）。

条件（経路が要る問い route_*）:
  1. 起点が引ける … 既定の検索（候補 20 件 → リランク上位 6 件、参照の追跡なし）の上位 6 件に、起点のチャンクが入る
  2. 答えが直接は引けない … 既定の候補 20 件に答えのチャンクが入らない。かつ、上位 6 件のどのチャンクにも答えの語がすべて揃っていない
  3. 起点に参照の一文がある … 作り方による（候補の ref が指す参照の一文が、起点のチャンクにある）
対照（control）: 答えのチャンクが上位 6 件に入る。答えの無い問い（unanswerable）: 条件なし。
参考: 全チャンクを質問で並べたときの答えと起点の順位（50 位まで）。
"""
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request

BACKEND = "http://localhost:8440"
norm = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")))


def retrieve(question, settings):
    data = urllib.parse.urlencode({"uri": "", "userName": "", "password": "", "database": "", "question": question, "document_names": "[]",
                                   "mode": "graph_vector_fulltext", "model": "gemma4", "embedding_provider": "vllm",
                                   "embedding_model": "cl-nagoya/ruri-v3-310m", "retrieval_settings": json.dumps(settings)}).encode()
    d = json.load(urllib.request.urlopen(urllib.request.Request(BACKEND + "/retrieve_context", data=data), timeout=300))
    if d.get("status") != "Success":
        raise RuntimeError(str(d)[:300])
    return d["data"]


def main():
    cands = json.load(open(sys.argv[1], encoding="utf-8"))["questions"]
    dump = json.load(open("refs_dump.json", encoding="utf-8"))
    text_of = {c["id"]: norm(c["text"]) for c in dump["chunks"]}
    out = []
    for n, c in enumerate(cands, 1):
        q = c["question"]
        order = [x["id"] for x in retrieve(q, {"candidate_k": 200, "rerank_top_n": 50, "candidate_min_score": 0.0, "reference_max": 0})["chunks"]]
        top6 = [x["id"] for x in retrieve(q, {"reference_max": 0})["chunks"]]
        top20 = [x["id"] for x in retrieve(q, {"rerank_top_n": 20, "candidate_k": 20, "reference_max": 0})["chunks"]]
        rank = lambda ids: min((order.index(i) + 1 for i in ids if i in order), default=None)
        answers, entries = set(c.get("answer_chunk_ids") or []), set(c.get("entry_chunk_ids") or [])
        keys = [norm(k) for k in c.get("answer_keys") or [] if norm(k)]
        leak = [i for i in top6 if i not in answers and keys and all(k in text_of.get(i, "") for k in keys)]
        rec = {**c, "answer_rank": rank(answers), "entry_rank": rank(entries), "entry_in_top6": bool(entries & set(top6)),
               "answer_in_top6": bool(answers & set(top6)), "answer_in_top20": bool(answers & set(top20)), "leak": leak}
        if c["type"].startswith("route"):
            ok = rec["entry_in_top6"] and not rec["answer_in_top20"] and not leak
            border = rec["entry_in_top6"] and rec["answer_in_top20"] and not rec["answer_in_top6"] and not leak
            rec["verdict"] = "採用" if ok else ("境目" if border else "不採用")
        elif c["type"] == "control":
            rec["verdict"] = "採用" if rec["answer_in_top6"] else "不採用"
        else:
            rec["verdict"] = "採用"
        out.append(rec)
        print(f"{n:3} {c['id']:5} {c['type']:15} {rec['verdict']:4} 起点 {rec['entry_rank']} 答え {rec['answer_rank']} 上位6に起点 {rec['entry_in_top6']} 候補20に答え {rec['answer_in_top20']}", flush=True)
    json.dump({"questions": out}, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    print(Counter((r["type"], r["verdict"]) for r in out))


if __name__ == "__main__":
    main()
