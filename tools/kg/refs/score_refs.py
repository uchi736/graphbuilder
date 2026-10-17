"""参照リンクの結果（chunk_refs.json）を、手作業の正解（gold_refs.json）と突き合わせる。

- 抽出: 正解の参照が、抽出結果に含まれるか（文書と原文の一部で照合）
- 文書: 参照先の文書を正しく特定したか
- チャンク: 結んだチャンクに、参照先にあるはずの語が含まれるか
使い方: python3 score_refs.py chunk_refs.json
"""
import collections
import json
import pathlib
import re
import sys
import unicodedata

here = pathlib.Path(__file__).parent
sq = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))
gold = json.load(open(here / "gold_refs.json", encoding="utf-8"))["references"]
res = json.load(open(sys.argv[1], encoding="utf-8"))
refs = res["references"]
used = set()
rows = []
for g in gold:
    hits = [i for i, r in enumerate(refs) if r["source_doc"] == g["src"] and sq(g["key"]) in sq(r["quote"])]
    used.update(hits)
    row = {"g": g, "extracted": bool(hits), "doc_ok": False, "chunk": "なし", "loose": False, "links": 0, "links_ok": 0}
    for i in hits:
        r = refs[i]
        if r["target_doc"] == g["target"]:
            row["doc_ok"] = True
        for t in r["targets"]:
            row["links"] += 1
            text = sq(t["text"])
            ok = r["target_doc"] == g["target"] and any(sq(a) in text for a in g["accept"])
            loose = r["target_doc"] == g["target"] and any(sq(a) in text for a in g.get("accept_loose", []))
            row["links_ok"] += ok
            row["loose"] = row["loose"] or loose
    if row["links_ok"]:
        row["chunk"] = "正"
    elif row["links"]:
        row["chunk"] = "近い" if row["loose"] else "誤"
    rows.append(row)

need = [r for r in rows if not r["g"].get("optional")]
print(f"正解の参照 {len(need)} 件（任意 {len(rows) - len(need)} 件を除く）")
print(f"  抽出できた           {sum(r['extracted'] for r in need)}/{len(need)}")
print(f"  参照先の文書が正しい {sum(r['doc_ok'] for r in need)}/{len(need)}")
chunk_level = [r for r in need if r["g"]["level"] == "chunk"]
c = collections.Counter(r["chunk"] for r in chunk_level)
print(f"  位置まで名指しした参照 {len(chunk_level)} 件: 正しいチャンク {c['正']}、隣のチャンク {c['近い']}、誤り {c['誤']}、結べず {c['なし']}")
doc_level = [r for r in need if r["g"]["level"] == "doc"]
c = collections.Counter(r["chunk"] for r in doc_level if r["g"]["accept"])
print(f"  文書だけを名指しした参照 {len(doc_level)} 件: 文書が正しい {sum(r['doc_ok'] for r in doc_level)}、うち内容の合うチャンクに結んだ {c['正']}、別のチャンク {c['誤']}、文書どまり {c['なし']}")
by_kind = collections.defaultdict(lambda: [0, 0, 0])
for r in need:
    k = by_kind[r["g"]["kind"]]
    k[0] += 1; k[1] += r["extracted"]; k[2] += (r["chunk"] == "正") if r["g"]["level"] == "chunk" else r["doc_ok"]
print("  種類別（件数 / 抽出 / 結び先が正しい）:", {k: v for k, v in by_kind.items()})
extra = [r for i, r in enumerate(refs) if i not in used]
c = collections.Counter(r["scope"] for r in extra)
print(f"正解に無い抽出 {len(extra)} 件: 外部の文書 {c['external']}、同じ文書 {c['same_document']}、他の文書 {c['other_document']}")
print("外部の文書の表記:", sorted({r['target_document'] for r in extra if r['scope'] == 'external'}))
print("-- 取りこぼしと誤り")
for r in need:
    if not r["extracted"] or (r["g"]["level"] == "chunk" and r["chunk"] != "正") or not r["doc_ok"]:
        print(f"  [{r['g']['src'][:12]}] {r['g']['key'][:28]:<28} 抽出={'○' if r['extracted'] else '×'} 文書={'○' if r['doc_ok'] else '×'} チャンク={r['chunk']}")
print("-- 正解に無い抽出のうち、外部以外")
for r in extra:
    if r["scope"] != "external":
        print(f"  [{r['source_doc'][:12]}#{r['source_position']}] {r['quote'][:50]} → {r['target_doc']} {[t['position'] for t in r['targets']]}")
json.dump([{**{k: v for k, v in r.items() if k != 'g'}, **r["g"]} for r in rows], open(here / "score_refs.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
