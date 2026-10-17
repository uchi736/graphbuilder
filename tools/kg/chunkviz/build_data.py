import json, collections, itertools
R = json.load(open("raw.json", encoding="utf-8"))
C = {c["id"]: c for c in R["chunks"]}
# 文書ごとの並び: FIRST_CHUNK から、同じ文書に属する NEXT_CHUNK だけを辿る
order = {}
for d in R["docs"]:
    doc, cur, seq, seen = d["doc"], d["first"], [], set()
    while cur and cur not in seen:
        seen.add(cur); seq.append(cur)
        nxt = [n for n in C[cur]["next"] if doc in C[n]["docs"] and n not in seen]
        cur = nxt[0] if nxt else None
    order[doc] = seq
    members = [c["id"] for c in R["chunks"] if doc in c["docs"]]
    print(f"{doc:22s} walked {len(seq):3d} / members {len(members):3d}  ambiguous_steps={sum(1 for c in seq if len([n for n in C[c]['next'] if doc in C[n]['docs']])>1)}")
ents = {e["eid"]: e for e in R["entities"]}
c2e = collections.defaultdict(set); e2c = collections.defaultdict(set)
for h in R["has_entity"]:
    c2e[h["chunk"]].add(h["eid"]); e2c[h["eid"]].add(h["chunk"])
deg = collections.Counter(len(v) for v in e2c.values())
print("entity -> #chunks distribution:", sorted(deg.items()))
print("top hub entities:", [(ents[e]["label"], ents[e]["name"], len(cs)) for e, cs in sorted(e2c.items(), key=lambda x: -len(x[1]))[:15]])
print("label counts:", collections.Counter(e["label"] for e in R["entities"]).most_common(14))
# 文書ごとのエンティティ集合と共有
doc_e = {doc: set().union(*[c2e[c] for c in seq]) if seq else set() for doc, seq in order.items()}
for a, b in itertools.combinations(order, 2):
    s = doc_e[a] & doc_e[b]
    if s: print(f"shared entities {a} x {b}: {len(s)}")
print("chunks without entities per doc:", {d: sum(1 for c in s if not c2e[c]) for d, s in order.items()})
json.dump({"order": order}, open("order.json", "w"), ensure_ascii=False)
