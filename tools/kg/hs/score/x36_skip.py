# X36: 行が抽出に使われるかどうかは、回によって変わるのか（揺れ）、チャンクの混み具合で変わるのか。
import json, re, sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import norm2, load_dump
N = lambda s: re.sub(r"[\s・･.…|:：;；,，、。()（）\[\]「」『』\-ー—~〜→]+", "", norm2(s or ""))
rows = json.load(open("score_out/x31/x36_rows.json", encoding="utf-8"))
golds = {}
for gp in ["gold_add/gold_add46.json", "gold_add/gold_hs4_full.json"]:
    for e in json.load(open(gp, encoding="utf-8"))["entities"]: golds.setdefault(e["id"], set()).update([e["id"]] + e.get("aliases", []))
def touched_lines(path):
    D, E, CH, EC, CE = load_dump(path)
    rbc = collections.defaultdict(list)
    for r in D["relationships"]:
        c = (r.get("props") or {}).get("source_chunk_id")
        for c in ([c] if c else list(EC.get(r["source"], set()) & EC.get(r["target"], set()))): rbc[c].append(r)
    out = {}; nrel = {}
    for cid, c in CH.items():
        lines = (c.get("text") or "").split("\n"); nl = [N(l) for l in lines]
        names = {e: N(E[e]["props"].get("id")) for e in CE.get(cid, set())}; names = {e: n for e, n in names.items() if len(n) >= 2}
        inl = [set(e for e, n in names.items() if n in l) for l in nl]; cnt = collections.Counter(e for s in inl for e in s)
        t = [False] * len(lines)
        for r in rbc.get(cid, []):
            s, o = r["source"], r["target"]
            for i, es in enumerate(inl):
                if (s in es and o in es) or (s in es and cnt[s] == 1) or (o in es and cnt[o] == 1): t[i] = True
        out[cid] = (lines, nl, t); nrel[cid] = len(rbc.get(cid, []))
    return out, nrel
A, nA = touched_lines("runs/prod_full56_1113/graph_dump.json")
B, nB = touched_lines("runs/full56_regions_text/graph_dump_linked.json")
lines = {}   # (chunk, line) -> 正解の本数
for r in rows:
    if not r["chunk"] or r["chunk"] not in A: continue
    ls, nl, t = A[r["chunk"]]
    okeys = [N(k) for k in golds.get(r["o"], [r["o"]]) if len(N(k)) >= 2]
    ol = [i for i, l in enumerate(nl) if any(k in l for k in okeys)]
    if ol: lines[(r["chunk"], ol[0])] = lines.get((r["chunk"], ol[0]), 0) + 1
both = [(k, A[k[0]][2][k[1]], B[k[0]][2][k[1]]) for k in lines if k[0] in B and len(B[k[0]][0]) == len(A[k[0]][0])]
c = collections.Counter((a, b) for _, a, b in both)
print("正解のある行", len(lines), "2 回で比べられる行", len(both))
print("  本番で使われた・11/9 でも使われた", c[(True, True)], " 本番だけ", c[(True, False)], " 11/9 だけ", c[(False, True)], " どちらも使われない", c[(False, False)])
sk = c[(False, True)] + c[(False, False)]; print("  本番で使われない行", sk, "のうち、11/9 の回では使われた", c[(False, True)], f"({c[(False, True)]/sk:.0%})")
# チャンクの混み具合
per_chunk = collections.defaultdict(lambda: [0, 0])
for (cid, i), n in lines.items():
    per_chunk[cid][0] += 1; per_chunk[cid][1] += A[cid][2][i]
