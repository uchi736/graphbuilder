# 知識グラフの有効性の評価。同じ問い・同じ LLM・同じ採点で、候補の取り方だけを変えて答えさせる。
#   python3 run_kg_eval.py --set ../out2/kg_eval_v1.json --out out/run1 --conds llm_only,B,N,G1 --runs 1 --temperature 0
#   G0（Graph Builder の graph_vector_fulltext）は --conds に G0 を足す。backend（:8440）と評価用 Neo4j（database4）が要る。
#
# 条件:
#   llm_only  文脈なし。一般知識の混入を分離する基準線（BRINK・CRAG・Han ら）
#   B         ベクトル上位 20 ∪ 全文上位 20 → リランカー → 上位 6 件。グラフなしの基準線
#   N         B に、別名表で展開した問いの候補、問いに出る文書番号の逆引き（その文書の全チャンクと、番号に言及するチャンク）、
#             文書→節の 2 段（RRF で文書上位 2 本の全チャンク）を足す → リランカー → 6 件。グラフと同じ情報を非グラフで持つ対の実装
#   G1        B に、問いに現れるエンティティ（別名表で展開）の HAS_ENTITY チャンク、その 1 ホップ隣のエンティティのチャンク、
#             B 上位 6 件のチャンク間参照リンク（両向き）、上位 6 件の文書を引いている文書エンティティ（REFERS_TO 規則）の出どころチャンク
#             を足す → リランカー → 6 件。グラフを「検索器」として使い、文脈はチャンク本文だけ（HippoRAG 系・LinearRAG の考え方）
#   G0        Graph Builder の検索（いまの設定）の graph_vector_fulltext。グラフの行を文脈に足す「知識の担体」型
#
# 生成: 常駐の gemma4（:8000、OpenAI 互換）。温度は Graph Builder と同じ 0 を既定にする。
# 採点（機械）: 鍵の照合（表記の正規化つき）、文書番号の再現率と誤混入、罠の値の混入、棄権の検出、文脈のトークン数。
#   極性・条件の判定は別途（含意判定）。生の回答と文脈を全部保存する。
import argparse, collections, json, pathlib, re, sys, time, unicodedata, urllib.parse, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import probe_q as P  # noqa: E402

DUMP = pathlib.Path("/home/uchida/ai-stack/llm-graph-builder/tools/kg/hs/runs/full56_edc_quality/graph_dump_linked.json")
LLM_URL = "http://localhost:8000/v1/chat/completions"
LLM_MODEL = "google/gemma-4-26B-A4B-it"
BACKEND = "http://localhost:8440"
CRED = {"uri": "neo4j://database4:7687", "userName": "neo4j", "password": "kgb-please-change", "database": "neo4j", "email": "test@example.com"}
CHAR_BUDGET = 14000
TOP_N = 6

SYSTEM = """あなたは社内規格の問い合わせに答える担当者である。与えられた文脈（規格の本文の抜粋）だけに基づいて日本語で答える。
- 答えは簡潔に。値は単位と条件を付け、出典として文書番号と節を書く。
- 複数の文書に書かれていれば、それぞれの値と文書を並べる。食い違いがあればそのまま指摘する。
- 文脈に根拠が無いことは「文脈の規格には記載がない」と明記し、推測で補わない。
- 文脈が与えられていない場合は、その旨を述べたうえで一般知識として知っていることがあれば答えてよい。"""


def norm(t):
    t = unicodedata.normalize("NFKC", t or "")
    t = t.replace("〜", "~").replace("～", "~").replace("―", "-").replace("‐", "-").replace("–", "-").replace("—", "-")
    t = t.replace("℃", "°C").replace("゚", "°").replace("ヶ", "か").replace("ケ月", "か月").replace("箇", "か").replace("ｶ", "か")
    t = re.sub(r"[\s・,、，.．]", "", t.lower())
    return t


# ---------- 材料 ----------
CHUNKS, IDS, TEXTS, EMB = P.load()
IDX = {c: i for i, c in enumerate(IDS)}
BM = P.BM25([P.toks_uni(t) for t in TEXTS])
NTEXT = [norm(t) for t in TEXTS]
DOCS_OF = collections.defaultdict(list)
for i, c in enumerate(IDS):
    DOCS_OF[CHUNKS[c]["doc"]].append(i)
