# 作問候補の機械判定（決定的。LLM は使わない）。
#   python3 judge.py --cands out2/cand_A.json [--cands out2/cand_B.json ...] --out out2/judge.json [--workers 3]
# 候補の形は out2/AUTHORING.md。答えの所在は gold_groups で書く。群はすべて必要で、群の中はどれか 1 チャンクに届けばよい
# （同じ成績表の複製のような「どれでもよい」チャンクを 1 群にまとめるため）。
#
# 1 問につき、全 612 チャンクをリランカーで 1 回だけ並べる。候補の取り方ごとの上位 6 件はその点数で決める。
# リランカーの点数は候補の集合に依らないので、集合ごとに並べ直すのと同じ結果になる。
# 呼ぶのは稼働中の埋め込み（:8003）とリランカー（:8006）だけ。コンテナは起動しない。
#
# 測る候補の取り方（いずれも最後はリランカーで 6 件）:
#   now6   Graph Builder の検索（いまの設定）: ベクトルと全文を各 1 位で割って最大値、上位 20、0.5 未満を落とす
#   union6 ベクトル上位 20 ∪ 全文上位 20          rrf6  各上位 20 の RRF（k=60）上位 20
#   u50_6  ベクトル上位 50 ∪ 全文上位 50（窓 50）   nb6   いまの候補に同一文書の前後 3 チャンクを足す
#   doc2_6 / doc3_6  文書→節の 2 段（RRF で文書を上位 2/3 本選び、その文書の全チャンク）
#   ref6   いまの 6 件の本文から文書番号を正規表現で拾い、その文書の全チャンクを足す（参照追跡）
#   rerank_all  全 612 チャンクでのリランカー順位（候補の取り方に依らない上限）
import argparse, collections, json, pathlib, re, sys, unicodedata
from concurrent.futures import ThreadPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import probe_q as P  # noqa: E402

DUMP = pathlib.Path("/home/uchida/ai-stack/llm-graph-builder/tools/kg/hs/runs/full56_edc_quality/graph_dump_linked.json")

CHUNKS, IDS, TEXTS, EMB = P.load()
IDX = {c: i for i, c in enumerate(IDS)}
BM_UNI = P.BM25([P.toks_uni(t) for t in TEXTS])
BM_BI = P.BM25([P.toks_bi(t) for t in TEXTS])
DOCS_OF = collections.defaultdict(list)          # 文書 -> チャンク番号（位置順）
for _i, _c in enumerate(IDS):
    DOCS_OF[CHUNKS[_c]["doc"]].append(_i)
NTEXT = [re.sub(r"\s+", "", P.norm(t)) for t in TEXTS]


def doc_key(docname):
    n = docname.replace(".pdf", "")
    m = re.match(r"(?:HS-)?(RU|TP)-(\d{3})", n)
    if m:
        return f"HS-{m.group(1)}-{m.group(2)}"
    m = re.match(r"(?:HS-)?VTR-(T|P)(\d{3})", n)
    if m:
        return f"HS-VTR-{m.group(1)}{m.group(2)}"
    m = re.match(r"(RU|TP)-(\d{3})廃止", n)
    if m:
        return f"HS-{m.group(1)}-{m.group(2)}"
    return None


KEY2DOC = collections.defaultdict(list)
for _d in DOCS_OF:
    _k = doc_key(_d)
    if _k:
        KEY2DOC[_k].append(_d)


def ids_in_text(text):
    out = set()
    t = P.norm(text).upper()
    for m in re.finditer(r"HS[-‐ ]?(RU|TP)[-‐ ]?(\d{3})", t):
        out.add(f"HS-{m.group(1)}-{m.group(2)}")
    for m in re.finditer(r"HS[-‐ ]?VTR[-‐ ]?(T|P)?[-‐ ]?(\d{3})", t):
        out.add(f"HS-VTR-{(m.group(1) or '')}{m.group(2)}")
    for m in re.finditer(r"(?<![A-Z-])VTR[-‐ ]?(T|P)(\d{3})", t):
        out.add(f"HS-VTR-{m.group(1)}{m.group(2)}")
    return out


