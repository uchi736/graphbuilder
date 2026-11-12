import json, re, unicodedata, collections, sys
OUT = sys.argv[1]
texts = collections.defaultdict(dict)
for line in open(f"{OUT}/pagetext.jsonl", encoding="utf-8"):
    r = json.loads(line); texts[r["file"]][r["page"]] = r["text"]
def norm(s):
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"[#|*_`>\-=:~\[\]]", "", s)
    return re.sub(r"\s+", "", s)
g = json.load(open("/home/uchida/ai-stack/llm-graph-builder/tools/kg/hs/runs/full56_night1109/graph_dump_linked.json"))
by = collections.defaultdict(str)
for c in g["chunks"]:
    by[c["fileName"]] += norm(re.sub(r"^\[[^\]\n]*\]\n", "", c["text"]))
res = {}
for fn, pages in texts.items():
    body = by.get(fn, "")
    out = {}
    for pg, t in sorted(pages.items()):
        lines = [l for l in re.split(r"[\r\n]+", t) if norm(l)]
        miss, tot, mchars = [], 0, 0
        for l in lines:
            n = norm(l); tot += len(n)
            if len(n) < 2: continue
            if n not in body:
                # 行の中の空白で区切った断片ごとに見る(表の行はセル順が変わるため)
                frags = [norm(f) for f in re.split(r"\s{2,}|\t", l) if len(norm(f)) >= 2]
                lost = [f for f in frags if f not in body] if len(frags) > 1 else [n]
                if lost:
                    mchars += sum(len(f) for f in lost); miss.append("／".join(lost))
        out[pg] = {"layer_chars": tot, "missing_chars": mchars, "missing_ratio": round(mchars / tot, 3) if tot else None, "missing": miss}
    res[fn] = out
json.dump(res, open(f"{OUT}/missing_lines.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
rows = sorted(((v["missing_chars"], fn, pg, v["missing_ratio"], v["missing"]) for fn, ps in res.items() for pg, v in ps.items()), reverse=True)
tot_layer = sum(v["layer_chars"] for ps in res.values() for v in ps.values()); tot_miss = sum(v["missing_chars"] for ps in res.values() for v in ps.values())
print("total layer", tot_layer, "missing", tot_miss)
for r in rows[:45]:
    print(r[0], r[1][:30], r[2], r[3], " | ".join(r[4])[:300])