ALIAS = json.load(open(HERE / "alias.json", encoding="utf-8"))["groups"]
G = json.load(open(DUMP, encoding="utf-8"))
ENAME = {e["id"]: e["props"].get("id", e["id"]) for e in G["entities"]}
ENORM = {eid: norm(n) for eid, n in ENAME.items()}
ENT_CHUNKS = collections.defaultdict(set)
for h in G["has_entity"]:
    if h["chunk"] in IDX:
        ENT_CHUNKS[h["entity"]].add(IDX[h["chunk"]])
ENT_DOCS = {eid: {CHUNKS[IDS[i]]["doc"] for i in ch} for eid, ch in ENT_CHUNKS.items()}
NEIGH = collections.defaultdict(set)
DOC_REF_IN = collections.defaultdict(set)      # 文書キー -> それを引いている文書エンティティ id
for r in G["relationships"]:
    NEIGH[r["source"]].add(r["target"]); NEIGH[r["target"]].add(r["source"])
    if r["type"] == "REFERS_TO" and r.get("props", {}).get("method") == "rule":
        DOC_REF_IN[ENAME.get(r["target"], "")].add(r["source"])
CHUNK_REF = collections.defaultdict(set)
for r in G["refers_to"]:
    if r["source"] in IDX and r["target"] in IDX:
        CHUNK_REF[IDX[r["source"]]].add(IDX[r["target"]]); CHUNK_REF[IDX[r["target"]]].add(IDX[r["source"]])


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
for d in DOCS_OF:
    k = doc_key(d)
    if k:
        KEY2DOC[k].append(d)


def ids_in_text(text):
    out = set()
    t = unicodedata.normalize("NFKC", text or "").upper()
    for m in re.finditer(r"HS[-‐ ]?(RU|TP)[-‐ ]?(\d{3})", t):
        out.add(f"HS-{m.group(1)}-{m.group(2)}")
    for m in re.finditer(r"(?<![A-Z-])(RU|TP)-(\d{3})(?!\d)", t):
        out.add(f"HS-{m.group(1)}-{m.group(2)}")
    for m in re.finditer(r"(?:HS[-‐ ]?)?VTR[-‐ ]?(T|P)[-‐ ]?(\d{3})", t):
        out.add(f"HS-VTR-{m.group(1)}{m.group(2)}")
    return out


MENTION_IDX = collections.defaultdict(set)   # 文書キー -> その番号に言及するチャンク（自文書を除く）
for i, t in enumerate(TEXTS):
    for k in ids_in_text(t[:2000]):
        if doc_key(CHUNKS[IDS[i]]["doc"]) != k:
            MENTION_IDX[k].add(i)


def alias_hits(q):
    nq = norm(q)
    groups = []
    for grp in ALIAS:
        hit = [a for a in grp if norm(a) and norm(a) in nq]
        if hit:
            groups.append((hit, [a for a in grp if a not in hit]))
    return groups


# ---------- 候補の取り方 ----------
_Q = {}


def base_lists(q):
    if q in _Q:
        return _Q[q]
    qv = P.embed([q])[0]
    vec = [P.cos(qv, EMB[c]) for c in IDS]
    v_order, _ = P.ranks_from_scores(vec)
    bu = BM.scores(P.toks_uni(q)); bu_order, _ = P.ranks_from_scores(bu)
    bu_hits = [i for i in bu_order if bu[i] > 0]
    _Q[q] = (v_order, bu_hits)
    return _Q[q]


def cand_B(q):
    v, b = base_lists(q)
    return list(dict.fromkeys(v[:20] + b[:20])), {}