# ---- グラフ（道の機械確認用） ----
_G = json.load(open(DUMP, encoding="utf-8"))
_ENT_NAME = {e["id"]: e["props"].get("id", e["id"]) for e in _G["entities"]}
_ENT_LABELS = {e["id"]: [l for l in e["labels"] if l != "__Entity__"] for e in _G["entities"]}
NAME2ENTS = collections.defaultdict(set)
for _eid, _n in _ENT_NAME.items():
    NAME2ENTS[unicodedata.normalize("NFKC", _n).lower().replace(" ", "")].add(_eid)
ENT_CHUNKS = collections.defaultdict(set)
for _h in _G["has_entity"]:
    if _h["chunk"] in IDX:
        ENT_CHUNKS[_h["entity"]].add(IDX[_h["chunk"]])
CHUNK_REFS = collections.defaultdict(set)        # チャンク間参照リンク（両向きに引けるように）
for _r in _G["refers_to"]:
    if _r["source"] in IDX and _r["target"] in IDX:
        CHUNK_REFS[IDX[_r["source"]]].add(("out", IDX[_r["target"]]))
        CHUNK_REFS[IDX[_r["target"]]].add(("in", IDX[_r["source"]]))
DOC_REFS = set()                                  # 文書エンティティ間の REFERS_TO（規則）
APPLIES = collections.defaultdict(list)           # 文書番号 -> APPLIES_TO の相手
for _r in _G["relationships"]:
    s, t = _ENT_NAME.get(_r["source"], ""), _ENT_NAME.get(_r["target"], "")
    if _r["type"] == "REFERS_TO" and _r.get("props", {}).get("method") == "rule":
        DOC_REFS.add((s, t))
    if _r["type"] == "APPLIES_TO":
        m = re.match(r"(HS-(?:RU|TP)-\d{3}|HS-VTR-[TP]\d{3}|VTR-T\d{3})", s)
        if m:
            APPLIES[m.group(1).replace("VTR-T", "HS-VTR-T") if m.group(1).startswith("VTR") else m.group(1)].append(
                (t, ",".join(_ENT_LABELS.get(_r["target"], []))))


def ents_by_name(name):
    return NAME2ENTS.get(unicodedata.normalize("NFKC", name).lower().replace(" ", ""), set())


# ---- 語の重なり（out/overlap_34.py と同じ定義） ----
PUNCT = re.compile(r"[\s、。，．,.・:：;；()（）\[\]「」『』|<>＜＞*＊/／\-−–—=＝~〜～≧≦±%％#]+")


def bigrams(t):
    s = PUNCT.sub("", P.norm(t))
    return set(s[i:i + 2] for i in range(len(s) - 1))


def alnum_tokens(t):
    s = P.norm(t)
    toks = set(re.findall(r"[a-z]*[0-9][a-z0-9]*(?:[.\-][a-z0-9]+)*|[a-z]{2,}[0-9][a-z0-9\-]*", s))
    toks |= set(re.findall(r"(?<![a-z0-9])[a-z]{2,}(?![a-z0-9])", s))
    drop = {"n", "mm", "mm2", "jis", "hs", "ru", "tp", "vtr", "kpa", "mpa", "hv", "hb", "hrc", "pt", "ut", "mt", "rt",
            "tps", "tpl", "rh", "at"}  # 機種の系列名は場面の語で、数百チャンクに出るので数えない
    return {x for x in toks if x not in drop and not re.fullmatch(r"[0-9]{1,2}", x)}


def overlap(q, i):
    qb, cb = bigrams(q), bigrams(TEXTS[i])
    inter = qb & cb
    qa = alnum_tokens(q)
    return {"coverage": round(len(inter) / max(1, len(qb)), 3), "jaccard": round(len(inter) / max(1, len(qb | cb)), 3),
            "alnum_hit": sorted(t for t in qa if t in P.norm(TEXTS[i]))}


