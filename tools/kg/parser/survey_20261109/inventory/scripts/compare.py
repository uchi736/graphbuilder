import json, re, unicodedata, collections, sys
OUT = sys.argv[1]
inv = json.load(open(f"{OUT}/inventory_raw.json"))
texts = collections.defaultdict(dict)
for line in open(f"{OUT}/pagetext.jsonl", encoding="utf-8"):
    r = json.loads(line); texts[r["file"]][r["page"]] = r["text"]

def norm(s):
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"[#|*_`>\-=:~\[\]]", "", s)
    return re.sub(r"\s+", "", s)

def grams(s, n=3):
    return {s[i:i+n] for i in range(len(s) - n + 1)}

res = {}
for tag, path in [("full56", "/home/uchida/ai-stack/llm-graph-builder/tools/kg/hs/runs/full56_night1109/graph_dump_linked.json"),
                  ("prod6", "/home/uchida/ai-stack/llm-graph-builder/tools/kg/hs/runs/prod_night1109/graph_dump.json")]:
    g = json.load(open(path))
    by = collections.defaultdict(list)
    for c in g["chunks"]:
        by[c["fileName"]].append(c)
    out = {}
    for fn, cs in by.items():
        raw = "".join(c["text"] for c in cs)
        body = "".join(re.sub(r"^\[[^\]\n]*\]\n", "", c["text"]) for c in cs)  # 先頭の [節] 行を外す
        nb = norm(body)
        G = grams(nb)
        pages = {}
        for pg, t in sorted(texts.get(fn, {}).items()):
            nt = norm(t)
            pg_g = grams(nt)
            cov = (len(pg_g & G) / len(pg_g)) if pg_g else None
            chunk_pages = sum(1 for c in cs if c.get("page") == pg)
            pages[pg] = {"layer_chars": len(nt), "coverage_3gram": round(cov, 3) if cov is not None else None, "chunks_starting": chunk_pages}
        out[fn] = {"chunks": len(cs), "chunk_chars_raw": len(raw), "chunk_chars_body_norm": len(nb), "pages": pages,
                   "sections": sorted({c.get("section") or "" for c in cs})}
    res[tag] = out
json.dump(res, open(f"{OUT}/chunk_compare.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
