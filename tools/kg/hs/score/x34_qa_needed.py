# X34: 問いごとに指定した「答えるために最小限必要な線」が、グラフに在るかを数える。
# 入力: gold_add/qa_sub/out/batch_*.json（RULES_QA.md の形）。出力: score_out/x34/（per_doc.json・per_question.json）と表。
# 使い方: python3 score/x34_qa_needed.py [--suffix .verified]
import json, glob, os, sys, subprocess, collections, re, argparse
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from common import load_dump
from matcher import Matcher
ap = argparse.ArgumentParser(); ap.add_argument("--dir", default="gold_add/qa_sub/out"); ap.add_argument("--tag", default="x34"); A = ap.parse_args()
ACC = json.load(open("gold_add/accept.json", encoding="utf-8"))
RUNS = [("11/14本番", os.environ.get("X34_PROD", "runs/prod_full56_1113/graph_dump.json")), ("11/9新変換EDC固定", "runs/full56_regions_text/graph_dump_linked.json"),
        ("11/9旧変換EDC固定", "runs/full56_night1109/graph_dump_linked.json"), ("10/17旧変換EDC品質", "runs/full56_edc_quality/graph_dump_linked.json")]
P = RUNS[0][0]
qs = []
for f in sorted(glob.glob(os.path.join(A.dir, "batch_*.json"))): qs += json.load(open(f, encoding="utf-8"))
ents = {}; rels = {}; bad = []
for q in qs:
    ids = {e["id"] for e in q["entities"]}
    for e in q["entities"]:
        x = ents.setdefault(e["id"], {"id": e["id"], "type": e.get("type", "Part"), "aliases": set(), "docs": set()})
        x["aliases"] |= set(e.get("aliases", [])); x["docs"] |= set(e.get("docs", []))
    for r in q["needed"]:
        if r["s"] not in ids or r["o"] not in ids or r["p"] not in ACC: bad.append((q["qid"], r)); continue
        ents[r["s"]]["docs"].add(r["doc"]); ents[r["o"]]["docs"].add(r["doc"])
        rels[(r["doc"], r["s"], r["p"], r["o"])] = r
gold = {"entities": [dict(e, aliases=sorted(e["aliases"]), docs=sorted(e["docs"])) for e in ents.values()],
        "relations": [dict(doc=k[0], s=k[1], p=k[2], o=k[3], accept=ACC[k[2]]) for k in rels], "doc_references": [], "derived_relations": []}
