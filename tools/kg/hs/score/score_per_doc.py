# 正解グラフを文書ごとに分けて recount_hs.py で採点し、合計する（X31）。
# 文書ごとに分けるのは、別の文書に同じ名前のノード（外観検査 など）があるときに、他の文書のノードで当たらないようにするため。
# 使い方:
#   python3 score/score_per_doc.py --gold gold_add/gold_add14.json --gold gold_hs_bydoc.json \
#     --run "名前=書き出し" ... --out score_out/x31/per_doc.json
# 正解の各リレーションには doc（ファイル名）、各エンティティには docs（ファイル名のリスト）が要る。
import argparse, json, os, subprocess, sys, tempfile, collections
HERE = os.path.dirname(os.path.abspath(__file__))

ap = argparse.ArgumentParser()
ap.add_argument("--gold", action="append", required=True)
ap.add_argument("--run", action="append", required=True)
ap.add_argument("--out", required=True)
A = ap.parse_args()

golds = []
for p in A.gold:
    g = json.load(open(p, encoding="utf-8"))
    golds.append(g)
docs = sorted({r["doc"] for g in golds for r in g["relations"]})
runs = [s.split("=", 1) for s in A.run]
res = {"runs": [l for l, _ in runs], "docs": {}}
tmp = tempfile.mkdtemp(prefix="perdoc_")
for doc in docs:
    ents, rels, der = {}, [], []
    for g in golds:
        for e in g["entities"]:
            if doc in e["docs"]: ents[e["id"]] = e
        rels += [r for r in g["relations"] if r["doc"] == doc]
    used = {r["s"] for r in rels} | {r["o"] for r in rels}
    for g in golds:
        for e in g["entities"]:
            if e["id"] in used and e["id"] not in ents: ents[e["id"]] = e
    gp = os.path.join(tmp, "gold.json"); fp = os.path.join(tmp, "files.txt")
    json.dump({"entities": list(ents.values()), "relations": rels, "doc_references": [], "derived_relations": []},
              open(gp, "w", encoding="utf-8"), ensure_ascii=False)
    open(fp, "w", encoding="utf-8").write(doc)
    op = os.path.join(tmp, "out.json")
    cmd = [sys.executable, os.path.join(HERE, "recount_hs.py"), "--gold", gp, "--out", op]
    for l, p in runs: cmd += ["--run", f"{l}={p}@{fp}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("失敗", doc, r.stderr[-800:]); continue
    o = json.load(open(op, encoding="utf-8"))
    res["docs"][doc] = {}
    for l, _ in runs:
        E, R = o[l]["entities"], o[l]["relations"]
        res["docs"][doc][l] = {
            "ent_gold": E["gold"], "ent_main": E["plus_sentence"], "ent_strict": E["strict"],
            "rel_gold": R["gold"], "rel_hit": R["final"], "rel_direct": R["direct"], "rel_typed": R["typed_direct"],
            "val_gold": R["value_obj"], "val_hit": R["value_hit"],
            "rows": [{"gold": x["gold"], "hit": x["hit"], "direct": x["direct"], "typed": x["typed_direct"], "is_value": x["is_value"],
                      "example": x["example"]} for x in R["rows"]],
            "ent_rows": [{"gold": x["gold"], "best": x["best"], "type": x["type"]} for x in E["rows"]],
            "chunks": o[l]["structure"]["chunks"]}
json.dump(res, open(A.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# 表
K = ["ent_main", "rel_typed", "rel_hit", "val_hit"]
def tot(l, ds=None):
    t = collections.Counter()
    for d, v in res["docs"].items():
        if ds and d not in ds: continue
        for k in ["ent_gold", "ent_main", "rel_gold", "rel_hit", "rel_direct", "rel_typed", "val_gold", "val_hit"]: t[k] += v[l][k]
    return t
print("| 文書 | 正解 エンティティ/リレーション | " + " | ".join(l for l, _ in runs) + " |")
print("|---|---|" + "---|" * len(runs))
for d, v in res["docs"].items():
    f = v[runs[0][0]]
    print(f"| {d} | {f['ent_gold']}/{f['rel_gold']} | " + " | ".join(f"{v[l]['rel_typed']}（{v[l]['rel_hit']}）" for l, _ in runs) + " |")
print()
print("| 合計 | " + " | ".join(l for l, _ in runs) + " |")
print("|---|" + "---|" * len(runs))
T = {l: tot(l) for l, _ in runs}
t0 = T[runs[0][0]]
print(f"| エンティティ本線（{t0['ent_gold']} 個のうち） | " + " | ".join(str(T[l]["ent_main"]) for l, _ in runs) + " |")
print(f"| リレーション 何らかの形で在る（{t0['rel_gold']} 本のうち） | " + " | ".join(str(T[l]["rel_hit"]) for l, _ in runs) + " |")
print(f"| 　直接 | " + " | ".join(str(T[l]["rel_direct"]) for l, _ in runs) + " |")
print(f"| 　直接で種類も合う | " + " | ".join(str(T[l]["rel_typed"]) for l, _ in runs) + " |")
print(f"| 　値の線（{t0['val_gold']} 本のうち） | " + " | ".join(str(T[l]["val_hit"]) for l, _ in runs) + " |")
