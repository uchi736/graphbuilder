"""チャンク接続の現況ページを生成する。入力: raw.json / order.json（Neo4j から取得済み）。"""
import collections
import html
import itertools
import json

R = json.load(open("raw.json", encoding="utf-8"))
order = json.load(open("order.json"))["order"]

DOCS = [
    ("HS-VTR-T001E.pdf", "T001E", "単独試運転要領・判定基準", "VTR-T001"),
    ("HS-VTR-T002F.pdf", "T002F", "VTR4 形 初号機の性能確認試験", "VTR-T002"),
    ("VTR-T003D.pdf", "T003D", "性能管理曲線の管理基準", "VTR-T003"),
    ("VTR-T004C.pdf", "T004C", "RU・RH 形 初号機の性能確認試験", "VTR-T004"),
    ("VTR-T005B.pdf", "T005B", "TPL.TPS 形 初号機の性能確認試験", "VTR-T005"),
    ("VTR-T_発行目録.pdf", "発行目録", "基準 5 本の一覧", None),
]
C = {c["id"]: c for c in R["chunks"]}
E = {e["eid"]: e for e in R["entities"]}
c2e = collections.defaultdict(set)
for h in R["has_entity"]:
    c2e[h["chunk"]].add(h["eid"])


def clean(s):
    return " ".join(str(s).replace("�", "").split())


# ノード（6 文書に属するチャンク。本文が同一のチャンクは 1 個）
node_ids, nid = [], {}
for f, _, _, _ in DOCS:
    for cid in order[f]:
        if cid not in nid:
            nid[cid] = len(node_ids)
            node_ids.append(cid)

own_names = {own for _, _, _, own in DOCS if own}
used = sorted({e for c in node_ids for e in c2e[c]}, key=lambda e: (E[e]["label"] or "", E[e]["name"] or ""))
eidx = {e: i for i, e in enumerate(used)}
ents = [{"n": clean(E[e]["name"])[:44], "l": E[e]["label"] or "", "d": 1 if (E[e]["label"] == "Standard" and E[e]["name"] in own_names) else 0}
        for e in used]
docnum = {e for e in used if ents[eidx[e]]["d"]}

nodes = []
for cid in node_ids:
    t = clean(C[cid]["text"])
    nodes.append({"t": t[:170], "len": len(t), "e": sorted(eidx[e] for e in c2e[cid])})
docs = [{"f": f, "s": s, "r": role, "own": own, "seq": [nid[c] for c in order[f]]} for f, s, role, own in DOCS]
nxt = sorted({(nid[c], nid[n]) for c in node_ids for n in C[c]["next"] if n in nid})

# 集計
n_has_entity = sum(len(c2e[c]) for c in node_ids)
doc_of = collections.defaultdict(set)
for di, d in enumerate(docs):
    for n in d["seq"]:
        doc_of[n].add(di)
pairs = {"in_ex": 0, "x_ex": 0, "in_all": 0, "x_all": 0}
for a, b in itertools.combinations(range(len(nodes)), 2):
    s_all = c2e[node_ids[a]] & c2e[node_ids[b]]
    s_ex = s_all - docnum
    same = bool(doc_of[a] & doc_of[b])
    if s_ex:
        pairs["in_ex" if same else "x_ex"] += 1
    if s_all:
        pairs["in_all" if same else "x_all"] += 1
e2c = collections.defaultdict(set)
for c in node_ids:
    for e in c2e[c]:
        e2c[e].add(c)
n_ent = len(e2c)
n_single = sum(1 for cs in e2c.values() if len(cs) == 1)
n_bridge = n_ent - n_single
shared_nodes = [n for n in range(len(nodes)) if len(doc_of[n]) > 1]

doc_e = [set().union(*[c2e[node_ids[n]] for n in d["seq"]]) - docnum for d in docs]
mat = [[len(doc_e[i] & doc_e[j]) for j in range(len(docs))] for i in range(len(docs))]
num_names = sorted(own_names)
refmat = [[sum(1 for n in d["seq"] if any(E[e]["name"] == nm for e in c2e[node_ids[n]] & docnum)) for nm in num_names] for d in docs]


