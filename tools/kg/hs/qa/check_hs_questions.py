#!/usr/bin/env python3
"""HS 基準の問い（questions_hs.json）を、実物のチャンクと稼働中のリランカーで機械的に点検する。

使い方（ホストで実行。標準ライブラリのみ）:
    python3 check_hs_questions.py <chunk_check.json> <questions_hs.json> <出力 JSON>

チャンクは、取り込みと同じ分割（枠を表として読む経路、512 トークン）で作った chunk_check.json のもの（613 チャンク）。
判定は synth/check_synth.py と同じ:
  経路が要る問い（ref1 / ent1 / ent2）
    (a) 起点のチャンクが、質問で並べ替えて 3 位以内
    (b) 答えのチャンクが、チャンク数の 2 割（最低 15）より下（参考に 20 位より下も出す）
    (c) 起点から答えへ道がある: 参照の一文が起点のチャンクにある、またはつなぎのエンティティの名前が起点と答え（2 回なら中継）のチャンクにある
    ほかに、上位 6 件に答えの語をすべて含む別のチャンクが無いこと、答えの語が質問と起点のチャンクに無いこと
  直接の問い（control）: 答えのチャンクが 3 位以内
  取り違え（confusable）: 答えのチャンクの順位と、わなのチャンクの順位を出す（答えがわなより上であること）
  集約（agg）: 証拠のチャンクの順位を出し、上位 5 件・10 件で何本の基準に届くかを数える。
               上位 10 件で全部の基準に届かないものを「集約に経路が要る」とする
"""
import json, re, sys, unicodedata, urllib.request

RERANK_URL = "http://localhost:8006/v1/rerank"
RERANK_MODEL = "cl-nagoya/ruri-v3-reranker-310m"
norm = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")))


def rerank(query, docs):
    scores = [0.0] * len(docs)
    for start in range(0, len(docs), 48):
        batch = [d[:1500] for d in docs[start:start + 48]]
        body = json.dumps({"model": RERANK_MODEL, "query": query, "documents": batch, "truncate_prompt_tokens": -1}).encode()
        req = urllib.request.Request(RERANK_URL, data=body, headers={"Content-Type": "application/json"})
        for x in json.load(urllib.request.urlopen(req, timeout=300))["results"]:
            scores[start + x["index"]] = x["relevance_score"]
    return scores


