"""X20 の数え直し: 後処理を流し直した後の章・節への参照を、夜間の判定（x20_mech.json の並び × x20_judged.json の final）に照らして数える。

使い方: python3 x20_recount.py <新しい refs_dump.json>
- 参照元チャンク・参照の一文（空白を除いた先頭 80 字）・参照先の節が同じなら同じ参照とみなし、夜間の最終判定を引き継ぐ。
- 夜間に無かった参照は「未判定」として一覧に出す（人か判定役が見る）。消えた参照も一覧に出す。
"""
import collections
import json
import re
import sys
import unicodedata

HERE = __file__.rsplit("/", 1)[0]
norm = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")))
key = lambda r: (r["source"], norm(r.get("quote"))[:80], r.get("section_id"))

old = json.load(open(f"{HERE}/x20_mech.json", encoding="utf-8"))["section_refs"]
final = {f["index"]: f for f in json.load(open(f"{HERE}/x20_judged.json", encoding="utf-8"))["final"]}
new = json.load(open(sys.argv[1], encoding="utf-8"))["section_refs"]

old_by_key = {key(r): (i, r) for i, r in enumerate(old)}
new_keys = set()
counts, by_via, unjudged = collections.Counter(), collections.Counter(), []
for r in new:
    k = key(r)
    new_keys.add(k)
    hit = old_by_key.get(k)
    if hit is None:
        unjudged.append({k2: r.get(k2) for k2 in ("source_file", "quote", "via", "section_title")})
        counts["未判定"] += 1
        continue
    v = final[hit[0]]["verdict"]
    counts[v] += 1
    by_via[(r.get("via"), v)] += 1
removed = [{"index": i, "quote": r.get("quote"), "via": r.get("via"), "verdict": final[i]["verdict"]}
           for k, (i, r) in old_by_key.items() if k not in new_keys]
print(json.dumps({"section_refs": len(new), "verdicts": dict(counts),
                  "by_via": {f"{a}/{b}": c for (a, b), c in sorted(by_via.items(), key=str)},
                  "removed": removed, "unjudged": unjudged}, ensure_ascii=False, indent=1))