# ---- 質問に書いてはいけない語 ----
BANNED = [
    ("文書番号", re.compile(r"(?:HS[-‐ ]?)?(?:RU|TP)[-‐ ]?\d{3}|(?:HS[-‐ ]?)?VTR[-‐ ]?[TP]?\d{3}|QC-KAI-\d+|HZTL\d+", re.I)),
    ("節番号", re.compile(r"\d+-\d+\s*項|(?<![\d.])\d-\d{1,2}(?![\d.])|表\s*[-‐]?\s*\d|注\s*\d|別表\s*\d|付表|第\s*\d+\s*項")),
    ("基準表記の型式", re.compile(r"[A-Z]{2,4}\s*・\s*・\s*\d|[A-Z]{2,4}\d{2,3}\s*[~〜～]\s*[A-Z]{0,4}\d{2,3}")),
]
DATE_RE = re.compile(r"\d{2,4}\s*[./年]\s*\d{1,2}\s*[./月]\s*\d{1,2}|[A-Z]\s*版|改訂\s*[A-Z]|改訂記号|版記号")


def banned_in(q, allowed):
    out = []
    for name, rx in BANNED:
        for m in rx.finditer(q):
            tok = m.group(0)
            if not any(P.norm(a).replace(" ", "") in P.norm(tok).replace(" ", "") or P.norm(tok).replace(" ", "") in P.norm(a).replace(" ", "")
                       for a in allowed):
                out.append(f"{name}:{tok}")
    return out


# ---- 引用の解決 ----
def resolve(quote):
    key = re.sub(r"\s+", "", P.norm(quote))
    if not key:
        return []
    return [i for i, t in enumerate(NTEXT) if key in t]


def label(i):
    return f"{CHUNKS[IDS[i]]['doc'].replace('.pdf', '')}#{CHUNKS[IDS[i]]['position']}"


# ---- 測定 ----
_CACHE = {}


DISK = HERE / "out2" / "measure_cache.json"
try:
    _DISK = json.load(open(DISK, encoding="utf-8"))
except Exception:
    _DISK = {}


def _restore(d):
    M = dict(d)
    M["sets"] = {k: set(v) for k, v in d["sets"].items()}
    return M


def save_disk():
    out = {}
    for q, M in _CACHE.items():
        x = dict(M); x["sets"] = {k: sorted(v) for k, v in M["sets"].items()}
        out[q] = x
    for q, d in _DISK.items():
        out.setdefault(q, d)
    json.dump(out, open(DISK, "w", encoding="utf-8"), ensure_ascii=False)


def measure(q):
    if q in _CACHE:
        return _CACHE[q]
    if q in _DISK:
        _CACHE[q] = _restore(_DISK[q])
        return _CACHE[q]
    qv = P.embed([q])[0]
    vec = [P.cos(qv, EMB[c]) for c in IDS]
    v_order, v_rank = P.ranks_from_scores(vec)
    bu = BM_UNI.scores(P.toks_uni(q)); bu_order, bu_rank = P.ranks_from_scores(bu)
    bb = BM_BI.scores(P.toks_bi(q)); _, bb_rank = P.ranks_from_scores(bb)
    rr = P.rerank(q, TEXTS)
    _, rr_rank = P.ranks_from_scores(rr)

    def top(cands, k=6):
        return sorted(cands, key=lambda i: -rr[i])[:k]

    vmax = max(vec) or 1.0; bmax = max(bu) or 1.0
    now = {i: s / vmax for i, s in enumerate(vec)}
    for i, s in enumerate(bu):
        if s > 0:
            now[i] = max(now[i], s / bmax)
    now_c = [i for i, _ in sorted(now.items(), key=lambda kv: -kv[1])[:20]]
    now_c = [i for i in now_c if now[i] >= 0.5]
    bu_hits = [i for i in bu_order if bu[i] > 0]
    union_c = list(dict.fromkeys(v_order[:20] + bu_hits[:20]))
    rrf = collections.Counter()
    for L in (v_order[:20], bu_hits[:20]):
        for r, i in enumerate(L):
            rrf[i] += 1.0 / (60 + r + 1)
    rrf_c = [i for i, _ in sorted(rrf.items(), key=lambda kv: -kv[1])[:20]]
    u50 = list(dict.fromkeys(v_order[:50] + bu_hits[:50]))
    nb = set(now_c)
    for i in now_c:
        L = DOCS_OF[CHUNKS[IDS[i]]["doc"]]; p = L.index(i)
        nb.update(L[max(0, p - 3):p + 4])
    docscore = {}
    for i, s in rrf.items():
        d = CHUNKS[IDS[i]]["doc"]; docscore[d] = max(docscore.get(d, 0.0), s)
    doc_rank = [d for d, _ in sorted(docscore.items(), key=lambda kv: -kv[1])]
    doc2 = [j for d in doc_rank[:2] for j in DOCS_OF[d]]
    doc3 = [j for d in doc_rank[:3] for j in DOCS_OF[d]]
    now6 = top(now_c)
    refs = set()
    for i in now6:
        for k in ids_in_text(TEXTS[i]):
            for d in KEY2DOC.get(k, []):
                if d != CHUNKS[IDS[i]]["doc"]:
                    refs.add(d)
    ref_c = set(now_c)
    for d in refs:
        ref_c.update(DOCS_OF[d])
    sets = {"now6": now6, "union6": top(union_c), "rrf6": top(rrf_c), "u50_6": top(u50), "u50_10": top(u50, 10),
            "nb6": top(list(nb)), "doc2_6": top(doc2), "doc3_6": top(doc3), "ref6": top(list(ref_c))}
    M = {"v_rank": v_rank, "bu": bu, "bu_rank": bu_rank, "bb": bb, "bb_rank": bb_rank, "rr": rr, "rr_rank": rr_rank,
         "sets": {k: set(v) for k, v in sets.items()}, "lists": {k: [label(i) for i in v] for k, v in sets.items() if k in ("now6", "union6", "u50_6")},
         "doc_rank": doc_rank, "refs": sorted(refs)}
    _CACHE[q] = M
    return M