def b(v, edges): return next((f"{lo}〜{hi}" for lo, hi in edges if lo <= v <= hi), None)
t1 = collections.defaultdict(lambda: [0, 0]); t2 = collections.defaultdict(lambda: [0, 0]); t3 = collections.defaultdict(lambda: [0, 0])
for cid, (n, u) in per_chunk.items():
    k = b(n, [(1, 3), (4, 6), (7, 10), (11, 99)]); t1[k][0] += n; t1[k][1] += u
    k = b(len(A[cid][0][0] and "\n".join(A[cid][0])), [(0, 300), (301, 600), (601, 1000), (1001, 99999)]); t2[k][0] += n; t2[k][1] += u
    k = b(nA[cid], [(0, 5), (6, 10), (11, 15), (16, 999)]); t3[k][0] += n; t3[k][1] += u
for name, t, order in [("チャンクの中の、正解のある行の数", t1, ["1〜3", "4〜6", "7〜10", "11〜99"]), ("チャンクの文字数", t2, ["0〜300", "301〜600", "601〜1000", "1001〜99999"]), ("そのチャンクから抽出されたリレーションの数", t3, ["0〜5", "6〜10", "11〜15", "16〜999"])]:
    print("\n##", name, "→ 行が抽出に使われた割合")
    for k in order:
        if k in t: print(f"  {k}: {t[k][1]}/{t[k][0]} = {t[k][1]/t[k][0]:.0%}")
import statistics
print("\nチャンクあたりの抽出リレーション数: 中央値", statistics.median(nA.values()), "最大", max(nA.values()), " 正解のある行の数: 中央値", statistics.median(v[0] for v in per_chunk.values()), "最大", max(v[0] for v in per_chunk.values()))

# ---- 2 回とも使われない行と、2 回とも使われた行の中身 ----
import random
def line_kind(l):
    s = l.strip()
    if s.startswith("[") and "]" in s[:120]: return "見出しの経路の行"
    if s.startswith("図中の文字"): return "図中の文字"
    if s.startswith("#"): return "見出し"
    if s.count("|") >= 2: return "表の行"
    if re.match(r"^(注|※|\(注|（注|但し|ただし|なお)", s): return "注記・但し書き"
    if re.match(r"^([0-9０-９]+[\).）]|[\(（][0-9０-９a-zａ-ｚア-ン]+[\)）]|[①-⑳]|[a-zａ-ｚ][\)）]|[・\-－])", s): return "箇条書き"
    return "地の文"
grp = {"2 回とも使われない": [], "2 回とも使われた": []}
for k, a, b_ in both:
    if not a and not b_: grp["2 回とも使われない"].append(k)
    if a and b_: grp["2 回とも使われた"].append(k)
PAT = [("条件（場合・時・とき）", r"場合|とき|時は|際は|際に"), ("数値を含む", r"[0-9０-９]"), ("提出・記録・成績", r"提出|記録|成績|報告|保管"), ("表示・刻印・マーク", r"表示|刻印|マーク|鋳出|打刻"),
       ("禁止・許容（ならない・認め・許容）", r"ならない|なきこと|無きこと|認め|許容|不可|禁止"), ("参照（による・参照・準ずる）", r"による|参照|準ずる|に従"), ("括弧書きを含む", r"[（(].+[)）]"), ("図・表への言及", r"図[-－0-9０-９ 中]|表[-－0-9０-９ ]|別紙|添付")]
print("\n## 行の種類と中身（2 回とも使われない 対 2 回とも使われた）")
for name, ks in grp.items():
    ls = [A[c][0][i] for c, i in ks]; n = len(ls)
    kc = collections.Counter(line_kind(l) for l in ls)
    print(f"\n{name} {n} 行  長さの中央値 {statistics.median(len(l.strip()) for l in ls):.0f} 字")
    print("  種類:", {k: f"{v}（{v/n:.0%}）" for k, v in kc.most_common()})
    print("  中身:", {p: f"{sum(1 for l in ls if re.search(rx, l))/n:.0%}" for p, rx in PAT})
random.seed(7)
ex = random.sample(grp["2 回とも使われない"], 45)
print("\n## 2 回とも使われない行の例")
for c, i in ex: print("  [%s] %s" % (line_kind(A[c][0][i]), A[c][0][i].strip()[:110]))