def cand_N(q):
    c, info = cand_B(q)
    cands = set(c); struct = set()
    exp = []
    for hit, others in alias_hits(q):
        if others:
            q2 = q + " " + " ".join(others[:6])
            exp.append(q2)
            v, b = base_lists(q2)
            cands.update(v[:20] + b[:20])
    info["expanded_queries"] = exp
    keys = ids_in_text(q)
    for k in keys:
        for d in KEY2DOC.get(k, []):
            struct.update(DOCS_OF[d])
        struct.update(MENTION_IDX.get(k, set()))
    info["doc_keys_in_question"] = sorted(keys)
    v, b = base_lists(q)
    rrf = collections.Counter()
    for L in (v[:20], b[:20]):
        for r, i in enumerate(L):
            rrf[i] += 1.0 / (60 + r + 1)
    docscore = {}
    for i, s in rrf.items():
        d = CHUNKS[IDS[i]]["doc"]; docscore[d] = max(docscore.get(d, 0), s)
    top_docs = [d for d, _ in sorted(docscore.items(), key=lambda kv: -kv[1])[:2]]
    for d in top_docs:
        struct.update(DOCS_OF[d])
    info["two_stage_docs"] = [d.replace(".pdf", "") for d in top_docs]
    cands.update(struct); info["struct"] = sorted(struct)
    return list(cands), info


def match_entities(q):
    nq = norm(q)
    terms = set()
    for hit, others in alias_hits(q):
        terms.update(norm(a) for a in hit + others)
    found = set()
    for eid, en in ENORM.items():
        if len(en) < 3 and not re.search(r"[a-z0-9]", en):
            continue
        if en in nq or any(t and t in en for t in terms if len(t) >= 2):
            if len(ENT_DOCS.get(eid, ())) > 12 and en not in nq:
                continue  # 別名経由のハブは除く（外観検査など）
            found.add(eid)
    return found


def cand_G1(q):
    c, info = cand_B(q)
    cands = set(c)
    ents = match_entities(q)
    info["entities_matched"] = sorted(ENAME[e] for e in ents)[:40]
    added_e = set(); added_n = set()
    for e in ents:
        chs = ENT_CHUNKS.get(e, set())
        added_e.update(list(chs)[:40])
        for nb in list(NEIGH.get(e, ()))[:60]:
            added_n.update(list(ENT_CHUNKS.get(nb, set()))[:5])
    cands.update(added_e); cands.update(list(added_n)[:120])
    # 上位 6 件からの参照リンク（両向き）と、その文書を引く文書エンティティの出どころチャンク
    sc = P.rerank(q, [TEXTS[i] for i in c])
    top6 = [c[j] for j in sorted(range(len(c)), key=lambda j: -sc[j])[:TOP_N]]
    refs = set()
    for i in top6:
        refs.update(CHUNK_REF.get(i, set()))
        k = doc_key(CHUNKS[IDS[i]]["doc"])
        for src in DOC_REF_IN.get(k, ()):
            refs.update(list(ENT_CHUNKS.get(src, set()))[:10])
    cands.update(refs)
    info["added"] = {"entity_chunks": len(added_e), "neighbor_chunks": min(len(added_n), 120), "ref_chunks": len(refs)}
    info["struct"] = sorted((added_e | refs) - set(c))
    return list(cands), info


RESERVE = 2


def select(q, cands, k=TOP_N, struct=None, reserve=0):
    sc = P.rerank(q, [TEXTS[i] for i in cands])
    order = [cands[j] for j in sorted(range(len(cands)), key=lambda j: -sc[j])]
    rank = {c: r + 1 for r, c in enumerate(order)}
    if struct and reserve:
        s_order = [c for c in order if c in struct]
        head = order[:k - reserve]
        extra = [c for c in s_order if c not in head][:reserve]
        sel = head + extra
        rest = [c for c in order if c not in sel]
        sel = (sel + rest)[:k]
    else:
        sel = order[:k]
    return sel, order[:10], rank


def build_context(sel):
    parts, used = [], 0
    for i in sel:
        c = CHUNKS[IDS[i]]
        head = f"[{c['doc'].replace('.pdf', '')} > {c['section']}]"
        body = c["text"].strip()
        if body.startswith("[") and "]" in body[:200]:
            body = body[body.index("]") + 1:].strip()
        block = f"{head}\n{body}"
        if used + len(block) > CHAR_BUDGET:
            block = block[:max(0, CHAR_BUDGET - used)]
        parts.append(block); used += len(block)
        if used >= CHAR_BUDGET:
            break
    return "\n\n".join(parts)


