"""ページの文字層の各文字が、同じファイルのチャンク本文の 5-gram で覆われるかを数える。覆われない連続部分(4 字以上)を落ちた候補として出す。"""
import json, re, unicodedata, collections, sys
OUT = sys.argv[1]; N = 5
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
    by[c["fileName"]] += norm(re.sub(r"^\[[^\]\n]*\]\n", "", c["text"])) + "\u0000"
res = {}
for fn, pages in texts.items():
    body = by.get(fn, "")
    G = {body[i:i+N] for i in range(len(body) - N + 1)}
    out = {}
    for pg, t in sorted(pages.items()):
        s = norm(t); cov = [False] * len(s)
        for i in range(len(s) - N + 1):
            if s[i:i+N] in G:
                for j in range(i, i + N): cov[j] = True
        spans, cur = [], ""
        for ch, c in zip(s, cov):
            if not c: cur += ch
            else:
                if len(cur) >= 4: spans.append(cur)
                cur = ""
        if len(cur) >= 4: spans.append(cur)
        miss = sum(1 for c in cov if not c)
        out[pg] = {"layer_chars": len(s), "uncovered_chars": miss, "uncovered_ratio": round(miss / len(s), 3) if s else None, "uncovered_spans": spans}
    res[fn] = out
json.dump(res, open(f"{OUT}/coverage_5gram.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
tl = sum(v["layer_chars"] for ps in res.values() for v in ps.values()); tm = sum(v["uncovered_chars"] for ps in res.values() for v in ps.values())
print("total layer", tl, "uncovered", tm, round(tm / tl, 3))
rows = sorted(((v["uncovered_chars"], fn, pg, v["uncovered_ratio"], v["uncovered_spans"]) for fn, ps in res.items() for pg, v in ps.items()), reverse=True)
for r in rows[:60]:
    print(r[0], r[1][:30], r[2], r[3], " | ".join(r[4])[:260])
