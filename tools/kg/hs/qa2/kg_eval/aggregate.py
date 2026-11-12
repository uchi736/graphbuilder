# 評価結果の集計。観点 × 条件で、到達・正答（鍵）・文書の再現率・誤混入・罠・棄権・文脈量を出し、G1 − N と N − B の差を並べる。
#   python3 aggregate.py out/run1/results.json [--judged out/run1/judged.json]
# judged.json は含意判定の結果（qid, cond, run → verdict: correct / partial / wrong / abstain, polarity_ok, notes）。あれば正答に使う。
import argparse, collections, json, pathlib

ASPECTS = ["集約の完全性", "逆引きと波及", "矛盾と不整合の検出", "取り違えの少なさ", "照合"]
CONDS = ["llm_only", "B", "N", "G1", "G0"]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def fmt(x, pct=True):
    if x is None:
        return "  -  "
    return f"{100 * x:5.0f}%" if pct else f"{x:6.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+"); ap.add_argument("--judged", default=None); ap.add_argument("--out", default=None)
    ap.add_argument("--conds", default=None)
    a = ap.parse_args()
    R = []
    for f in a.results:
        R += json.load(open(f, encoding="utf-8"))
    global CONDS
    if a.conds:
        CONDS = a.conds.split(",")
    else:
        CONDS = list(dict.fromkeys([c for c in CONDS if any(r["cond"] == c for r in R)] + [r["cond"] for r in R]))
    J = {}
    if a.judged and pathlib.Path(a.judged).exists():
        for j in json.load(open(a.judged, encoding="utf-8")):
            J[(j["qid"], j["cond"], j.get("run", 0))] = j
    by = collections.defaultdict(list)
    for r in R:
        by[(r["aspect"], r["cond"])].append(r)
    rows = []
    print(f"{'観点':12s} {'条件':8s} {'n':>3s} {'到達(全群)':>9s} {'鍵再現':>7s} {'全鍵':>6s} {'文書再現':>8s} {'誤混入':>6s} {'罠':>6s} {'棄権':>6s} {'判定正答':>8s} {'文脈字数':>8s}")
    for asp in ASPECTS:
        for cond in CONDS:
            rs = by.get((asp, cond))
            if not rs:
                continue
            reach = [int(r["score"]["gold_chunks_in_context"] == r["score"]["gold_chunks_total"]) if r["score"]["gold_chunks_in_context"] is not None else None for r in rs]
            keyr = [r["score"]["key_recall"] for r in rs]
            full = [int(r["score"]["key_recall"] == 1.0) for r in rs]
            docr = [r["score"]["doc_recall"] for r in rs]
            extra = [len(r["score"]["extra_docs"]) for r in rs]
            trap = [int(bool(r["score"]["trap_hit"])) for r in rs]
            abst = [int(r["score"]["abstain"]) for r in rs]
            ctx = [r["context_chars"] for r in rs]
            judged = [J.get((r["qid"], r["cond"], r["run"])) for r in rs]
            jc = [int(j["verdict"] == "correct") for j in judged if j] or None
            row = {"aspect": asp, "cond": cond, "n": len(rs), "reach_all": mean(reach), "key_recall": mean(keyr), "all_keys": mean(full), "doc_recall": mean(docr),
                   "extra_docs": mean(extra), "trap_rate": mean(trap), "abstain_rate": mean(abst), "judged_correct": mean(jc) if jc else None, "ctx_chars": mean(ctx)}
            rows.append(row)
            print(f"{asp:12s} {cond:8s} {len(rs):3d} {fmt(row['reach_all']):>9s} {fmt(row['key_recall']):>7s} {fmt(row['all_keys']):>6s} {fmt(row['doc_recall']):>8s} {fmt(row['extra_docs'], False):>6s} {fmt(row['trap_rate']):>6s} {fmt(row['abstain_rate']):>6s} {fmt(row['judged_correct']):>8s} {int(row['ctx_chars'] or 0):8d}")
        print()
    # 差分（問いごとの対応づけ）
    print("== 条件間の差（同じ問いの対で、鍵の再現率と到達の差を平均）")
    idx = {(r["qid"], r["cond"], r["run"]): r for r in R}
    for asp in ASPECTS:
        pairs = [(x, y) for x in CONDS for y in CONDS if x != y and ((x.startswith("N") and y.startswith("B")) or (x.startswith("G1") and y.startswith("N")) or (x.startswith("G1") and y.startswith("B"))) and x[1:] == y[1:] or (x.startswith("G1") and y.startswith("N") and x[2:] == y[1:]) or (x.startswith("G1") and y.startswith("B") and x[2:] == y[1:])]
        for (x, y) in dict.fromkeys(pairs):
            d_key, d_reach, n = [], [], 0
            for r in R:
                if r["aspect"] != asp or r["cond"] != x:
                    continue
                o = idx.get((r["qid"], y, r["run"]))
                if not o:
                    continue
                n += 1
                d_key.append((r["score"]["key_recall"] or 0) - (o["score"]["key_recall"] or 0))
                if r["score"]["gold_chunks_in_context"] is not None and o["score"]["gold_chunks_in_context"] is not None:
                    d_reach.append(r["score"]["gold_chunks_in_context"] - o["score"]["gold_chunks_in_context"])
            if n:
                print(f"{asp:12s} {x} - {y}: n={n:2d} 鍵再現の差 {mean(d_key):+.2f}  届いた答えのチャンク数の差 {mean(d_reach) if d_reach else 0:+.2f}")
    # 問いごとの一覧（鍵の再現率）
    print("\n== 問いごとの鍵の再現率（llm_only / B / N / G1 / G0）")
    qids = list(dict.fromkeys(r["qid"] for r in R))
    for q in qids:
        cells = []
        for cond in CONDS:
            r = idx.get((q, cond, 0))
            cells.append(f"{(r['score']['key_recall'] if r and r['score']['key_recall'] is not None else float('nan')):.2f}" if r else "  -  ")
        asp = next(r["aspect"] for r in R if r["qid"] == q)
        print(f"{q:20s} {asp:10s} " + " ".join(f"{c:>5s}" for c in cells))
    if a.out:
        json.dump(rows, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