def seq_fill(v, bins):
    """値を順序のある青 1 色の濃淡に対応付ける。戻り値: (背景色, 文字色)"""
    steps = [("#FFFFFF", "#7A8794"), ("#cde2fb", "#1B2430"), ("#9ec5f4", "#1B2430"), ("#5598e7", "#1B2430"), ("#1c5cab", "#FFFFFF")]
    k = 0
    for i, b in enumerate(bins):
        if v >= b:
            k = i + 1
    return steps[k]


def matrix_html():
    out = ['<table class="mx"><thead><tr><th></th>' + "".join(f"<th>{html.escape(d['s'])}</th>" for d in docs) + "</tr></thead><tbody>"]
    for i, d in enumerate(docs):
        row = [f"<th>{html.escape(d['s'])}</th>"]
        for j in range(len(docs)):
            if i == j:
                row.append('<td class="dg">―</td>')
            else:
                bg, fg = seq_fill(mat[i][j], [1, 5, 10, 20])
                row.append(f'<td style="background:{bg};color:{fg}">{mat[i][j]}</td>')
        out.append("<tr>" + "".join(row) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def refmat_html():
    out = ['<table class="mx"><thead><tr><th></th>' + "".join(f"<th>{nm.replace('VTR-', '')}</th>" for nm in num_names) + "</tr></thead><tbody>"]
    for i, d in enumerate(docs):
        row = [f"<th>{html.escape(d['s'])}</th>"]
        for j, nm in enumerate(num_names):
            v = refmat[i][j]
            if d["own"] == nm:
                row.append(f'<td class="own">{v}</td>')
            else:
                bg, fg = seq_fill(v, [1, 2, 3, 99])
                row.append(f'<td style="background:{bg};color:{fg}">{v}</td>')
        out.append("<tr>" + "".join(row) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def shared_desc():
    items = []
    for n in shared_nodes:
        where = "と".join(f"{docs[di]['s']} の {docs[di]['seq'].index(n) + 1} 番目" for di in sorted(doc_of[n]))
        items.append(f"<li>{where}。本文の冒頭は「{html.escape(nodes[n]['t'][:46])}」である。</li>")
    return "".join(items)


DATA = {"docs": docs, "nodes": nodes, "ents": ents, "next": nxt, "sel": docs[0]["seq"][1]}
vals = {
    "__DATA__": json.dumps(DATA, ensure_ascii=False, separators=(",", ":")),
    "__N_NODES__": str(len(nodes)),
    "__N_MEMBER__": str(sum(len(d["seq"]) for d in docs)),
    "__N_NEXT__": str(len(nxt)),
    "__N_SIMILAR__": str(R["similar"]),
    "__N_SHARED__": str(len(shared_nodes)),
    "__N_HASENT__": f"{n_has_entity:,}",
    "__P_EX__": f"{pairs['in_ex'] + pairs['x_ex']:,}",
    "__P_IN_EX__": str(pairs["in_ex"]),
    "__P_X_EX__": str(pairs["x_ex"]),
    "__P_ALL__": f"{pairs['in_all'] + pairs['x_all']:,}",
    "__P_TOTAL__": f"{len(nodes) * (len(nodes) - 1) // 2:,}",
    "__N_ENT__": str(n_ent),
    "__N_SINGLE__": str(n_single),
    "__N_BRIDGE__": str(n_bridge),
    "__MATRIX__": matrix_html(),
    "__REFMATRIX__": refmat_html(),
    "__SHARED_DESC__": shared_desc(),
}
page = open("template.html", encoding="utf-8").read()
for k, v in vals.items():
    assert k in page, k
    page = page.replace(k, v)
assert "�" not in page
open("chunk-links.html", "w", encoding="utf-8").write(page)
print({k: v for k, v in vals.items() if k.startswith("__N_") or k.startswith("__P_")})
print("matrix", mat)
print("refmat", refmat)
print("bytes", len(page.encode("utf-8")))