METHODS = ["now6", "union6", "rrf6", "u50_6", "u50_10", "nb6", "doc2_6", "doc3_6", "ref6"]


def cm(M, i):
    d = CHUNKS[IDS[i]]["doc"]
    within = sorted(DOCS_OF[d], key=lambda j: -M["rr"][j]).index(i) + 1
    r = {"chunk": label(i), "vec": M["v_rank"][i], "bm25": M["bu_rank"][i] if M["bu"][i] > 0 else None,
         "bm25cjk": M["bb_rank"][i] if M["bb"][i] > 0 else None, "rerank_all": M["rr_rank"][i],
         "doc_rank": (M["doc_rank"].index(d) + 1) if d in M["doc_rank"] else None, "within_doc": f"{within}/{len(DOCS_OF[d])}"}
    for m in METHODS:
        r[m] = i in M["sets"][m]
    return r


def group_reach(M, groups):
    """群ごとに、どの取り方で届いたか（群の中のどれか 1 チャンク）。"""
    out = {m: sum(1 for g in groups if any(i in M["sets"][m] for i in g["idx"])) for m in METHODS}
    out["n"] = len(groups)
    return out


def best_chunk(M, g):
    return min(g["idx"], key=lambda i: M["rr_rank"][i])


BIG = 10 ** 6


def nz(x):
    return BIG if x is None else x


CAT_JA = {"lookup": "単純照会", "vocab_gap": "語彙の不一致", "applies": "暗黙の適用関係", "ref_hop": "明示参照の多段",
          "entity_hop": "実体を介した多段", "confusable": "取り違え", "table_cond": "表の条件読み", "negation": "否定・不許可",
          "version": "版・廃止・時間", "unanswerable": "答えが無い", "crosscheck": "照合・比較"}
SEARCH_SIDE = {"vocab_gap", "applies", "ref_hop", "entity_hop"}
NEG_RE = re.compile(r"一切|認めない|認めず|不可|禁止|してはならない|許容しない|許可しない|認められない|行わない|しないこと|行なわない")
COND_RE = re.compile(r"但し|ただし|注\s*\d|注:|注：|参考|の場合|に限る|限る|を除く|適用しない|以外")
DELEG_RE = re.compile(r"JIS|ASTM|HZTL|QC-KAI|HS-VTR-037|図面|指示書|別途|による|に依る|に基づ|承認図書|社内規格|社内管理|要領書|要領図|分布図|仕様書|ABB|協議"
                      r"|(?:VTR|HS|基準|規格|要領|仕様|寸法)[^。]{0,20}?に\s*(?:よ|依|基づ)")