os.makedirs(f"score_out/{A.tag}", exist_ok=True)
json.dump(gold, open(f"score_out/{A.tag}/gold_qa_needed.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
cmd = [sys.executable, os.path.join(HERE, "score_per_doc.py"), "--gold", f"score_out/{A.tag}/gold_qa_needed.json", "--out", f"score_out/{A.tag}/per_doc.json"]
for l, p in RUNS: cmd += ["--run", f"{l}={p}"]
r = subprocess.run(cmd, capture_output=True, text=True)
if r.returncode: print(r.stderr[-2000:]); sys.exit(1)
res = json.load(open(f"score_out/{A.tag}/per_doc.json", encoding="utf-8"))
row = {l: {(d, x["gold"]): x for d, v in res["docs"].items() for x in v[l]["rows"]} for l, _ in RUNS}
# shared: 複数の文書にまたがるエンティティが、1 つのノードとして各文書のチャンクに付いているか
M = Matcher(gold["entities"])
def shared_ok(path):
    d, E, chunks, ec, ce = load_dump(path)
    cdoc = {c: v["fileName"] for c, v in chunks.items()}
    node_docs = {e: {cdoc[c] for c in cs} for e, cs in ec.items()}
    hits = collections.defaultdict(list)          # gold id -> [ノードの文書の集合]
    for e in d["entities"]:
        if e["id"] not in node_docs: continue
        al = e["props"].get("aliases"); al = al if isinstance(al, list) else []
        for nm_ in [e["props"].get("id")] + [a for a in al if isinstance(a, str)]:
            for gid, (tier, cov) in M.match_name(nm_).items():
                if tier in ("strict", "modified"): hits[gid].append(node_docs[e["id"]])
    return hits
SH = {l: shared_ok(p) for l, p in RUNS[:1]}
ret = {}
try:
    for x in json.load(open("eval_full56/score_1114.json", encoding="utf-8"))["per"]:
        if x["kind"] == "main" and x["mode"] == "graph_vector_fulltext": ret[x["qid"]] = x["gold_all"]
except Exception: pass
cat = {}
for q in json.load(open("qa2/qa_main_v1.json", encoding="utf-8")): cat[q["qid"]] = q["category"]
out = []
for q in qs:
    need = [r for r in q["needed"] if (r["doc"], r["s"], r["p"], r["o"]) in rels]
    o = {"qid": q["qid"], "category": cat.get(q["qid"], ""), "n": len(need), "retrieved": ret.get(q["qid"])}
    for l, _ in RUNS:
        xs = [row[l].get((r["doc"], f'{r["s"]} -[{r["p"]}]-> {r["o"]}')) for r in need]
        o[l] = {"hit": sum(1 for x in xs if x and x["hit"]), "direct": sum(1 for x in xs if x and x["direct"]), "typed": sum(1 for x in xs if x and x["typed"])}
    xs = [(r, row[P].get((r["doc"], f'{r["s"]} -[{r["p"]}]-> {r["o"]}'))) for r in need]
    o["route"] = [sum(1 for r, x in xs if r.get("role") == "route" and x and x["hit"]), sum(1 for r, x in xs if r.get("role") == "route")]
    o["answer"] = [sum(1 for r, x in xs if r.get("role") != "route" and x and x["hit"]), sum(1 for r, x in xs if r.get("role") != "route")]
    o["miss"] = [f'{r["doc"].replace(".pdf","")}: {r["s"]} —{r["p"]}→ {r["o"]}（{r.get("role")}）' for r, x in xs if not (x and x["hit"])]
    sh = []
    for s in q.get("shared", []):
        ok = any(set(s["docs"]) <= nd for nd in SH[P].get(s["id"], []))
        sh.append({"id": s["id"], "docs": s["docs"], "ok": ok})
    o["shared"] = sh
    h = o[P]["hit"]
    o["status"] = "指定なし" if not need else "全部在る" if h == len(need) else "一部在る" if h else "1 本も無い"
    out.append(o)
json.dump(out, open(f"score_out/{A.tag}/per_question.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("問い", len(out), "必要な線（重複を除く）", len(rels), "除外", len(bad))
tot = {l: collections.Counter() for l, _ in RUNS}
for o in out:
    for l, _ in RUNS:
        tot[l]["n"] += o["n"]; tot[l]["hit"] += o[l]["hit"]; tot[l]["direct"] += o[l]["direct"]; tot[l]["typed"] += o[l]["typed"]
        tot[l]["all"] += (o["n"] > 0 and o[l]["hit"] == o["n"]); tot[l]["q"] += o["n"] > 0
print("\n| 取り込み | 必要な線 | 在る（緩い） | 直接 | 厳格 | 全部そろう問い |\n|---|---|---|---|---|---|")
for l, _ in RUNS:
    t = tot[l]; print(f'| {l} | {t["n"]} | {t["hit"]}（{t["hit"]/t["n"]:.0%}） | {t["direct"]} | {t["typed"]}（{t["typed"]/t["n"]:.0%}） | {t["all"]}/{t["q"]} |')
c = collections.defaultdict(collections.Counter)
for o in out:
    k = c[o["category"]]; k["q"] += 1; k[o["status"]] += 1; k["n"] += o["n"]; k["hit"] += o[P]["hit"]
    k["rt"] += o["route"][1]; k["rh"] += o["route"][0]; k["at"] += o["answer"][1]; k["ah"] += o["answer"][0]
print("\n| 型 | 問い | 全部在る | 一部在る | 1 本も無い | 必要な線 | 在る | 道 | 答え |\n|---|---|---|---|---|---|---|---|---|")
T = collections.Counter()
for k, v in sorted(c.items()):
    T.update(v); print(f'| {k} | {v["q"]} | {v["全部在る"]} | {v["一部在る"]} | {v["1 本も無い"]} | {v["n"]} | {v["hit"]} | {v["rh"]}/{v["rt"]} | {v["ah"]}/{v["at"]} |')
v = T; print(f'| 計 | {v["q"]} | {v["全部在る"]} | {v["一部在る"]} | {v["1 本も無い"]} | {v["n"]} | {v["hit"]} | {v["rh"]}/{v["rt"]} | {v["ah"]}/{v["at"]} |')
r2 = collections.defaultdict(collections.Counter)
for o in out:
    if o["n"]: r2["検索で根拠が入った" if o["retrieved"] else "検索で根拠が欠けた"][o["status"]] += 1
print("\n検索（X28 既定）との突き合わせ:", {k: dict(v) for k, v in r2.items()})
sh = [s for o in out for s in o["shared"]]
print("文書をまたぐ同一ノード:", sum(s["ok"] for s in sh), "/", len(sh))
