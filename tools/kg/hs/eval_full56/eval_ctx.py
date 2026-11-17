#!/usr/bin/env python3
"""本番グラフ（HS 基準 56 本）で、QA 設計セット（qa2/qa_main_v1.json の主問 85 と双子 71）の文脈を集めて採点する。

使い方:
  python3 eval_ctx.py collect <出力.jsonl>      主問と双子 × 方式で POST /retrieve_context を呼び、文脈を丸ごと保存
  python3 eval_ctx.py score <出力.jsonl> <chunks.json> <結果.json>   採点（正解の引用が文脈に入ったか・答えの鍵が入ったか）

/retrieve_context は検索だけ（LLM を呼ばず履歴も書かない）。採点は文字の照合だけで、LLM は使わない。
引用の照合: NFKC・空白と表の罫線（|）と見出し記号を除き、長音とダッシュ類を同じ字にそろえて部分一致（厳密）。
  一致しないときは、引用を句読点・中点・括弧・表の罫線・空白・ダッシュで切った 3 字以上の断片の字数の 8 割以上が入っていれば一致（緩い）。
chunks.json（全チャンクの本文）で、引用がグラフのどこかにあるか（照合できるか）も数え、検索の外しと表記の差を分ける。
"""
import json, os, re, subprocess, sys, time, unicodedata
from collections import defaultdict

B = "http://localhost:8440"
QA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "qa2", "qa_main_v1.json")
MODES = ["graph_vector_fulltext", "graph_vector_fulltext_ref"]

DASH = re.compile(r"[\-‐‑‒–—―ー－﹣➖−─━ｰ~〜～]")
DROP = re.compile(r"[\s|#*`>_]")
CUT = re.compile(r"[、。，,・･…:：;；()（）「」『』\[\]【】/／|\s\-]+")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = DASH.sub("-", s)
    return DROP.sub("", s)


def frags(q):
    """表のセル・行の継ぎ目（|・空白・ダッシュ）や句読点で切った 3 字以上の断片（norm 済み）。"""
    q = DASH.sub("-", unicodedata.normalize("NFKC", q or ""))
    return [f for f in (norm(x) for x in CUT.split(q)) if len(f) >= 3]


def match(quote, text_n):
    """厳密一致なら 2、緩い一致なら 1、無ければ 0。text_n は norm 済み。"""
    qn = norm(quote)
    if qn and qn in text_n:
        return 2
    fs = frags(quote)
    if not fs:
        return 0
    tot = sum(len(f) for f in fs)
    hit = sum(len(f) for f in fs if f in text_n)
    return 1 if hit >= 0.8 * tot else 0


def items():
    qs = json.load(open(QA, encoding="utf-8"))
    out = []
    for q in qs:
        out.append({"qid": q["qid"], "kind": "main", "category": q["category"], "question": q["question"],
                    "gold_groups": q.get("gold_groups") or [], "answer_keys": q.get("answer_keys") or []})
        if q.get("twin_question"):
            out.append({"qid": q["qid"] + "~twin", "kind": "twin", "category": q["category"],
                        "question": q["twin_question"], "gold_groups": q.get("gold_groups") or [],
                        "answer_keys": q.get("answer_keys") or []})
    return out


def call(question, mode):
    args = ["curl", "-s", "--max-time", "300", "-X", "POST", f"{B}/retrieve_context"]
    for k, v in (("uri", ""), ("userName", ""), ("password", ""), ("database", ""), ("email", "eval@local"),
                 ("question", question), ("mode", mode), ("document_names", "[]"), ("retrieval_settings", "{}"),
                 ("embedding_provider", "vllm"), ("embedding_model", "cl-nagoya/ruri-v3-310m"), ("model", "gemma4")):
        args += ["--form-string", f"{k}={v}"]
    p = subprocess.run(args, capture_output=True, text=True)
    try:
        r = json.loads(p.stdout)
    except Exception:
        return {"status": "unreadable", "raw": p.stdout[-300:]}
    d = r.get("data") or {}
    return {"status": r.get("status"), "error": r.get("error"), "context": d.get("context") or "",
            "chunks": [{k: c.get(k) for k in ("id", "fileName", "section", "score", "via_reference")}
                       for c in d.get("chunks") or [] if isinstance(c, dict)],
            "references": len(d.get("references") or [])}