# ---------- 生成 ----------
def llm(question, context, temperature):
    if context is None:
        user = f"（文脈は与えられていない）\n\n質問: {question}"
    else:
        user = f"### 文脈\n{context}\n\n### 質問\n{question}"
    body = {"model": LLM_MODEL, "temperature": temperature, "max_tokens": 700,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
    req = urllib.request.Request(LLM_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=600))
            ch = r["choices"][0]["message"]["content"] or ""
            return ch.strip(), r.get("usage", {})
        except Exception as e:  # noqa
            err = e; time.sleep(5)
    raise RuntimeError(f"LLM failed: {err}")


def call_g0(question, tag):
    form = {**CRED, "question": question, "document_names": "[]", "mode": "graph_vector_fulltext", "model": "gemma4",
            "embedding_provider": "vllm", "embedding_model": "cl-nagoya/ruri-v3-310m", "session_id": tag, "history": "false"}
    req = urllib.request.Request(BACKEND + "/chat_bot", data=urllib.parse.urlencode(form).encode())
    d = json.load(urllib.request.urlopen(req, timeout=900))
    if d.get("status") != "Success":
        raise RuntimeError(str(d)[:300])
    x = d["data"]; info = x.get("info") or {}
    ids = [c.get("id") for c in ((info.get("nodedetails") or {}).get("chunkdetails") or [])]
    ctx = (info.get("metric_details") or {}).get("contexts") or ""
    return (x.get("message") or "").strip(), [IDX[c] for c in ids if c in IDX], ctx


# ---------- 採点（機械） ----------
ABSTAIN = re.compile(r"記載がない|記載はない|記載されていない|定めがない|定めはない|基準には無い|基準にはない|文脈には|見当たらない|確認できない|情報がない|情報はない|分かりません|わかりません")


