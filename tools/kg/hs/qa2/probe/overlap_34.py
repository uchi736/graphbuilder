# 34 問について「質問と答えのチャンクの語の重なり」を数える。
# 指標: NFKC 正規化後の文字 2-gram の Jaccard、質問側 2-gram のうち答えのチャンクに含まれる割合（coverage）、
#       英数字トークン（型式・文書番号・材質名・規格番号）の一致数、そのトークンを含むチャンクの数（コーパス全体での希少さ）。
import json, re, unicodedata, pathlib, collections, statistics

HERE = pathlib.Path(__file__).resolve().parent.parent
chunks = json.load(open(HERE / "chunks.json", encoding="utf-8"))
qs = json.load(open(HERE / "inputs/questions_hs.json", encoding="utf-8"))["questions"]
req = json.load(open(HERE / "inputs/required_chunks.json", encoding="utf-8"))
probe = {r["qid"]: r for r in json.load(open(HERE / "out/probe_34.json", encoding="utf-8"))}
ev = {q["qid"]: q for q in json.load(open(HERE / "inputs/eval_full56_final.json", encoding="utf-8"))["questions"]}

PUNCT = re.compile(r"[\s、。，．,.・:：;；()（）\[\]「」『』|<>＜＞*＊/／\-−–—=＝~〜～≧≦±%％#]+")


def norm(t):
    return unicodedata.normalize("NFKC", t or "").lower()


def bigrams(t):
    s = PUNCT.sub("", norm(t))
    return set(s[i:i + 2] for i in range(len(s) - 1))


def alnum_tokens(t):
    # 英数字の語（型式 AT14、文書番号 HS-RU-015、材質 SCM440H、規格 JIS Z 2201 の数字部分など）
    s = norm(t)
    toks = set(re.findall(r"[a-z]*[0-9][a-z0-9]*(?:[.\-][a-z0-9]+)*|[a-z]{2,}[0-9][a-z0-9\-]*", s))
    # 2 文字以上の英字だけの語（jis, tig, hv など）も別に拾う
    toks |= set(re.findall(r"(?<![a-z0-9])[a-z]{2,}(?![a-z0-9])", s))
    drop = {"n", "mm", "mm2", "jis", "hs", "ru", "tp", "vtr"}  # 単独では弁別力の無いもの
    return {t for t in toks if t not in drop and not re.fullmatch(r"[0-9]", t)}


def chunk_text_norm(cid):
    return norm(chunks[cid]["text"])


# コーパス全体で、英数字トークンを含むチャンク数（希少さ）
all_norm = {cid: norm(c["text"]) for cid, c in chunks.items()}


def df_of(tok):
    return sum(1 for t in all_norm.values() if tok in t)


rows = []
for q in qs:
    qid = q["qid"]
    question = q["question"]
    qb = bigrams(question)
    qa = alnum_tokens(question)
    golds = [g for grp in req[qid]["required"] for g in grp]
    pr = {g["chunk"]: g for g in probe[qid]["gold"]}
    per = []
    for g in golds:
        cb = bigrams(chunks[g]["text"])
        inter = qb & cb
        jac = len(inter) / max(1, len(qb | cb))
        cov = len(inter) / max(1, len(qb))
        ct = all_norm[g]
        hit = sorted(t for t in qa if t in ct)
        miss = sorted(t for t in qa if t not in ct)
        p = pr[g]
        per.append({
            "chunk": g, "doc": chunks[g]["doc"], "section": chunks[g]["section"],
            "chars": len(chunks[g]["text"]),
            "jaccard": round(jac, 3), "coverage": round(cov, 3),
            "alnum_hit": hit, "alnum_miss": miss,
            "alnum_hit_df": {t: df_of(t) for t in hit},
            "vec_rank": p["vec_rank"], "bm25_rank": p["bm25_rank"], "bm25cjk_rank": p["bm25cjk_rank"],
            "in_now6": p["in_now6"], "in_union6": p["in_union6"], "in_rrf6": p["in_rrf6"],
        })
    runs = ev[qid]["runs"]
    rows.append({
        "qid": qid, "type": q["type"], "question": question, "q_chars": len(question), "q_bigrams": len(qb),
        "q_alnum": sorted(qa),
        "gold_n": len(golds),
        "gold": per,
        "llm": {k: bool(v.get("final")) for k, v in runs.items()},
    })

