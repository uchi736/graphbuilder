# X33: QA の根拠の文（gold_groups の quotes）に対応する正解のリレーションが、本番のグラフに在るかを数える。
import json, re, sys, os, glob, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import norm2
N = lambda s: re.sub(r"[\s・･.…|:：;；,，、。()（）\[\]「」『』\-ー—~〜]+", "", norm2(s or ""))
src = {}
for p in glob.glob("gold_add/src_raw/*.txt"):
    b = os.path.basename(p)[:-4]
    src[b + ".pdf"] = N(open(p, encoding="utf-8").read()) + N(open("gold_add/src/" + b + ".txt", encoding="utf-8").read())
gold = {}; score = {}
for gp, sp in [("gold_add/gold_add46.json", "score_out/x31/add46_perdoc.json"), ("gold_add/gold_hs4_full.json", "score_out/x31/hs4_full_perdoc.json")]:
    g = json.load(open(gp, encoding="utf-8")); r = json.load(open(sp, encoding="utf-8"))
    for x in g["relations"]: gold.setdefault(x["doc"], []).append(x)
    for d, v in r["docs"].items():
        for row in v["11/14本番"]["rows"]: score[(d, row["gold"])] = row
ent = {}
for gp in ["gold_add/gold_add46.json", "gold_add/gold_hs4_full.json"]:
    for e in json.load(open(gp, encoding="utf-8"))["entities"]: ent.setdefault(e["id"], set()).update([e["id"]] + e.get("aliases", []))
def lcs_len(a, b, need):
    # 共通の部分文字列が need 字以上あるか
    if len(a) < need or len(b) < need: return False
    grams = {a[i:i + need] for i in range(len(a) - need + 1)}
    return any(b[i:i + need] in grams for i in range(len(b) - need + 1))
def keys_in(x, t):
    return any(len(N(k)) >= 2 and N(k) in t for k in ent.get(x, [x]))
qs = json.load(open("qa2/qa_main_v1.json", encoding="utf-8"))
out = []; cat = collections.defaultdict(lambda: collections.Counter())
for q in qs:
    need = {}; docs = set(); nodoc = 0; nogold = 0
    for g in q["gold_groups"]:
        for qu in g["quotes"]:
            t = N(qu)
            if len(t) < 6: continue
            ds = [d for d, s in src.items() if t in s] or [d for d, s in src.items() if len(t) >= 16 and (t[:16] in s or t[-16:] in s)]
            if not ds: nodoc += 1; continue
            for d in ds:
                docs.add(d)
                if d not in gold: nogold += 1; continue
                for r in gold[d]:
                    ev = N(r.get("evidence"))
                    # 根拠の引用が QA の引用と 8 字以上重なる、または主語と目的語の名前がともに QA の引用に在る
                    if lcs_len(ev, t, 8) or (keys_in(r["s"], t) and keys_in(r["o"], t)):
                        need[(d, f'{r["s"]} -[{r["p"]}]-> {r["o"]}')] = r
    rows = [score[k] for k in need if k in score]
    n = len(rows); hit = sum(x["hit"] for x in rows); typed = sum(x["typed"] for x in rows); direct = sum(x["direct"] for x in rows)
    st = "正解なし(試運転系など)" if not n and nogold else "対応する正解なし" if not n else "全部在る" if hit == n else "一部在る" if hit else "1 本も無い"
    out.append(dict(qid=q["qid"], category=q["category"], docs=sorted(docs), n=n, hit=hit, direct=direct, typed=typed, status=st,
                    miss=[k[1] for k in need if k in score and not score[k]["hit"]][:8]))
    c = cat[q["category"]]; c["q"] += 1; c[st] += 1; c["n"] += n; c["hit"] += hit; c["typed"] += typed
json.dump(out, open("score_out/x31/x33_qa_subgraph.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
tot = collections.Counter()
print("| 型 | 問い | 全部在る | 一部在る | 1 本も無い | 対応する正解なし | 正解なし | 必要な線 | 在る | 厳格 |"); print("|---|---|---|---|---|---|---|---|---|---|")
for k, c in sorted(cat.items()):
    tot.update(c)
    print(f'| {k} | {c["q"]} | {c["全部在る"]} | {c["一部在る"]} | {c["1 本も無い"]} | {c["対応する正解なし"]} | {c["正解なし(試運転系など)"]} | {c["n"]} | {c["hit"]} | {c["typed"]} |')
c = tot
print(f'| 計 | {c["q"]} | {c["全部在る"]} | {c["一部在る"]} | {c["1 本も無い"]} | {c["対応する正解なし"]} | {c["正解なし(試運転系など)"]} | {c["n"]} | {c["hit"]} | {c["typed"]} |')
import statistics
ns = [o["n"] for o in out if o["n"]]; print("対応した問い", len(ns), "必要な線の中央値", statistics.median(ns), "最大", max(ns))