def collect(out):
    its = items()
    t = time.time()
    with open(out, "w", encoding="utf-8") as f:
        for i, it in enumerate(its):
            for m in MODES:
                r = call(it["question"], m)
                f.write(json.dumps(dict(it, mode=m, **r), ensure_ascii=False) + "\n")
            if i % 20 == 0:
                print(f"{i}/{len(its)} {time.time()-t:.0f}s", flush=True)
    print(f"done {len(its)} 問 × {len(MODES)} 方式 {time.time()-t:.0f}s")


def score(src, chunks_path, out):
    chunks = json.load(open(chunks_path, encoding="utf-8"))
    corpus = [(c["id"], c["file"], norm(c["text"])) for c in chunks]
    rows = [json.loads(l) for l in open(src, encoding="utf-8")]
    where_cache = {}

    def where(quote):
        if quote not in where_cache:
            best = 0; ids = []
            for cid, fn, tn in corpus:
                m = match(quote, tn)
                if m > best:
                    best, ids = m, [(cid, fn)]
                elif m and m == best:
                    ids.append((cid, fn))
            where_cache[quote] = (best, ids)
        return where_cache[quote]

    per = []
    for r in rows:
        ctx = norm(r["context"])
        got_ids = {c["id"] for c in r["chunks"]}
        groups = []
        for g in r["gold_groups"]:
            qs = g.get("quotes") or []
            in_ctx = max((match(q, ctx) for q in qs), default=0)
            in_corpus = max((where(q)[0] for q in qs), default=0)
            gold_ids = {cid for q in qs for cid, _ in where(q)[1]}
            groups.append({"name": g.get("name"), "in_ctx": in_ctx, "in_corpus": in_corpus,
                           "chunk_hit": bool(gold_ids & got_ids), "gold_files": sorted({fn for q in qs for _, fn in where(q)[1]})})
        keys = r["answer_keys"]
        kh = [k for k in keys if norm(k) and norm(k) in ctx]
        per.append({"qid": r["qid"], "kind": r["kind"], "category": r["category"], "mode": r["mode"],
                    "status": r["status"], "groups": groups,
                    "gold_all": bool(groups) and all(g["in_ctx"] for g in groups),
                    "gold_all_matchable": bool(groups) and all(g["in_corpus"] for g in groups),
                    "key_recall": (len(kh) / len(keys)) if keys else None, "keys_missing": [k for k in keys if k not in kh],
                    "context_len": len(r["context"]), "n_chunks": len(r["chunks"]), "references": r.get("references")})

    agg = defaultdict(lambda: defaultdict(lambda: {"n": 0, "gold": 0, "gold_n": 0, "unmatchable": 0, "key": 0.0, "key_n": 0, "err": 0}))
    for p in per:
        for cat in (p["category"], "全体"):
            a = agg[(p["mode"], p["kind"])][cat]
            a["n"] += 1
            a["err"] += p["status"] != "Success"
            if p["groups"]:
                if not p["gold_all_matchable"]:
                    a["unmatchable"] += 1
                else:
                    a["gold_n"] += 1; a["gold"] += p["gold_all"]
            if p["key_recall"] is not None:
                a["key_n"] += 1; a["key"] += p["key_recall"]
    summary = {f"{m}|{k}": {c: dict(v, gold_rate=round(v["gold"] / v["gold_n"], 3) if v["gold_n"] else None,
                                    key_rate=round(v["key"] / v["key_n"], 3) if v["key_n"] else None)
                            for c, v in sorted(cats.items())} for (m, k), cats in agg.items()}
    json.dump({"summary": summary, "per": per}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for key, cats in summary.items():
        print("==", key)
        for c, v in cats.items():
            print(f"  {c:12s} n={v['n']:3d} 正解の引用が全部入った {v['gold']}/{v['gold_n']} ({v['gold_rate']}) "
                  f"照合不能 {v['unmatchable']} 鍵 {v['key_rate']} 失敗 {v['err']}")


if __name__ == "__main__":
    if sys.argv[1] == "collect":
        collect(sys.argv[2])
    else:
        score(sys.argv[2], sys.argv[3], sys.argv[4])
