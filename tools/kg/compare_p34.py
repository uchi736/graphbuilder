# S4-a(標準) / ③ / ④ の横並び: 正解データ採点 + マルチホップ (キーワード仮採点)
import json,re,sys,os
S=sys.argv[1]
def gold(p):
    if not os.path.exists(p): return None
    return json.loads(re.search(r'\{.*\}',open(p,encoding='utf-8').read(),re.S).group(0))
G={"S4-a 標準":gold(f"{S}/s4a_gold.json"),"③ 線に限定子":gold(f"{S}/gold_p3.json"),"④ 規定ノード":gold(f"{S}/gold_p4.json")}
print("=== 正解データ採点 ===")
print(f"  {'構成':14s} {'実体再現':>9s} {'分裂':>5s} {'平均ノード':>8s} {'型一致':>6s} {'関係any':>8s} {'typed':>6s} {'参照(Std)':>9s}")
for k,d in G.items():
    if not d: print(f"  {k:14s} (未)"); continue
    e,r,dr=d["entities"],d["relations"],d["doc_references"]
    print(f"  {k:14s} {e['found']:>4}/{e['gold']:<4} {e['fragmented(>1node)']:>5} {e['avg_nodes_per_found']:>8} {e['type_match']:>6} {r['any_edge']:>4}/{r['gold']:<3} {r['typed']:>6} {str(dr.get('standard_refs','-')):>4}/{dr['gold']}")
    if k!="S4-a 標準":
        print("      未発見:", e["missing"][:12]); print("      関係の取れなかったもの:", len(r["misses"]), "例:", r["misses"][:4])
keys={"M01":["50kPa"],"M02":["10分"],"M03":["0.8"],"M04":["3%","3％"],"M05":["02313"],"M06":["00862"],"M07":["-10"],"M08":["6点","02313"],"M09":["2点","4台"],"M10":["検査部門長"],"M11":["027","00862","02313"],"M12":["45"],"M13":["回転機事業部"],"M14":["取り付けない","取付けない","取付ない","6点"],"M15":["2%","2％","C側"],"M16":["T側"]}
def grade(p):
    if not os.path.exists(p): return None
    A={json.loads(l)["id"]:json.loads(l) for l in open(p,encoding="utf-8")}; out={}
    for q,ks in keys.items():
        a=A.get(q,{}).get("answer",""); hit=[k for k in ks if k in a]
        # M04/M15: 全角/半角どちらかで可
        need=len(ks) - (1 if q in("M04","M15") else 0)
        out[q]="○" if len(hit)>=max(1,need) else ("△" if hit else "×")
    return out
print("\n=== マルチホップ (キーワード仮採点; 最終は目視) ===")
cols=[("S4-a 既定",f"{S}/eval_multihop_graph_vector_fulltext.jsonl"),("S4-a gv",f"{S}/eval_multihop_graph_vector.jsonl"),
      ("③ 既定",f"{S}/eval_multihop_p3_graph_vector_fulltext.jsonl"),("③ gv",f"{S}/eval_multihop_p3_graph_vector.jsonl"),
      ("④ 既定",f"{S}/eval_multihop_p4_graph_vector_fulltext.jsonl"),("④ gv",f"{S}/eval_multihop_p4_graph_vector.jsonl")]
R={c:grade(p) for c,p in cols}
print("  "+"      ".join(f"{c:>8s}" for c,_ in cols))
for q in keys:
    print(f"  {q} "+"  ".join(f"{(R[c] or {}).get(q,'-'):>12s}" for c,_ in cols))
for c,_ in cols:
    g=R[c]; 
    if g: print(f"  {c:10s} ○{sum(v=='○' for v in g.values())} △{sum(v=='△' for v in g.values())} ×{sum(v=='×' for v in g.values())}")
