# X36: 抽出できたリレーションとできなかったリレーションの違いを、原文の書かれ方とチャンクの条件で数える。
import json, re, sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import norm2, load_dump
N = lambda s: re.sub(r"[\s・･.…|:：;；,，、。()（）\[\]「」『』\-ー—~〜→]+", "", norm2(s or ""))
P = "11/14本番"
D, E, CH, EC, CE = load_dump("runs/prod_full56_1113/graph_dump.json")
by_doc = collections.defaultdict(list)
for c in D["chunks"]: by_doc[c["fileName"]].append(c)
rel_in_chunk = collections.Counter()
for r in D["relationships"]:
    for c in EC.get(r["source"], set()) & EC.get(r["target"], set()): rel_in_chunk[c] += 1
rows = []
for gp, sp in [("gold_add/gold_add46.json", "score_out/x31/add46_perdoc.json"), ("gold_add/gold_hs4_full.json", "score_out/x31/hs4_full_perdoc.json")]:
    g = json.load(open(gp, encoding="utf-8")); sc = json.load(open(sp, encoding="utf-8"))
    al = {e["id"]: [e["id"]] + e.get("aliases", []) for e in g["entities"]}; ty = {e["id"]: e["type"] for e in g["entities"]}
    hit = {(d, x["gold"]): x for d, v in sc["docs"].items() for x in v[P]["rows"]}
    ebest = {d: {e["gold"]: e["best"] for e in v[P]["ent_rows"]} for d, v in sc["docs"].items()}
    for r in g["relations"]:
        x = hit.get((r["doc"], f'{r["s"]} -[{r["p"]}]-> {r["o"]}'))
        if not x: continue
        ev = N(r.get("evidence")); skeys = [N(k) for k in al.get(r["s"], [r["s"]]) if len(N(k)) >= 2]; okeys = [N(k) for k in al.get(r["o"], [r["o"]]) if len(N(k)) >= 2]
        best = None
        for c in by_doc[r["doc"]]:
            t = N(c.get("text"))
            sc_ = (2 if ev[:10] and ev[:10] in t else 0) + (1 if any(k in t for k in okeys) else 0) + (1 if any(k in t for k in skeys) else 0)
            if sc_ >= 2 and (best is None or sc_ > best[0]): best = (sc_, c, t)
        row = dict(doc=r["doc"], s=r["s"], p=r["p"], o=r["o"], hit=bool(x["hit"]), typed=bool(x["typed"]), is_value=x["is_value"],
                   s_ent=ebest[r["doc"]].get(r["s"]), o_ent=ebest[r["doc"]].get(r["o"]), o_type=ty.get(r["o"]), s_type=ty.get(r["s"]), chunk=None)
        if best:
            _, c, t = best; raw = c.get("text") or ""
            row["chunk"] = c["id"]; row["chunk_chars"] = len(raw); row["s_in_chunk"] = any(k in t for k in skeys); row["o_in_chunk"] = any(k in t for k in okeys)
            # 目的語のある行が表の行か、主語が同じ行にあるか
            lines = raw.split("\n"); ol = [i for i, ln in enumerate(lines) if any(k in N(ln) for k in okeys)]
            sl = [i for i, ln in enumerate(lines) if any(k in N(ln) for k in skeys)]
            row["o_in_table"] = bool(ol) and all(lines[i].count("|") >= 2 for i in ol[:1])
            row["same_line"] = bool(set(ol) & set(sl))
            row["line_dist"] = min((abs(a - b) for a in ol for b in sl), default=None)
            row["o_pos"] = (ol[0] / max(1, len(lines) - 1)) if ol else None
            row["s_only_heading"] = bool(sl) and all(lines[i].lstrip().startswith(("[", "#")) for i in sl)
            row["chunk_table_lines"] = sum(1 for ln in lines if ln.count("|") >= 2)
        rows.append(row)
gold_in_chunk = collections.Counter(r["chunk"] for r in rows if r["chunk"])
for r in rows:
    if r["chunk"]: r["gold_in_chunk"] = gold_in_chunk[r["chunk"]]; r["rels_in_chunk"] = rel_in_chunk[r["chunk"]]
json.dump(rows, open("score_out/x31/x36_rows.json", "w", encoding="utf-8"), ensure_ascii=False)
loc = [r for r in rows if r["chunk"]]
print("正解のリレーション", len(rows), "チャンクに結べた", len(loc), "全体の再現率（緩い）", round(sum(r["hit"] for r in rows) / len(rows), 3), "結べた分", round(sum(r["hit"] for r in loc) / len(loc), 3))
def tab(name, key, rs=loc, order=None):
    c = collections.defaultdict(lambda: [0, 0])
    for r in rs:
        k = key(r)
        if k is None: continue
        c[k][0] += 1; c[k][1] += r["hit"]
    print(f"\n## {name}")
    for k in (order or sorted(c, key=str)):
        if k in c: print(f"  {k}: {c[k][1]}/{c[k][0]} = {c[k][1]/c[k][0]:.0%}")
b = lambda v, edges: None if v is None else next((f"{lo}〜{hi}" for lo, hi in edges if lo <= v <= hi), None)
tab("目的語のある行が表の行か", lambda r: "表の行" if r["o_in_table"] else "本文の行")
tab("主語と目的語が同じ行にあるか（本文の行）", lambda r: None if r["o_in_table"] else ("同じ行" if r["same_line"] else "主語がチャンクに無い" if not r["s_in_chunk"] else "主語は見出しの行だけ" if r["s_only_heading"] else f'別の行（{b(r["line_dist"], [(1,1),(2,3),(4,8),(9,999)])} 行離れ）'))
tab("主語と目的語が同じ行にあるか（表の行）", lambda r: None if not r["o_in_table"] else ("同じ行" if r["same_line"] else "主語がチャンクに無い" if not r["s_in_chunk"] else "主語は見出しの行だけ" if r["s_only_heading"] else "主語は別の行"))
tab("チャンクの中の正解のリレーションの数", lambda r: b(r["gold_in_chunk"], [(1,5),(6,10),(11,20),(21,40),(41,999)]), order=["1〜5","6〜10","11〜20","21〜40","41〜999"])
tab("目的語の位置（チャンクの前から）", lambda r: None if r["o_pos"] is None else b(int(r["o_pos"]*100), [(0,25),(26,50),(51,75),(76,100)]), order=["0〜25","26〜50","51〜75","76〜100"])
tab("値のリレーションか", lambda r: "値" if r["is_value"] else "値以外")
tab("目的語のエンティティが抽出されているか", lambda r: "ある" if r["o_ent"] in ("strict","modified","sentence","compound","props") else "無い", rs=rows)
tab("主語のエンティティが抽出されているか", lambda r: "ある" if r["s_ent"] in ("strict","modified","sentence","compound") else "無い", rs=rows)
