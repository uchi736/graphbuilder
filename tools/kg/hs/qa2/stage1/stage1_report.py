# 段 1 の集計: (1) 予備実行の答えの揺れ、(2) 条件を伏せた判定し直しと前回の判定の一致（κ 係数）。
#   python3 stage1_report.py
import collections, json, math, pathlib, re, unicodedata

H = pathlib.Path(__file__).resolve().parent


def norm(t):
    t = unicodedata.normalize("NFKC", t or "")
    return re.sub(r"\s+", "", t)


def kappa(a, b, cats):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pa = collections.Counter(a); pb = collections.Counter(b)
    pe = sum((pa[c] / n) * (pb[c] / n) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0, po


out = {}
# (1) 揺れ
pre = json.load(open(H / "out/pre1/results.json", encoding="utf-8"))
byq = collections.defaultdict(list)
for r in pre:
    byq[r["qid"]].append(r)
same_text = keys_stable = 0
detail = []
for q, rs in byq.items():
    texts = {norm(r["answer"]) for r in rs}
    keysets = {tuple(sorted(set(r["score"]["keys_missing"]))) for r in rs}
    absts = {r["score"]["abstain"] for r in rs}
    same_text += int(len(texts) == 1)
    stable = len(keysets) == 1 and len(absts) == 1
    keys_stable += int(stable)
    detail.append({"qid": q, "n_runs": len(rs), "distinct_answers": len(texts), "key_hits": [r["score"]["keys_hit"] for r in rs], "abstain": [int(r["score"]["abstain"]) for r in rs], "stable": stable})
nq = len(byq)
out["variability"] = {"questions": nq, "runs_per_question": len(pre) // max(1, nq), "all_5_identical_text": same_text,
                      "key_hits_and_abstain_stable": keys_stable, "unstable_share": round(1 - keys_stable / nq, 3), "detail": detail}
print(f"[揺れ] {nq} 問 × {len(pre)//max(1,nq)} 回: 5 回とも同じ文字列 {same_text} 問、鍵の当たりと棄権が 5 回とも同じ {keys_stable} 問、揺れる問いの割合 {1 - keys_stable/nq:.1%}")

# (2) 判定の一致
bj = H / "blind1/judged_blind.json"
if bj.exists():
    key = {k["id"]: k for k in json.load(open(H / "blind1/key/key.json", encoding="utf-8"))}
    prev = {(j["qid"], j["cond"], j.get("run", 0)): j for j in json.load(open(H / "out/run1/judged_B_llm.json", encoding="utf-8"))}
    new = json.load(open(bj, encoding="utf-8"))
    a, b, rows = [], [], []
    for j in new:
        k = key[j["id"]]; p = prev.get((k["qid"], k["cond"], k["run"]))
        if not p:
            continue
        a.append(p["verdict"]); b.append(j["verdict"])
        rows.append({"id": j["id"], "qid": k["qid"], "cond": k["cond"], "prev": p["verdict"], "blind": j["verdict"]})
    cats = sorted(set(a) | set(b))
    k5, po5 = kappa(a, b, cats)
    a2 = ["correct" if x == "correct" else "not" for x in a]; b2 = ["correct" if x == "correct" else "not" for x in b]
    k2, po2 = kappa(a2, b2, ["correct", "not"])
    conf = collections.Counter(zip(a, b))
    out["agreement"] = {"n": len(a), "kappa_5cat": round(k5, 3), "agree_5cat": round(po5, 3), "kappa_correct_vs_not": round(k2, 3), "agree_correct_vs_not": round(po2, 3),
                        "confusion": {f"{x}->{y}": n for (x, y), n in conf.most_common()}, "disagreements": [r for r in rows if r["prev"] != r["blind"]]}
    print(f"[一致] {len(a)} 件: 5 区分の κ {k5:.3f}（一致率 {po5:.1%}）、正答かどうかの κ {k2:.3f}（一致率 {po2:.1%}）")
    for (x, y), n in conf.most_common():
        if x != y:
            print(f"   前回 {x} → 盲検 {y}: {n}")
json.dump(out, open(H / "out/stage1_report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
