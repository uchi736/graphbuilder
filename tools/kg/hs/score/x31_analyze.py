# X31 の分析: リレーションの取りこぼしの内訳（端点の物が無い／両端は在るが線が無い）と、回どうしの対の比較（McNemar）。
import json, sys, collections, math
res = json.load(open(sys.argv[1], encoding="utf-8"))
runs = res["runs"]; MAIN = {"strict", "modified", "sentence", "compound"}
def gold_rel(g):  # "s -[p]-> o"
    s, rest = g.split(" -[", 1); p, o = rest.split("]-> ", 1); return s, p, o
print("## 取りこぼしの内訳（リレーションが何の形でも無いもの）")
for l in runs:
    c = collections.Counter()
    for d, v in res["docs"].items():
        ent = {e["gold"]: e["best"] for e in v[l]["ent_rows"]}
        for r in v[l]["rows"]:
            if r["hit"]: continue
            s, p, o = gold_rel(r["gold"])
            es, eo = ent.get(s) in MAIN, ent.get(o) in MAIN or (r["is_value"] and ent.get(o) == "props")
            c["両端あり・線なし" if es and eo else "主語の物なし" if not es and eo else "目的語の物なし" if es else "両端とも物なし"] += 1
    print(l, dict(c), "計", sum(c.values()))
def mcnemar(a, b):
    n01 = sum(1 for x, y in zip(a, b) if not x and y); n10 = sum(1 for x, y in zip(a, b) if x and not y)
    n = n01 + n10
    if n == 0: return n01, n10, 1.0
    k = min(n01, n10); p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n * 2
    return n01, n10, min(1.0, p)
print("\n## 対の比較（直接で種類も合う、同じ正解のリレーションごと）")
key = lambda l: [r["typed"] for d in sorted(res["docs"]) for r in res["docs"][d][l]["rows"]]
for i in range(len(runs)):
    for j in range(i + 1, len(runs)):
        n01, n10, p = mcnemar(key(runs[i]), key(runs[j]))
        print(f"{runs[i]} → {runs[j]}: 良化 {n01}・悪化 {n10}・p={p:.3f}")