def score(item, answer, sel_idx):
    na = norm(answer)
    keys = item.get("answer_keys_primary") or item.get("answer_keys") or []
    hit = [k for k in keys if norm(k) in na]
    bonus = [k for k in (item.get("answer_keys_bonus") or []) if norm(k) in na]
    gold_docs = {doc_key(d + ".pdf") for d in item["breadth"]["doc_list"]}
    gold_docs.discard(None)
    said = ids_in_text(answer)
    doc_recall = len(said & gold_docs) / len(gold_docs) if gold_docs else None
    extra_docs = sorted(said - gold_docs)
    trap_vals = set()
    for tq in item.get("trap_quotes") or []:
        for v in re.findall(r"\d+(?:[.,]\d+)?", unicodedata.normalize("NFKC", tq)):
            if len(v) >= 2:
                trap_vals.add(norm(v))
    gold_vals = {norm(v) for k in keys for v in re.findall(r"\d+(?:[.,]\d+)?", unicodedata.normalize("NFKC", k))}
    trap_hit = sorted(v for v in trap_vals - gold_vals if v in na)
    gold_idx = {IDX[c] for g in item["gold_groups"] for c in g.get("ids", []) if c in IDX}
    # gold_groups に ids が無い場合は引用で解決
    if not gold_idx:
        for g in item["gold_groups"]:
            for qt in g.get("quotes", []):
                key = norm(qt)
                gold_idx.update(i for i, t in enumerate(NTEXT) if key and key in t)
    reached = len(gold_idx & set(sel_idx)) if sel_idx is not None else None
    return {"keys_hit": len(hit), "keys_total": len(keys), "key_recall": len(hit) / len(keys) if keys else None, "keys_missing": [k for k in keys if k not in hit],
            "bonus_hit": len(bonus), "doc_recall": doc_recall, "docs_said": sorted(said), "extra_docs": extra_docs,
            "trap_hit": trap_hit, "abstain": bool(ABSTAIN.search(answer)), "gold_chunks_in_context": reached, "gold_chunks_total": len(gold_idx)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--conds", default="llm_only,B,N,G1"); ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--temperature", type=float, default=0.0); ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--qids", default="")
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    items = json.load(open(a.set, encoding="utf-8"))
    if a.qids:
        want = set(a.qids.split(",")); items = [x for x in items if x["qid"] in want]
    if a.limit:
        items = items[:a.limit]
    conds = a.conds.split(",")
    results = []
    log = open(out / "run.log", "a", encoding="utf-8")
    for it in items:
        q = it["question"]
        for cond in conds:
            sel = None; ctx = None; info = {}
            t0 = time.time()
            if cond == "llm_only":
                pass
            elif cond == "G0":
                pass
            else:
                cands, info = {"B": cand_B, "N": cand_N, "G1": cand_G1}[cond](q)
                struct = set(info.pop("struct", [])) if cond in ("N", "G1") else None
                sel, top10, rank = select(q, cands, struct=struct, reserve=RESERVE if struct else 0)
                info["n_candidates"] = len(cands); info["top10"] = [f"{CHUNKS[IDS[i]]['doc'].replace('.pdf', '')}#{CHUNKS[IDS[i]]['position']}" for i in top10]
                info["n_struct"] = len(struct) if struct else 0
                gold_idx = {IDX[c] for g in it["gold_groups"] for c in g.get("ids", []) if c in IDX}
                if not gold_idx:
                    for g in it["gold_groups"]:
                        for qt in g.get("quotes", []):
                            key = norm(qt); gold_idx.update(i for i, t in enumerate(NTEXT) if key and key in t)
                info["gold_in_candidates"] = sum(1 for i in gold_idx if i in rank)
                info["gold_rerank_ranks"] = sorted(rank[i] for i in gold_idx if i in rank)
                info["gold_from_struct"] = sum(1 for i in gold_idx if struct and i in struct)
                ctx = build_context(sel)
            for run in range(a.runs):
                if cond == "G0":
                    ans, sel, ctx = call_g0(q, f"kgeval-{it['qid']}-{run}")
                    usage = {}
                else:
                    ans, usage = llm(q, ctx, a.temperature)
                sc = score(it, ans, sel)
                rec = {"qid": it["qid"], "aspect": it["aspect"], "cond": cond, "run": run, "answer": ans, "score": sc,
                       "selected": [f"{CHUNKS[IDS[i]]['doc'].replace('.pdf', '')}#{CHUNKS[IDS[i]]['position']}" for i in sel] if sel else [],
                       "context_chars": len(ctx) if ctx else 0, "usage": usage, "retrieval_info": info, "sec": round(time.time() - t0, 1)}
                results.append(rec)
                log.write(json.dumps(rec, ensure_ascii=False) + "\n"); log.flush()
                print(f"{it['qid']:20s} {cond:8s} r{run} keys {sc['keys_hit']}/{sc['keys_total']} docR {sc['doc_recall']} trap {sc['trap_hit']} abst {int(sc['abstain'])} ctx {rec['context_chars']} gold {sc['gold_chunks_in_context']}/{sc['gold_chunks_total']}", flush=True)
        json.dump(results, open(out / "results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 集計
    agg = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in results:
        s = r["score"]
        agg[(r["aspect"], r["cond"])]["key_recall"].append(s["key_recall"] if s["key_recall"] is not None else 0)
        agg[(r["aspect"], r["cond"])]["full"].append(int(s["key_recall"] == 1.0))
        if s["doc_recall"] is not None:
            agg[(r["aspect"], r["cond"])]["doc_recall"].append(s["doc_recall"])
        agg[(r["aspect"], r["cond"])]["extra"].append(len(s["extra_docs"]))
        agg[(r["aspect"], r["cond"])]["trap"].append(int(bool(s["trap_hit"])))
        agg[(r["aspect"], r["cond"])]["abstain"].append(int(s["abstain"]))
        agg[(r["aspect"], r["cond"])]["ctx"].append(r["context_chars"])
    rows = []
    for (asp, cond), v in sorted(agg.items()):
        m = lambda k: (sum(v[k]) / len(v[k])) if v[k] else None
        rows.append({"aspect": asp, "cond": cond, "n": len(v["full"]), "key_recall": m("key_recall"), "all_keys": m("full"), "doc_recall": m("doc_recall"),
                     "extra_docs": m("extra"), "trap_rate": m("trap"), "abstain_rate": m("abstain"), "ctx_chars": m("ctx")})
        print(f"{asp:12s} {cond:8s} n={len(v['full']):2d} 鍵再現={m('key_recall'):.2f} 全鍵={m('full'):.2f} 文書再現={(m('doc_recall') or 0):.2f} 誤混入={m('extra'):.2f} 罠={m('trap'):.2f} 棄権={m('abstain'):.2f} 文脈={int(m('ctx') or 0)}")
    json.dump(rows, open(out / "summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
