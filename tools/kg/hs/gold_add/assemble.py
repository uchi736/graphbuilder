# 検算済みの正解グラフ（ワークフローの結果）を 1 つにまとめ、機械検査を掛ける（X31）。
# 入力: results.json（[{doc, created, verified:{gold, changes, summary}}]）
# 出力: gold_add14.json（採点用。doc・accept を付ける）、out/<文書>.json（文書ごと）、check.md（機械検査の結果）
import json, re, sys, os, unicodedata, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "score"))
from common import norm2

ACC = json.load(open(os.path.join(HERE, "accept.json"), encoding="utf-8"))
TYPES = {"Document", "Standard", "Part", "Model", "Material", "Process", "TestItem", "Specimen", "Instrument", "Defect",
         "Criterion", "Parameter", "Property", "Organization", "Location", "Equipment", "Condition"}
res = json.load(open(sys.argv[1], encoding="utf-8"))
allg = {"_note": "X31 追加の正解グラフ（2026-11-14、Claude 作成・別の Claude が検算、人の確認なし）。決まりは gold_add/RULES.md。accept は accept.json の固定表。",
        "entities": [], "relations": [], "doc_references": [], "derived_relations": []}
seen = {}
lines = ["# 機械検査（X31）", ""]
tot = collections.Counter()
for item in res:
    doc = item["doc"] + ".pdf"
    v = item.get("verified")
    if not v:
        lines.append(f"- {doc}: 検算の結果なし"); continue
    g = v["gold"]
    src = open(os.path.join(HERE, "src", item["doc"] + ".txt"), encoding="utf-8").read()
    srcn = re.sub(r"\s+", "", norm2(src))
    ids = {e["id"] for e in g["entities"]}
    issues = []
    # 1) 字面: id か aliases のどれかが原本の文字層に在るか（空白を除いて）
    not_lit = []
    for e in g["entities"]:
        keys = [e["id"]] + e.get("aliases", [])
        if not any(re.sub(r"\s+", "", norm2(k)) in srcn for k in keys if k):
            not_lit.append(e["id"])
    # 2) 端点・種類
    bad_end = [f'{r["s"]} -[{r["p"]}]-> {r["o"]}' for r in g["relations"] if r["s"] not in ids or r["o"] not in ids]
    bad_p = [f'{r["s"]} -[{r["p"]}]-> {r["o"]}' for r in g["relations"] if r["p"] not in ACC]
    bad_t = [f'{e["id"]}:{e["type"]}' for e in g["entities"] if e["type"] not in TYPES]
    # 3) 並列・複合の名前（ENTITY_UNIT.md 8 の機械検査）
    para = [e["id"] for e in g["entities"] if re.search(r"及び|又は|並びに|・|、", e["id"]) and e["type"] not in ("Criterion", "Parameter", "Condition", "Document", "Standard")]
    noco = [e["id"] for e in g["entities"] if "の" in e["id"] and e["type"] not in ("Criterion", "Parameter", "Condition", "Document", "Standard")]
    # 4) 重複リレーション
    dup = [k for k, c in collections.Counter((r["s"], r["p"], r["o"]) for r in g["relations"]).items() if c > 1]
    # 5) 根拠の字面
    ev_miss = [f'{r["s"]} -[{r["p"]}]-> {r["o"]}' for r in g["relations"]
               if r.get("evidence") and re.sub(r"\s+", "", norm2(r["evidence"]))[:12] not in srcn]
    ch = collections.Counter(c["action"] for c in v["changes"])
    tot.update(ch); tot["relations"] += len(g["relations"]); tot["entities"] += len(g["entities"])
    lines.append(f"## {doc}")
    lines.append(f"- エンティティ {len(g['entities'])}・リレーション {len(g['relations'])}・文書間参照 {len(g['doc_references'])}・derived {len(g['derived_relations'])}")
    lines.append(f"- 検算の変更: {dict(ch)}（作成時 リレーション {len((item.get('created') or {}).get('relations', []))}）")
    for name, xs in [("原本の文字層に無い名前", not_lit), ("端点が entities に無い", bad_end), ("種類が一覧外", bad_p), ("型が一覧外", bad_t),
                     ("並列を含む名前", para), ("「の」を含む名前", noco), ("重複", dup), ("根拠の引用が文字層に無い（先頭 12 字）", ev_miss)]:
        if xs: lines.append(f"- {name} {len(xs)}: " + "、".join(map(str, xs[:15])))
    lines.append("")
    # 採点用にまとめる（端点の無い線と一覧外の種類は除く）
    keep = [r for r in g["relations"] if r["s"] in ids and r["o"] in ids and r["p"] in ACC]
    for e in g["entities"]:
        e = dict(e, docs=[doc])
        if e["id"] in seen:
            seen[e["id"]]["docs"] = sorted(set(seen[e["id"]]["docs"]) | {doc})
            seen[e["id"]]["aliases"] = sorted(set(seen[e["id"]]["aliases"]) | set(e.get("aliases", [])))
        else:
            seen[e["id"]] = e
    allg["relations"] += [dict(s=r["s"], p=r["p"], o=r["o"], doc=doc, accept=ACC[r["p"]], evidence=r.get("evidence"), page=r.get("page")) for r in keep]
    allg["doc_references"] += [dict(x, doc=doc) for x in g["doc_references"]]
    allg["derived_relations"] += [dict(x, doc=doc) for x in g["derived_relations"]]
    json.dump(dict(g, doc=doc, changes=v["changes"], summary=v["summary"]), open(os.path.join(HERE, "out", item["doc"] + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
allg["entities"] = list(seen.values())
json.dump(allg, open(os.path.join(HERE, sys.argv[2] if len(sys.argv) > 2 else "gold_add14.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
lines.insert(2, f"合計: エンティティ {tot['entities']}・リレーション {tot['relations']}。検算の変更 消した {tot['removed']}・直した {tot['fixed']}・足した {tot['added']}・エンティティを直した {tot['entity_fixed']}\n")
open(os.path.join(HERE, "check.md"), "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines[:4]))