ADMIN_RE = re.compile(r"来歴|協議|通知|目録")


def judge(it):
    cat = it["category"]
    R, D, tags, info = [], [], [], {}
    q = it["question"]
    allowed = list(it.get("printed_terms") or [])
    # 引用の解決
    groups = []
    for g in it.get("gold_groups") or []:
        idx = []
        for qt in g.get("quotes", []):
            hit = resolve(qt)
            if not hit:
                R.append(f"引用が見つからない（gold）: {qt[:40]}")
            idx += hit
        idx += [IDX[c] for c in g.get("ids", []) if c in IDX]
        idx = list(dict.fromkeys(idx))
        if idx:
            docs = sorted({CHUNKS[IDS[i]]["doc"] for i in idx})
            if len(docs) > 1 and not g.get("any_doc"):
                tags.append(f"群が複数文書に当たる: {g.get('name', '')} → {','.join(d.replace('.pdf', '') for d in docs)}")
            groups.append({"name": g.get("name", ""), "idx": idx})
    if not groups:
        return {"verdict": "fail", "reasons": R + ["答えのチャンクが特定できない"], "difficulty": [], "reach_full": {}, "route_needed": None, "tags": tags, "info": info}
    entry = []
    for qt in ([it["entry_quote"]] if it.get("entry_quote") else []):
        entry = resolve(qt)
        if not entry:
            R.append(f"引用が見つからない（entry）: {qt[:40]}")
    traps = []
    for qt in it.get("trap_quotes") or []:
        h = resolve(qt)
        if not h:
            R.append(f"引用が見つからない（trap）: {qt[:40]}")
        traps += h
    if any(r.startswith("引用が見つからない") for r in R):
        return {"verdict": "fail", "reasons": R, "difficulty": [], "reach_full": {}, "route_needed": None, "tags": tags, "info": info}

    M = measure(q)
    gold_docs = sorted({CHUNKS[IDS[i]]["doc"] for g in groups for i in g["idx"]})
    info["gold"] = [{"name": g["name"], "alts": [label(i) for i in g["idx"]], **cm(M, best_chunk(M, g)),
                     **overlap(q, best_chunk(M, g))} for g in groups]
    info["reach"] = group_reach(M, groups)
    info["n_groups"], info["n_docs"] = len(groups), len(gold_docs)
    info["union6"], info["now6"] = M["lists"]["union6"], M["lists"]["now6"]
    if entry:
        info["entry"] = [cm(M, i) for i in entry]
    if traps:
        info["traps"] = [{"chunk": label(i), "vec": M["v_rank"][i], "union6": i in M["sets"]["union6"], "now6": i in M["sets"]["now6"]} for i in traps]
    bad = banned_in(q, allowed)
    if bad:
        info["banned"] = bad
    # 対照
    tw = None
    if it.get("twin_question"):
        TM = measure(it["twin_question"])
        tw = group_reach(TM, groups)
        info["twin_reach"] = tw
        info["twin_gold"] = [{"vec": TM["v_rank"][best_chunk(TM, g)], "union6": any(i in TM["sets"]["union6"] for i in g["idx"])} for g in groups]
    elif cat != "lookup":
        R.append("対照の問い（twin_question）が無い")
    # 変種（答え側の語を 1 つ足した形）
    if it.get("variant_question"):
        VM = measure(it["variant_question"])
        vr = group_reach(VM, groups)
        info["variant_reach"] = vr
        if vr["union6"] == vr["n"]:
            tags.append("1語差（答え側の語 1 つで和集合に届く）")

    n = len(groups)
    reach = info["reach"]
    golds = info["gold"]
    all_u6 = reach["union6"] == n
    cover_max = max(g["coverage"] for g in golds)
    alnum_any = any(g["alnum_hit"] for g in golds)

    if cat == "lookup":
        if n > 2:
            R.append(f"答えの群が {n}（単純照会は 2 以内）")
        if not all(g["now6"] or g["union6"] for g in golds):
            D.append("いまの設定でも和集合でも届かない群がある（単純照会ではない）")
        elif max(min(nz(g["vec"]), nz(g["bm25"])) for g in golds) > 6:
            tags.append("窓の境目（min(vec,bm25) が 6 を超える群がある）")
    elif cat == "vocab_gap":
        phr = it.get("phrasings") or []
        if len(phr) < 3:
            R.append("言い回しが 3 通りない")
        else:
            rows = []
            for p in phr:
                PM = measure(p["question"])
                g = groups[0]; i = best_chunk(PM, g)
                c = cm(PM, i); c.update(overlap(p["question"], i)); c["label"] = p.get("label", ""); c["banned"] = banned_in(p["question"], allowed)
                rows.append(c)
            info["phrasings"] = rows
            med = sorted(rows, key=lambda c: min(nz(c["vec"]), nz(c["bm25"])))[1]
            info["median_phrasing"] = med["label"]
            # 外れ方を記録する: 候補 20 件に入らない（候補で外れる）か、候補には入るがリランカーが 6 件に残さない（リランカーで外れる）か
            tags.append("中央値の言い回しは " + ("候補で外れる" if (med["vec"] > 20 and nz(med["bm25"]) > 20) else "リランカーで外れる")
                        + f"（vec {med['vec']}・bm25 {med['bm25']}・全件リランク {med['rerank_all']}）")
            reached = [r["label"] for r in rows if r["union6"]]
            if reached:
                tags.append("和集合に届く言い回し: " + ",".join(reached))
            if med["union6"] or med["rrf6"]:
                D.append("中央値の言い回しで和集合か RRF に届く")
            if med["u50_6"]:
                D.append("中央値の言い回しで窓 50 に届く（top-K を増やせば届く）")
            if med["rerank_all"] > 50:
                tags.append(f"リランカーの失敗（全件リランク {med['rerank_all']} 位）")
            if any(r["banned"] for r in rows):
                R.append("言い回しに書いてはいけない語: " + ";".join(sum((r["banned"] for r in rows), [])))
        if n > 2:
            R.append(f"答えの群が {n}（2 以内）")
        if tw and not (tw["union6"] == n and all(t["vec"] <= 10 for t in info["twin_gold"])):
            D.append("対照の問いが和集合で届かないか vec が 10 を超える")
        if alnum_any:
            R.append("質問の英数字が答えのチャンクと一致: " + ",".join(sum((g["alnum_hit"] for g in golds), [])))
        if cover_max >= 0.30:
            R.append(f"語の重なり {cover_max}（0.30 未満が条件）")
    elif cat == "applies":
        if not entry:
            R.append("起点（適用の節）が無い")
        else:
            if not any(e["vec"] <= 20 or nz(e["bm25"]) <= 20 for e in info["entry"]):
                D.append("起点が vec・bm25 とも 21 位以下")
            edocs = {CHUNKS[IDS[i]]["doc"] for i in entry}
            if not set(gold_docs) <= edocs:
                R.append("答えが起点と別の文書（適用関係は同一文書内）")
        for g in golds:
            if g["union6"] or g["rrf6"]:
                D.append(f"答え {g['chunk']} が和集合か RRF に届く")
            if g["u50_6"]:
                D.append(f"答え {g['chunk']} が窓 50 で届く")
            if g["coverage"] >= 0.30:
                R.append(f"答え {g['chunk']} の語の重なり {g['coverage']}")
        info["baseline_doc2_or_nb"] = [g["doc2_6"] or g["nb6"] for g in golds]
        k = doc_key(gold_docs[0]) if gold_docs else None
        info["applies_to"] = APPLIES.get(k, [])[:8] if k else []
        if not info["applies_to"]:
            tags.append("抽出の穴（文書番号から APPLIES_TO が無い）")
    elif cat == "ref_hop":
        if not entry:
            R.append("起点（参照の一文）が無い")
        else:
            if not any(e["union6"] or e["vec"] <= 20 for e in info["entry"]):
                D.append("起点が和集合に入らず vec も 21 位以下")
            edocs = {CHUNKS[IDS[i]]["doc"] for i in entry}
            if set(gold_docs) & edocs:
                R.append("答えが起点と同じ文書にある")
            ek = {doc_key(d) for d in edocs}; gk = {doc_key(d) for d in gold_docs}
            doc_link = any((a, b) in DOC_REFS or (a and b and any(s.startswith(a) and t.startswith(b) for s, t in DOC_REFS)) for a in ek for b in gk)
            chunk_link = any(("out", j) in CHUNK_REFS[i] for i in entry for g in groups for j in g["idx"])
            info["route"] = {"doc_refers_to": doc_link, "chunk_refers_to": chunk_link}
            if not (doc_link or chunk_link):
                tags.append("規則の参照リンクが無い（文書間・チャンク間とも）")
        for g in golds:
            if not (g["vec"] > 20 and nz(g["bm25"]) > 20):
                D.append(f"答え {g['chunk']} が vec {g['vec']}・bm25 {g['bm25']}（どちらも 21 位以下が条件）")
            if g["union6"]:
                D.append(f"答え {g['chunk']} が和集合に届く")
            if g["u50_6"]:
                D.append(f"答え {g['chunk']} が窓 50 で届く")
        if not R and not all(g["ref6"] for g in golds):
            tags.append("節を外す型（参照追跡でも 6 件に入らない）")
    elif cat == "entity_hop":
        if alnum_any:
            R.append("質問の英数字が答えのチャンクと一致: " + ",".join(sum((g["alnum_hit"] for g in golds), [])))
        sub = it.get("subtype", "")
        if sub == "made_of":
            if reach["union6"] or reach["rrf6"]:
                D.append("単一答えが和集合か RRF に届く")
        else:
            if not (2 <= n <= 4):
                R.append(f"答えの群が {n}（2〜4）")
            if len(gold_docs) < 2:
                R.append("答えの文書が 1 本")
            if reach["union6"] >= n:
                D.append("和集合で全群が届く")
            if reach["u50_6"] >= n:
                D.append("窓 50 で全群が届く")
        ents = it.get("route_entities") or []
        found, ends = [], 0
        for name in ents:
            es = ents_by_name(name)
            if es:
                found.append(name)
        for g in groups:
            hit = any(i in ENT_CHUNKS[e] for name in ents for e in ents_by_name(name) for i in g["idx"])
            hit = hit or any(("in", j) in CHUNK_REFS[i] or ("out", j) in CHUNK_REFS[i] for i in g["idx"] for j in entry)
            ends += int(hit)
        info["route"] = {"entities_found": found, "entities_missing": [x for x in ents if x not in found], "groups_reached_by_route": ends}
        if ends < n:
            tags.append(f"道がチャンクで終わらない群がある（{ends}/{n}）")
    elif cat == "confusable":
        if not all_u6:
            D.append("答えが和集合に届かない（取り違えは検索が届く前提）")
        if not traps:
            R.append("罠（trap_quotes）が無い")
        elif not any(t["union6"] or t["vec"] <= 10 for t in info["traps"]):
            R.append("罠が和集合に同席せず vec も 11 位以下")
    elif cat == "table_cond":
        if not all_u6:
            D.append("答えが和集合に届かない")
        if not any(COND_RE.search(P.norm(TEXTS[i])) or TEXTS[i].count("|") >= 6 for g in groups for i in g["idx"]):
            R.append("答えのチャンクに条件語も表も無い")
    elif cat == "negation":
        if not all_u6:
            D.append("答えが和集合に届かない")
        if not any(NEG_RE.search(TEXTS[i]) for g in groups for i in g["idx"]):
            R.append("答えのチャンクに否定語が無い")
    elif cat == "version":
        if not any(ADMIN_RE.search(CHUNKS[IDS[i]]["section"] + TEXTS[i][:80]) for g in groups for i in g["idx"]):
            R.append("来歴・協議・通知票・発行目録のチャンクが無い")
        secs = {CHUNKS[IDS[best_chunk(M, g)]]["section"] for g in groups}
        if n < 2 or (len(gold_docs) < 2 and len(secs) < 2):
            R.append("突き合わせる 2 か所が無い")
        if reach["now6"] == n:
            D.append("いまの設定で全群が届く")
        dm = [m.group(0) for m in DATE_RE.finditer(q)]
        dm = [x for x in dm if not any(P.norm(a) in P.norm(x) or P.norm(x) in P.norm(a) for a in allowed)]
        if dm:
            R.append("質問に版記号・日付: " + ",".join(dm))
    elif cat == "unanswerable":
        if it.get("subtype") != "false_premise" and not any(DELEG_RE.search(TEXTS[i]) for g in groups for i in g["idx"]):
            R.append("答えのチャンクに委ねる一文が無い")
        keys = [k for k in it.get("answer_keys", []) if k not in ("無い", "ない", "定めが無い", "基準に無い")]
        if not any(re.sub(r"\s+", "", P.norm(k)) in NTEXT[i] for k in keys for g in groups for i in g["idx"]):
            tags.append("採点の鍵に委ね先の名前が無い（本文に一致しない）")
    elif cat == "crosscheck":
        if n < 2 or len(gold_docs) < 2:
            R.append("2 文書以上の突き合わせでない")
        derived = [k for k in it.get("answer_keys", []) if not any(re.sub(r"\s+", "", P.norm(k)) in NTEXT[i] for g in groups for i in g["idx"])]
        info["derived_keys"] = derived
        if reach["union6"] == n and not derived:
            D.append("和集合で全群が届き、導出値も無い")
    else:
        R.append(f"未知の分類 {cat}")

    if bad and cat not in ("lookup",):
        R.append("質問に書いてはいけない語: " + ",".join(bad))
    elif bad:
        tags.append("質問に文書番号など: " + ",".join(bad))
    if cat in SEARCH_SIDE and not R:
        for g in golds:
            if g["rerank_all"] <= 6:
                tags.append(f"全件リランクなら届く（{g['chunk']} {g['rerank_all']} 位）")
    border = [g for g in golds if g["bm25"] and 15 <= g["bm25"] <= 25]
    if border:
        tags.append("全文検索の順位が境目（15〜25）。Graph Builder の全文索引で確定が要る")
    full = {m: reach[m] == n for m in METHODS}
    full["rerank_all6"] = all(g["rerank_all"] <= 6 for g in golds)
    route_needed = not (full["union6"] or full["rrf6"] or full["u50_6"])
    verdict = "fail" if R else "pass"
    return {"verdict": verdict, "reasons": R, "difficulty": D, "reach_full": full, "route_needed": route_needed,
            "tags": tags, "info": info}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cands", action="append", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    items = []
    for f in a.cands:
        items += json.load(open(f, encoding="utf-8"))
    # 先に問いを全部測ってキャッシュに入れる（並列）
    qs = []
    for it in items:
        qs += [it["question"]] + ([it["twin_question"]] if it.get("twin_question") else []) + \
              ([it["variant_question"]] if it.get("variant_question") else []) + [p["question"] for p in it.get("phrasings") or []]
    qs = list(dict.fromkeys(qs))
    with ThreadPoolExecutor(a.workers) as ex:
        for k, _ in enumerate(ex.map(measure, qs)):
            if (k + 1) % 20 == 0:
                print(f"measured {k + 1}/{len(qs)}", flush=True)
    save_disk()
    out = []
    for it in items:
        r = judge(it)
        out.append({"qid": it["qid"], "category": it["category"], "question": it["question"], **r})
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    by = collections.defaultdict(collections.Counter)
    for r in out:
        by[r["category"]][r["verdict"]] += 1
        if r["verdict"] == "pass":
            for m in ("now6", "union6", "u50_6"):
                by[r["category"]][m] += int(bool(r["reach_full"].get(m)))
            by[r["category"]]["route"] += int(bool(r["route_needed"]))
    print("分類          成立 不成立 | 成立のうち全群が届く: いまの設定 和集合 窓50 | 窓50でも届かない")
    for c, v in sorted(by.items()):
        print(f"{c:13s} {v['pass']:4d} {v['fail']:5d} | {v['now6']:20d} {v['union6']:6d} {v['u50_6']:4d} | {v['route']:4d}")


if __name__ == "__main__":
    main()