json.dump(rows, open(HERE / "out/overlap_34.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# 表: 1 問 1 行（複数チャンクは平均と到達数）
print("qid\ttype\tnG\tjac(mean)\tcov(mean)\tcov(max)\talnum_hit\tvec(min)\tbm25(min)\tnow6\tunion6\trrf6\tLLM本文/参照/グラフ/両方")
for r in rows:
    g = r["gold"]
    jac = statistics.mean(x["jaccard"] for x in g)
    cov = statistics.mean(x["coverage"] for x in g)
    covmax = max(x["coverage"] for x in g)
    hits = sorted({t for x in g for t in x["alnum_hit"]})
    vec = min(x["vec_rank"] for x in g)
    bm = min((x["bm25_rank"] or 9999) for x in g)
    now6 = sum(x["in_now6"] for x in g); un6 = sum(x["in_union6"] for x in g); rr6 = sum(x["in_rrf6"] for x in g)
    llm = "".join(("O" if r["llm"][k] else "x") if k in r["llm"] else "-" for k in ("本文だけ", "参照あり", "グラフあり", "参照＋グラフ"))
    print(f"{r['qid']}\t{r['type']}\t{r['gold_n']}\t{jac:.3f}\t{cov:.3f}\t{covmax:.3f}\t{','.join(hits) or '-'}\t{vec}\t{bm}\t{now6}\t{un6}\t{rr6}\t{llm}")

# 重なりと到達の関係（チャンク単位）
print("\n== チャンク単位: coverage の帯 × in_union6 ==")
bands = [(0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.45), (0.45, 1.01)]
allg = [(r["type"], x) for r in rows for x in r["gold"]]
for lo, hi in bands:
    sel = [x for t, x in allg if lo <= x["coverage"] < hi]
    if not sel:
        continue
    print(f"coverage [{lo:.2f},{hi:.2f}): n={len(sel)} union6={sum(x['in_union6'] for x in sel)} now6={sum(x['in_now6'] for x in sel)} "
          f"vec_rank中央値={statistics.median(x['vec_rank'] for x in sel)} bm25中央値={statistics.median((x['bm25_rank'] or 612) for x in sel)}")

print("\n== チャンク単位: 英数字トークンの一致の有無 × in_union6 ==")
for label, sel in (("一致あり", [x for t, x in allg if x["alnum_hit"]]), ("一致なし", [x for t, x in allg if not x["alnum_hit"]])):
    print(f"{label}: n={len(sel)} union6={sum(x['in_union6'] for x in sel)} now6={sum(x['in_now6'] for x in sel)} "
          f"vec_rank中央値={statistics.median(x['vec_rank'] for x in sel) if sel else '-'} bm25中央値={statistics.median((x['bm25_rank'] or 612) for x in sel) if sel else '-'}")

print("\n== 型ごと: 平均 coverage と到達率 ==")
by = collections.defaultdict(list)
for t, x in allg:
    by[t].append(x)
for t, sel in by.items():
    print(f"{t}: chunks={len(sel)} cov平均={statistics.mean(x['coverage'] for x in sel):.3f} jac平均={statistics.mean(x['jaccard'] for x in sel):.3f} "
          f"union6={sum(x['in_union6'] for x in sel)}/{len(sel)} now6={sum(x['in_now6'] for x in sel)}/{len(sel)}")

# 対の問い（control）と経路の問いの差
print("\n== 対（control）と元の問いの coverage 差 ==")
cmap = {r["qid"]: r for r in rows}
for r in rows:
    if r["type"] == "control":
        base = cmap[r["qid"][:-1]]
        bc = base["gold"][0]["coverage"]; cc = r["gold"][0]["coverage"]
        print(f"{base['qid']} cov={bc:.3f} union6={int(base['gold'][0]['in_union6'])} | {r['qid']} cov={cc:.3f} union6={int(r['gold'][0]['in_union6'])} | 差={cc-bc:+.3f}")
