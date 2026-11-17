# X36: 落ちた正解のリレーションについて、元の行が抽出に使われたか（行ごと読み飛ばされたか）、使われたが別の形になったかを分ける。
import json, re, sys, os, collections, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import norm2, load_dump
N = lambda s: re.sub(r"[\s・･.…|:：;；,，、。()（）\[\]「」『』\-ー—~〜→]+", "", norm2(s or ""))
D, E, CH, EC, CE = load_dump("runs/prod_full56_1113/graph_dump.json")
rows = json.load(open("score_out/x31/x36_rows.json", encoding="utf-8"))
golds = {}
for gp in ["gold_add/gold_add46.json", "gold_add/gold_hs4_full.json"]:
    g = json.load(open(gp, encoding="utf-8"))
    for e in g["entities"]: golds.setdefault(e["id"], set()).update([e["id"]] + e.get("aliases", []))
rel_by_chunk = collections.defaultdict(list)
for r in D["relationships"]:
    c = (r.get("props") or {}).get("source_chunk_id")
    cs = [c] if c else list(EC.get(r["source"], set()) & EC.get(r["target"], set()))
    for c in cs: rel_by_chunk[c].append(r)
MAIN = {"strict", "modified", "sentence", "compound"}
cache = {}
def chunk_info(cid):
    if cid in cache: return cache[cid]
    raw = CH[cid].get("text") or ""; lines = raw.split("\n"); nl = [N(l) for l in lines]
    names = {e: N(E[e]["props"].get("id")) for e in CE.get(cid, set())}
    names = {e: n for e, n in names.items() if len(n) >= 2}
    in_line = [set(e for e, n in names.items() if n in l) for l in nl]
    cnt = collections.Counter(e for s in in_line for e in s)
    touch = [[] for _ in lines]
    for r in rel_by_chunk.get(cid, []):
        s, o = r["source"], r["target"]
        for i, es in enumerate(in_line):
            if (s in es and o in es) or (s in es and cnt[s] == 1) or (o in es and cnt[o] == 1): touch[i].append(r)
    cache[cid] = (lines, nl, in_line, touch, names); return cache[cid]
def line_kind(l):
    s = l.strip()
    if s.startswith("[") and "]" in s[:120] and ">" in s[:120]: return "見出しの経路の行"
    if s.startswith("#"): return "見出し"
    if s.count("|") >= 2: return "表の行"
    if re.match(r"^(注|※|\(注|（注|但し|ただし|なお)", s): return "注記・但し書き"
    if re.match(r"^([0-9０-９]+[\).）]|[\(（][0-9０-９a-zａ-ｚア-ン]+[\)）]|[①-⑳]|[a-zａ-ｚ][\)）]|[・\-－])", s): return "箇条書き"
    return "地の文"
cat = collections.Counter(); ex = collections.defaultdict(list); kind = collections.defaultdict(lambda: [0, 0]); per_line = {}
for r in rows:
    if not r["chunk"]: cat["（チャンクに結べない）"] += 1; continue
    lines, nl, in_line, touch, names = chunk_info(r["chunk"])
    okeys = [N(k) for k in golds.get(r["o"], [r["o"]]) if len(N(k)) >= 2]
    ol = [i for i, l in enumerate(nl) if any(k in l for k in okeys)]
    if not ol:
        cat["抽出できた" if r["hit"] else "（目的語の行が見つからない）"] += 1; continue
    i = ol[0]; touched = bool(touch[i]); k = line_kind(lines[i])
    per_line.setdefault((r["chunk"], i), [k, touched, 0, 0]); per_line[(r["chunk"], i)][2] += 1; per_line[(r["chunk"], i)][3] += r["hit"]
    if r["hit"]: c = "抽出できた"
    elif not touched: c = "行が抽出に使われていない"
    else:
        s_ok = r["s_ent"] in MAIN; o_ok = r["o_ent"] in MAIN or r["o_ent"] == "props"
        c = ("行は使われた: 両端のエンティティはあるがリレーションが無い" if s_ok and o_ok else
             "行は使われた: 目的語が別の切り方（正解の目的語のエンティティが無い）" if s_ok else
             "行は使われた: 主語のエンティティが無い" if o_ok else "行は使われた: 両端とも別の切り方")
    cat[c] += 1
    if c != "抽出できた" and len(ex[c]) < 400:
        ex[c].append({"doc": r["doc"], "gold": f'{r["s"]} —{r["p"]}→ {r["o"]}', "line": lines[i].strip()[:160],
                      "extracted": [f'{E[x["source"]]["props"].get("id")} —{x["type"]}→ {E[x["target"]]["props"].get("id")}' for x in touch[i][:6]]})
tot = sum(cat.values())
print("正解のリレーション", tot)
for k, v in cat.most_common(): print(f"  {v:5d} {v/tot:5.1%}  {k}")
print("\n## 正解のある行ごと: その行が抽出に使われたか（行の種類別）")
lk = collections.defaultdict(lambda: [0, 0, 0, 0])
for (cid, i), (k, t, n, h) in per_line.items():
    lk[k][0] += 1; lk[k][1] += t; lk[k][2] += n; lk[k][3] += h
for k, (n, t, g, h) in sorted(lk.items(), key=lambda x: -x[1][0]): print(f"  {k}: 行 {n}・使われた {t}（{t/n:.0%}）・その行の正解 {g} 本中 {h} 本抽出（{h/g:.0%}）")
random.seed(1)
json.dump({k: random.sample(v, min(40, len(v))) for k, v in ex.items()}, open("score_out/x31/x36_examples.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for k, v in ex.items():
    print("\n==", k)
    for x in random.sample(v, min(5, len(v))): print("  ", x["doc"][:11], "|", x["gold"], "| 行:", x["line"][:70], "| 抽出:", x["extracted"][:3])
