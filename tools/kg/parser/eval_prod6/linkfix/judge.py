"""章・節への参照の判定（X18・X20 と同じ 3 区分: 正しい・広すぎる・誤り）。
同じ参照元・同じ一文の参照が 11/9 の判定（refjudge/judged.json）にあれば、その正解の結び先（correct_target_ids）で機械的に判定する:
  参照先のチャンク ⊆ 正解 → 正しい、交わりあり → 広すぎる、交わりなし → 誤り。
11/9 の判定に無い参照は、manual.json（Claude が参照元と参照先の本文を読んで付けた判定）を使う。無ければ「未判定」。
文書の最上位の節（参照先の文書の題だけの節）に結んだ参照は、11/9 の判定（index 22）と同じく正しいとする。
使い方: python3 judge.py <refs_dump_名前.json> [manual.json]  → 判定の表を標準出力に、<名前>_judged.json に書く"""
import json, os, re, sys, unicodedata, collections
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
norm = lambda s: re.sub(r"[\s*_|`\\]", "", unicodedata.normalize("NFKC", s or ""))
items = json.load(open(os.path.join(BASE, "refjudge/judge_input.json")))["items"]
final = {x["index"]: x for x in json.load(open(os.path.join(BASE, "refjudge/judged.json")))["final"]}
gold = {}
for it in items:
    f = final[it["index"]]
    key = (it["source_chunk"], norm(it["quote"])[:40])
    if f.get("correct_target_ids"):
        gold.setdefault(key, set()).update(f["correct_target_ids"])
path = sys.argv[1]
manual = {}
if len(sys.argv) > 2 and os.path.exists(sys.argv[2]):
    for m in json.load(open(sys.argv[2])):
        manual[(m["source_file"], m["source_position"], norm(m["quote_prefix"]), m["section_file"], m["section_path"])] = m
dump = json.load(open(path))
chunks = {c["id"]: c for c in dump["chunks"]}
out, counts = [], collections.Counter()
for r in sorted(dump["section_refs"], key=lambda r: (r["source_file"], chunks[r["source"]]["position"], r["quote"] or "", r["section_file"], r["section_path"])):
    t = set(r["targets"]) - {r["source"]}
    key = (r["source"], norm(r["quote"])[:40])
    g = gold.get(key)
    mk = next((k for k in manual if k[0] == r["source_file"] and k[1] == chunks[r["source"]]["position"] and norm(r["quote"]).startswith(k[2])
               and k[3] == r["section_file"] and k[4] == " > ".join(r["section_path"] or [])), None)
    if g is None and mk is not None:
        m = manual[mk]
        verdict, how, reason = m["verdict"], "manual", m.get("reason", "")
    elif g is not None:
        verdict = "正しい" if t and t <= g else ("広すぎる" if t & g else "誤り")
        how, reason = "mechanical", ""
    elif len(r["section_path"] or []) == 1 and r["section_title"] and r["section_title"].replace(".pdf", "") in r["section_file"]:
        verdict, how, reason = "正しい", "document_top", "文書の題だけの最上位の節（11/9 の index 22 と同じ扱い）"
    else:
        verdict, how, reason = "未判定", "", ""
    counts[verdict] += 1
    pos = {c: chunks[c]["position"] for c in t if c in chunks}
    out.append({"source": r["source"], "source_file": r["source_file"], "source_position": chunks[r["source"]]["position"], "quote": r["quote"],
                "via": r["via"], "section_id": r["section_id"], "section_file": r["section_file"], "section_path": r["section_path"],
                "targets": sorted(t, key=lambda c: pos.get(c, 0)), "target_positions": sorted(pos.values()),
                "gold_positions": sorted(chunks[c]["position"] for c in g if c in chunks) if g is not None else None,
                "verdict": verdict, "how": how, "reason": reason})
name = os.path.basename(path).replace("refs_dump_", "").replace(".json", "")
json.dump({"counts": dict(counts), "n": len(out), "refs": out}, open(os.path.join(HERE, f"{name}_judged.json"), "w"), ensure_ascii=False, indent=1)
for x in out:
    print(f'{x["verdict"]:5} {x["how"][:4]:4} {x["via"]:8} {x["source_file"][:9]}#{x["source_position"]:<2} {(x["quote"] or "")[:38]!s:40} -> {x["section_file"][:9]} {" > ".join(x["section_path"])[:50]} t={x["target_positions"]} g={x["gold_positions"]}')
print(dict(counts), len(out))