def main():
    chunk_path, q_path, out_path = sys.argv[1:4]
    d = json.load(open(chunk_path, encoding="utf-8"))
    pool = []
    for name, cs in d["chunks"].items():
        if name.endswith("::meta"):
            continue
        for i, c in enumerate(cs):
            pool.append({"doc": name, "pos": i, "text": c["text"], "n": norm(c["text"]), "kind": c.get("kind")})
    N = len(pool)
    below = max(int(N * 0.2), 15)
    qs = json.load(open(q_path, encoding="utf-8"))["questions"]
    results = []
    for q in qs:
        errs, rec = [], {"qid": q["qid"], "type": q["type"]}
        t = q["type"]
        find = lambda doc, quote: [k for k, c in enumerate(pool) if c["doc"] == doc and norm(quote) in c["n"]]
        keys = [norm(k) for k in q.get("answer_keys", [])]
        nq = norm(q["question"])
        for k in keys:
            if k in nq:
                errs.append(f"答えの語が質問に入っている: {k}")
        scores = rerank(q["question"], [c["text"] for c in pool])
        order = sorted(range(N), key=lambda k: -scores[k])
        rank = {k: r + 1 for r, k in enumerate(order)}
        best = lambda ks: min((rank[k] for k in ks), default=None)
        rec["top5"] = [f"{pool[k]['doc'][:18]}#{pool[k]['pos']}" for k in order[:5]]
        if t in ("ref1", "ent1", "ent2", "control", "confusable"):
            ans = find(q["answer_doc"], q["answer_quote"])
            if not ans:
                errs.append("答えの引用が文書に無い")
            ans_k = [k for k in ans if all(x in pool[k]["n"] for x in keys)]
            if ans and not ans_k:
                errs.append("答えの語が、答えの引用のあるチャンクにそろっていない")
            rec["answer_rank"] = best(ans_k or ans)
            leak = [f"{pool[k]['doc'][:18]}#{pool[k]['pos']}" for k in order[:6] if k not in ans and keys and all(x in pool[k]["n"] for x in keys)]
            rec["leak_top6"] = leak
        if t in ("ref1", "ent1", "ent2"):
            ent = find(q["entry_doc"], q["entry_quote"])
            if not ent:
                errs.append("起点の引用が文書に無い")
            rec["entry_rank"] = best(ent)
            if set(ent) & set(ans):
                errs.append("起点と答えが同じチャンクにある")
            if any(all(x in pool[k]["n"] for x in keys) for k in ent):
                errs.append("答えの語が起点のチャンクにもある")
            if q["bridge_kind"] == "reference":
                if not any(norm(q["bridge_quote"]) in pool[k]["n"] for k in ent):
                    errs.append("参照の一文が起点のチャンクに無い")
            else:
                names = [norm(x) for x in [q["bridge_entity"]] + q.get("bridge_aliases", [])]
                if not any(any(n in pool[k]["n"] for n in names) for k in ent):
                    errs.append("つなぎのエンティティの名前が起点のチャンクに無い")
                last = ans
                if t == "ent2":
                    mids = [m for step in q.get("path", []) for m in find(step["doc"], step["quote"])]
                    if not mids:
                        errs.append("中継の引用が文書に無い")
                    rec["mid_rank"] = best(mids)
                    last = mids
                if not any(any(n in pool[k]["n"] for n in names) for k in last):
                    errs.append("つなぎのエンティティの名前（別名を含む）が" + ("中継" if t == "ent2" else "答え") + "のチャンクに無い")
            rec["cond_a_entry_top3"] = rec["entry_rank"] is not None and rec["entry_rank"] <= 3
            rec["cond_b_answer_below"] = rec["answer_rank"] is not None and rec["answer_rank"] > below
            rec["cond_b_relaxed_below20"] = rec["answer_rank"] is not None and rec["answer_rank"] > 20
            rec["no_leak"] = not rec["leak_top6"]
            rec["route_required"] = not errs and rec["cond_a_entry_top3"] and rec["cond_b_answer_below"] and rec["no_leak"]
            rec["route_required_relaxed"] = not errs and rec["cond_a_entry_top3"] and rec["cond_b_relaxed_below20"] and rec["no_leak"]
        if t == "control":
            rec["direct_ok"] = not errs and rec["answer_rank"] is not None and rec["answer_rank"] <= 3
        if t == "confusable":
            trap = find(q["trap_doc"], q["trap_quote"])
            if not trap:
                errs.append("わなの引用が文書に無い")
            rec["trap_rank"] = best(trap)
            rec["answer_above_trap"] = rec["answer_rank"] is not None and (rec["trap_rank"] is None or rec["answer_rank"] < rec["trap_rank"])
        if t == "agg":
            ev = []
            for e in q["evidence"]:
                ks = find(e["doc"], e["quote"])
                if not ks:
                    errs.append(f"証拠の引用が文書に無い: {e['doc']}")
                ev.append({"doc": e["doc"], "rank": best(ks)})
            rec["evidence"] = ev
            docs = len(ev)
            rec["docs"] = docs
            rec["docs_in_top5"] = sum(1 for e in ev if e["rank"] and e["rank"] <= 5)
            rec["docs_in_top10"] = sum(1 for e in ev if e["rank"] and e["rank"] <= 10)
            rec["route_required"] = not errs and rec["docs_in_top10"] < docs
        rec["errors"] = errs
        results.append(rec)
        print(f"{q['qid']:7s} {t:10s} " + (f"起点 {rec.get('entry_rank')} 位 " if 'entry_rank' in rec else "") +
              (f"答え {rec.get('answer_rank')} 位 " if 'answer_rank' in rec else "") +
              (f"わな {rec.get('trap_rank')} 位 " if 'trap_rank' in rec else "") +
              (f"証拠 {[e['rank'] for e in rec['evidence']]} 上位10に {rec['docs_in_top10']}/{rec['docs']} " if t == 'agg' else "") +
              (f"経路が要る={rec.get('route_required')} (緩め={rec.get('route_required_relaxed')}) " if t in ('ref1', 'ent1', 'ent2', 'agg') else "") +
              (f"直接で届く={rec.get('direct_ok')} " if t == 'control' else "") +
              (f"漏れ {rec['leak_top6']} " if rec.get('leak_top6') else "") + ("| " + "; ".join(errs) if errs else ""), flush=True)
    json.dump({"n_chunks": N, "answer_below": below, "results": results}, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
