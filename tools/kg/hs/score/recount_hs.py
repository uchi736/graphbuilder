# 3 つの書き出し（標準 / スキーマ統制 / 規定レコード）を、方式の持ち方に依らない数え方で数え直す。
# 読むのは書き出し済みの JSON と正解グラフだけ。標準ライブラリのみ。結果は recount.json と標準出力。
#
# ══ 数え方の規則 ══════════════════════════════════════════════════════════
# 【共通】名前の照合は NFKC（全角と半角、半角カナ）、空白の除去、大文字化をそろえて行う（score_gold.py と同じ）。
#         数え直しでは加えて、長音とハイフン類、波ダッシュ類をそろえる。
#         正解の文書番号（HS-VTR-T00x）は「HS-」の有無を問わない（正解の別名では T002 だけ「VTR-T002」が抜けている）。
#
# 【エンティティ】ノードの名前（props.id）と、正解の id・別名（鍵）を比べる。規則の本体は matcher.py の冒頭。
#   strict   その物のノード : 一致、前後 3 文字までの伸び（いまの数え方と同じ幅）、末尾の括弧書きを除くと一致。
#                             ただし別の正解の物の、より長い鍵に覆われる出現は数えない（RU形過給機 は 過給機 に数えない）。
#   modified 修飾つきの名前 : 鍵を含み、別の正解の物を含まず、鍵が名前の半分以上（最大でも50kPa以下）。
#   sentence 文の形         : 鍵を含み、別の正解の物を含まず、鍵が名前の半分未満（原則として3年毎に定期見直しを実施する）。
#   compound 複合の名前     : 鍵を含み、別の正解の物も含む（RU.RH.RH3.AT形過給機）。
#   props    プロパティだけ : どのノードの名前にも無いが、ノードの説明・プロパティ、リレーションのプロパティに鍵が在る。
#   「在る」の本線は strict + modified + sentence（名前がそれを含み、別の正解の物を含まない）。compound と props は別の行に出す。
#   1 つの物あたりのノードの数は strict のノードで数える（いまの数え方と同じ幅で、拾いすぎを除いたもの）。
#
# 【リレーション】正解 38 本。両端のノードは、上の 4 段階（strict〜compound）のどれかで当たるノード。
#   direct 直接 : 両端のノードの間に線が 1 本ある（向きと種類は問わない）。
#   relay  中継 : 両端のノードが、中継のノード 1 つを経てつながる。中継のノードは
#                   ・別の正解の物そのもの（strict / modified で当たるノード）ではない、かつ
#                   ・両端と同じチャンクに付いている（3 つに共通のチャンクが 1 つ以上ある）。
#                 文書→文書の参照（REFERENCES の 6 本）には中継を使わない（並んで挙げられただけの文書どうしが当たるため）。
#   value  値   : 目的語が値（正解の型が Criterion / Parameter、および「6点」）の 9 本について、値の鍵が
#                   ・主語のノード自身の名前・説明・プロパティ、
#                   ・主語のノードの隣のノードの名前・説明・プロパティ、またはその間のリレーションのプロパティ、
#                   ・2 回でたどれるノードの名前・説明・プロパティ（中継は別の正解の物そのものではなく、3 つに共通のチャンクがある）
#                 のどれかに在る。数値の鍵は、前後が数字でないことを条件にする（2℃ が 12℃ に当たらない）。
#   desc   説明 : 目的語が値でない物でも、目的語の鍵が主語のノード自身の説明・プロパティに在れば数える
#                 （標準の抽出は「吐出圧力」の説明に「圧力計」と持つ）。隣のノードまでは広げない。
#   reflink 参照: 文書→文書の 6 本は、下の【文書間の参照】の link（線）で数える（正解の 11 件のうちの 6 件と同じ事実）。
#   数え直しの合計 = 参照でない 32 本の（direct / relay / value / desc のどれか）＋ 文書→文書 6 本の reflink。
#   種類の一致は direct だけで見る（中継と値には、比べる種類が 1 つに決まらない）。
#
# 【文書間の参照】正解 11 件（参照元の文書 → 参照先の文書）。参照先の文書を表すノード＝名前に参照先の文書番号（VTR-T00x）を含むノード。
#   mention 言及 : 参照元の文書のチャンクに、参照先の文書を表すノードが付いている。
#   link    線   : 参照元の文書のチャンク 1 つに、エンティティ e と参照先の文書を表すノード n がともに付いており、
#                  e（参照先の文書番号を名前に含まない）と n が、直接または中継 1 つでつながる。
#   bridge  橋   : link が成り立ち、かつ同じノード n が参照先の文書のチャンクにも付いている（n を経て参照先の本文へ渡れる）。
#   link の中継のノードも、同じチャンクに付いていることを条件にする。
#   参考として、本文に在る参照の全体（参照元のチャンクの本文に参照先の文書番号が現れる組。目録の表は文書番号が行で割れていて
#   機械では拾えないので、正解の 11 件を合わせる）でも同じ数え方をする。
#
# 【出どころ】リレーションの両端がともに付いているチャンクの数（0 / 1 / 2 以上）。1 つに決まる割合を出す。
#   全部のリレーションについてと、正解のリレーション（参照でない 32 本）を実現している経路（直接・中継・値・説明）についての 2 通り。
#   出どころは両端のノードが付くチャンクから求めるので、同じ物を細かいノードに分けるほど 1 つに決まりやすい点に注意する。
#   後者は、1 つに決まったチャンクの本文に、主語と目的語の鍵がともに現れるかも見る。
# ═════════════════════════════════════════════════════════════════════
import sys, json, re, ast, collections, unicodedata, os, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import *
from matcher import Matcher, TIERS, norm2, occurrences, is_serial, usable

ap = argparse.ArgumentParser()
ap.add_argument("--gold", required=True)
ap.add_argument("--run", action="append", required=True, help="名前=書き出しのパス[@文書の一覧のファイル（, 区切り）]")
ap.add_argument("--out", required=True)
ARGS = ap.parse_args()
RUNS = {}
for spec in ARGS.run:
    label, rest = spec.split("=", 1)
    path, _, filt = rest.partition("@")
    files = open(filt, encoding="utf-8").read().strip().split(",") if filt else None
    RUNS[label] = (path, files)
METHODS = list(RUNS)
def gold():
    return json.load(open(ARGS.gold, encoding="utf-8"))
def load(method):
    path, files = RUNS[method]
    return load_dump(path, files)

class _RC:
    """いまの数え方（score_gold.py）の再現。01_reproduce_current.py と同じ照合（完全一致と前後 3 文字の伸び）。"""
    def current(self, method):
        d, ents, chunks, ent_chunks, chunk_ents = load(method)
        nodes = [{"eid": e["id"], "id": e["props"].get("id"), "labels": e["labels"]} for e in d["entities"] if ent_chunks.get(e["id"])]
        by_norm = collections.defaultdict(list)
        for n in nodes: by_norm[norm(n["id"])].append(n)
        def match(gent):
            keys = {norm(gent["id"])} | {norm(a) for a in gent.get("aliases", [])}
            hits = []
            for k in keys: hits += by_norm.get(k, [])
            for k in keys:
                if len(k) >= 3:
                    for nk, ns in by_norm.items():
                        if nk in keys: continue
                        if (nk.startswith(k) or nk.endswith(k)) and len(nk) - len(k) <= 3: hits += ns
            return list({h["eid"]: h for h in hits}.values())
        rows = []; ms = {}
        for ge in g["entities"]:
            m = match(ge); ms[ge["id"]] = {x["eid"] for x in m}
            rows.append({"gold": ge["id"], "found": bool(m), "ids": [x["id"] for x in m], "labels": dict(collections.Counter(l for x in m for l in x["labels"])), "type": ge["type"]})
        adj = collections.defaultdict(list)
        for r in d["relationships"]:
            adj[(r["source"], r["target"])].append(r["type"]); adj[(r["target"], r["source"])].append(r["type"])
        rel = []
        for gr in g["relations"]:
            A_ = ms.get(gr["s"], set()); B_ = ms.get(gr["o"], set())
            types = set(t for a in A_ for b in B_ for t in adj.get((a, b), []))
            rel.append({"gold": f'{gr["s"]} -[{gr["p"]}]-> {gr["o"]}', "any": bool(types), "typed": bool(types & set(gr["accept"] + [gr["p"]]))})
        return {"found": sum(r["found"] for r in rows), "missing": [r["gold"] for r in rows if not r["found"]], "rows": rows,
                "rel_miss": [r["gold"] for r in rel if not r["any"]], "typed": sum(r["typed"] for r in rel), "any": sum(r["any"] for r in rel),
                "type_ok": sum(1 for r in rows if r["found"] and r["type"] in r["labels"])}
rc = _RC()
g = gold()
# 正解の文書番号は HS- の有無を問わない（別名の抜けを補う）
for ge in g["entities"]:
    m = re.fullmatch(r"HS-(VTR-T\d{3})", ge["id"])
    if m and m.group(1) not in ge["aliases"]: ge["aliases"] = ge["aliases"] + [m.group(1)]
M = Matcher(g["entities"])
gtype = {e["id"]: e["type"] for e in g["entities"]}
VALUE_TYPES = ("Criterion", "Parameter", "Value", "Condition")  # Value・Condition は社内規定の正解（rules/gold_rules.json）の型。技術基準の正解には無い
EXTRA_VALUE_KEYS = {"6点": {norm2("6点")}}                     # 正解のエンティティ一覧に無い目的語
REF_RELS = [r for r in g["relations"] if r["p"] == "REFERENCES"]
DOCNUM = {"HS-VTR-T001E.pdf": "VTR-T001", "HS-VTR-T002F.pdf": "VTR-T002", "VTR-T003D.pdf": "VTR-T003",
          "VTR-T004C.pdf": "VTR-T004", "VTR-T005B.pdf": "VTR-T005"}
# 規定レコードの名寄せ辞書（req_materialize.py の ALIAS）を読むだけ読む
HAS_DOCREFS = any(r["p"] == "REFERENCES" for r in g["relations"])
src = open(K + "/req_materialize.py", encoding="utf-8").read()
ALIAS = ast.literal_eval(re.search(r"ALIAS = (\{.*?\n\})", src, re.S).group(1))
ALIAS_TARGETS = set(ALIAS.values())

ENDPOINT_TIERS = set(TIERS)      # リレーションの両端に使うノードの段階。06_sensitivity.py が狭めて数え直す

def value_keys(o):
    return M.keys[o] if o in M.keys else EXTRA_VALUE_KEYS[o]

def value_in(o, text):
    t = norm2(text or "")
    for k in value_keys(o):
        if t == k: return True
        if (len(k) >= 3 or k[0].isdigit()) and occurrences(k, t): return True
    return False

def blob(e):
    p = e["props"]; extra = ""
    if p.get("value"): extra = f' {p.get("value","")}{p.get("unit","")}'
    return " ".join(str(v) for k, v in p.items() if not (k == "id" and is_serial(str(v)))) + extra

def blob_wo_name(e):
    return " ".join(str(v) for k, v in e["props"].items() if k != "id")

def key_in(gid, text):
    t = norm2(text or "")
    return any(t == k or (usable(k) and occurrences(k, t)) for k in M.keys[gid])

def is_value_rel(gr):
    return gr["o"] in EXTRA_VALUE_KEYS or gtype.get(gr["o"]) in VALUE_TYPES


def structure(d, ents, chunks, ent_chunks):
    """構造の数: エンティティ・リレーションの数、種類の数、RELATED_TO の割合、チャンクあたりの数、1 本しかない種類の数"""
    n_chunks = max(1, len(chunks))
    rel_types = collections.Counter(r["type"] for r in d["relationships"])
    labels = collections.Counter(l for e in d["entities"] for l in e["labels"])
    attached = [e for e in d["entities"] if ent_chunks.get(e["id"])]
    return {"chunks": len(chunks), "entities": len(attached), "relationships": len(d["relationships"]),
            "rel_types": len(rel_types), "rel_types_singleton": sum(1 for v in rel_types.values() if v == 1),
            "related_to_share": round(sum(v for k, v in rel_types.items() if k.startswith("RELATED_TO")) / max(1, len(d["relationships"])), 3),
            "entity_labels": len(labels), "entities_per_chunk": round(len(attached) / n_chunks, 1), "rels_per_chunk": round(len(d["relationships"]) / n_chunks, 1),
            "top_rel_types": rel_types.most_common(12)}

def recount(method):
    d, ents, chunks, ent_chunks, chunk_ents = load(method)
    cur = rc.current(method)
    nm = lambda eid: ents[eid]["props"].get("id")
    out = {"method": method}
    # ── エンティティ ──
    ms = collections.defaultdict(dict)                         # gold id -> {eid: tier}
    for e in d["entities"]:
        if not ent_chunks.get(e["id"]): continue               # チャンクに付いたノードだけ（score_gold.py と同じ）
        # 名前と、統合で残った別名（props.aliases。表記ゆれの統合・手動の統合が書く）のどれかで当たれば当たり。段階は良い方を取る（X35）
        al = e["props"].get("aliases"); al = al if isinstance(al, list) else []
        for nm_ in [e["props"].get("id")] + [a for a in al if isinstance(a, str)]:
            for gid, (tier, cov) in M.match_name(nm_).items():
                if gid not in ms or e["id"] not in ms[gid] or TIERS.index(tier) < TIERS.index(ms[gid][e["id"]]): ms[gid][e["id"]] = tier
    node_tier_gold = collections.defaultdict(dict)             # eid -> {gid: tier}
    for gid, mm in ms.items():
        for eid, t in mm.items(): node_tier_gold[eid][gid] = t
    all_blobs = [(e["id"], blob(e)) for e in d["entities"]]
    rel_blobs = [json.dumps(r.get("props") or {}, ensure_ascii=False) for r in d["relationships"]]
    exact_names = collections.Counter(norm(e["props"].get("id") or "") for e in d["entities"] if ent_chunks.get(e["id"]))
    rows = []
    for ge in g["entities"]:
        gid = ge["id"]; mm = ms.get(gid, {})
        tiers = collections.Counter(mm.values())
        best = next((t for t in TIERS if tiers.get(t)), None)
        if best is None:
            in_props = any(value_in(gid, b) for _, b in all_blobs) or any(value_in(gid, b) for b in rel_blobs)
            best = "props" if in_props else "none"
        exact = any(exact_names.get(norm(a)) for a in [gid] + ge["aliases"])
        rows.append({"gold": gid, "type": ge["type"], "is_value": ge["type"] in VALUE_TYPES, "best": best, "exact": exact,
                     "current": gid not in cur["missing"], "n_strict": tiers.get("strict", 0), "n_all": len(mm),
                     "names": {t: sorted({nm(e) for e, tt in mm.items() if tt == t})[:6] for t in TIERS if tiers.get(t)},
                     "labels": sorted({l for e, tt in mm.items() if tt == "strict" for l in ents[e]["labels"]})})
    over = []
    cur_ids = {r["gold"]: set(r["ids"]) for r in cur["rows"]}
    for ge in g["entities"]:
        mine = {nm(e) for e in ms.get(ge["id"], {})}
        for n in sorted(cur_ids[ge["id"]] - mine): over.append(f'{ge["id"]}←{n}')
    def cnt(pred): return sum(1 for r in rows if pred(r))
    cum = {}
    acc = set()
    for t in TIERS + ["props"]:
        acc |= {r["gold"] for r in rows if r["best"] == t}; cum[t] = len(acc)
    strict_rows = [r for r in rows if r["n_strict"] > 0]
    na = [r for r in strict_rows if r["gold"] not in ALIAS_TARGETS]
    # 名寄せ辞書を 3 方式に同じように当てた場合（strict のノードの名前を辞書で寄せてから、相異なる名前の数を数える）
    def canon(n):
        x = re.sub(r"\s+", "", unicodedata.normalize("NFKC", n or ""))
        return norm(ALIAS.get(x, x))
    merged = []
    for ge in g["entities"]:
        names = {canon(nm(e)) for e, t in ms.get(ge["id"], {}).items() if t == "strict"}
        if names: merged.append(len(names))
    out["entities"] = {
        "gold": len(rows), "things": cnt(lambda r: not r["is_value"]), "values": cnt(lambda r: r["is_value"]),
        "exact": cnt(lambda r: r["exact"]), "current": cnt(lambda r: r["current"]),
        "strict": cum["strict"], "plus_modified": cum["modified"], "plus_sentence": cum["sentence"], "plus_compound": cum["compound"], "plus_props": cum["props"],
        "none": cnt(lambda r: r["best"] == "none"),
        "by_best": dict(collections.Counter(r["best"] for r in rows)),
        "things_main": cnt(lambda r: not r["is_value"] and r["best"] in ("strict", "modified", "sentence")),
        "values_main": cnt(lambda r: r["is_value"] and r["best"] in ("strict", "modified", "sentence")),
        "values_any": cnt(lambda r: r["is_value"] and r["best"] != "none"),
        "current_but_not_strict": [r["gold"] for r in rows if r["current"] and r["n_strict"] == 0],
        "current_overmatch_pairs": over,
        "values_current": cnt(lambda r: r["is_value"] and r["current"]), "things_current": cnt(lambda r: not r["is_value"] and r["current"]),
        "only_by_extension": [r["gold"] for r in rows if r["n_strict"] > 0 and not r["exact"]],
        "strict_but_not_current": [r["gold"] for r in rows if not r["current"] and r["n_strict"] > 0],
        "nodes_per_thing_strict": round(sum(r["n_strict"] for r in strict_rows) / max(1, len(strict_rows)), 2),
        "fragmented_strict": sum(1 for r in strict_rows if r["n_strict"] > 1),
        "nodes_per_thing_strict_excl_alias_dict": round(sum(r["n_strict"] for r in na) / max(1, len(na)), 2),
        "fragmented_strict_excl_alias_dict": f'{sum(1 for r in na if r["n_strict"] > 1)}/{len(na)}',
        "names_per_thing_after_same_dict": round(sum(merged) / max(1, len(merged)), 2),
        "fragmented_after_same_dict": sum(1 for x in merged if x > 1),
        "rows": rows}
    # ── リレーション ──
    adj = collections.defaultdict(list)
    for r in d["relationships"]:
        adj[r["source"]].append((r["target"], r)); adj[r["target"]].append((r["source"], r))
    def is_other_gold_node(x, s, o):
        return any(t in ("strict", "modified") and gid not in (s, o) for gid, t in node_tier_gold.get(x, {}).items())
    rel_rows = []
    for gr in g["relations"]:
        key = f'{gr["s"]} -[{gr["p"]}]-> {gr["o"]}'
        A = {e: t for e, t in ms.get(gr["s"], {}).items() if t in ENDPOINT_TIERS}; B = {e: t for e, t in ms.get(gr["o"], {}).items() if t in ENDPOINT_TIERS}
        is_ref = gr["p"] == "REFERENCES"; is_val = is_value_rel(gr)
        wit = []                                               # (種別, ノードの並び, 線の種類)
        for a in A:
            for (x, r) in adj[a]:
                if x in B: wit.append(("direct", (a, x), (r["type"],)))
        if not is_ref:
            for a in A:
                for (x, r1) in adj[a]:
                    if x in A or x in B or is_other_gold_node(x, gr["s"], gr["o"]): continue
                    for (b, r2) in adj[x]:
                        if b in B and b != a and (ent_chunks[a] & ent_chunks[x] & ent_chunks[b]):
                            wit.append(("relay", (a, x, b), (r1["type"], r2["type"])))
        if is_val:
            for a in A:
                if value_in(gr["o"], blob(ents[a])): wit.append(("value", (a,), ()))
                for (x, r1) in adj[a]:
                    if value_in(gr["o"], blob(ents[x])) or value_in(gr["o"], json.dumps(r1.get("props") or {}, ensure_ascii=False)):
                        wit.append(("value", (a, x), (r1["type"],)))
                    if is_other_gold_node(x, gr["s"], gr["o"]): continue
                    for (y, r2) in adj[x]:
                        if y != a and value_in(gr["o"], blob(ents[y])) and (ent_chunks[a] & ent_chunks[x] & ent_chunks[y]):
                            wit.append(("value", (a, x, y), (r1["type"], r2["type"])))
        if not is_ref and not is_val:
            for a in A:
                if key_in(gr["o"], blob_wo_name(ents[a])): wit.append(("desc", (a,), ()))
        kinds = {w[0] for w in wit}
        acc = set(gr["accept"] + [gr["p"]])
        typed = any(w[0] == "direct" and (set(w[2]) & acc) for w in wit)
        # 出どころ
        skeys = M.keys[gr["s"]]; okeys = value_keys(gr["o"])
        uniq = False; uniq_text = False
        for (kind, nodes, types) in wit:
            common = set.intersection(*[set(ent_chunks[n]) for n in nodes])
            if len(common) == 1:
                uniq = True
                t = norm2(chunks[next(iter(common))]["text"])
                if any(k in t for k in skeys) and any(k in t for k in okeys): uniq_text = True
        ex = None
        for pref in ("direct", "relay", "value", "desc"):
            ws = [w for w in wit if w[0] == pref]
            if ws:
                w = ws[0]; ex = pref + ": " + " — ".join((nm(n) or "")[:40] for n in w[1]) + (" [" + ",".join(w[2]) + "]" if w[2] else ""); break
        rel_rows.append({"gold": key, "is_ref": is_ref, "is_value": is_val, "current": key not in cur["rel_miss"],
                         "direct": "direct" in kinds, "relay": "relay" in kinds, "value": "value" in kinds, "desc": "desc" in kinds, "hit": bool(kinds), "typed_direct": typed,
                         "s": gr["s"], "o": gr["o"], "p": gr["p"],
                         "prov_unique": uniq, "prov_unique_text": uniq_text, "s_nodes": len(A), "o_nodes": len(B), "example": ex})
    def rc_(pred): return sum(1 for r in rel_rows if pred(r))
    out["relations"] = {
        "gold": len(rel_rows), "ref": rc_(lambda r: r["is_ref"]), "value_obj": rc_(lambda r: r["is_value"]),
        "current": rc_(lambda r: r["current"]), "current_typed": cur["typed"],
        "direct": rc_(lambda r: r["direct"]), "typed_direct": rc_(lambda r: r["typed_direct"]),
        "direct_or_relay": rc_(lambda r: r["direct"] or r["relay"]), "hit": rc_(lambda r: r["hit"]),
        "nonref_current": rc_(lambda r: not r["is_ref"] and r["current"]), "nonref_hit": rc_(lambda r: not r["is_ref"] and r["hit"]), "nonref_total": rc_(lambda r: not r["is_ref"]),
        "value_current": rc_(lambda r: r["is_value"] and r["current"]), "value_hit": rc_(lambda r: r["is_value"] and r["hit"]),
        "ref_current": rc_(lambda r: r["is_ref"] and r["current"]), "ref_direct": rc_(lambda r: r["is_ref"] and r["direct"]),
        "only_relay": [r["gold"] for r in rel_rows if r["relay"] and not r["direct"]],
        "only_value": [r["gold"] for r in rel_rows if r["value"] and not r["direct"] and not r["relay"]],
        "new_direct": [r["gold"] for r in rel_rows if r["direct"] and not r["current"]],
        "lost": [r["gold"] for r in rel_rows if r["current"] and not r["hit"]],
        "miss": [r["gold"] for r in rel_rows if not r["hit"]],
        "prov_unique": rc_(lambda r: r["hit"] and r["prov_unique"]), "prov_unique_text": rc_(lambda r: r["hit"] and r["prov_unique_text"]),
        "rows": rel_rows}
    # ── 文書間の参照 ──
    file_chunks = collections.defaultdict(list)
    for c in d["chunks"]: file_chunks[c["fileName"]].append(c["id"])
    def names_doc(eid, num):
        n = ents[eid]["props"].get("id") or ""
        return (not is_serial(n)) and bool(occurrences(num, norm2(n).replace("HSVTR", "HS-VTR")))
    docnodes = {f: {e["id"] for e in d["entities"] if names_doc(e["id"], num)} for f, num in DOCNUM.items()}
    def docref(F, T):
        N = docnodes[T]; mention = link = bridge = False; via = None
        tchunks = set(file_chunks[T])
        for c in file_chunks[F]:
            here = chunk_ents.get(c, set()); ns = here & N
            if not ns: continue
            mention = True
            for n in ns:
                ok = False
                for (x, r) in adj[n]:
                    if x in here and x not in N: ok = True; via = via or f'{nm(x)[:30]} -{r["type"]}- {nm(n)[:30]}'; break
                if not ok:
                    for (x, r1) in adj[n]:
                        if x in N or x not in here: continue
                        for (e, r2) in adj[x]:
                            if e in here and e not in N and e != n: ok = True; via = via or f'{nm(e)[:24]} -{r2["type"]}- {nm(x)[:24]} -{r1["type"]}- {nm(n)[:24]}'; break
                        if ok: break
                if ok:
                    link = True
                    if ent_chunks[n] & tchunks: bridge = True
        return {"mention": mention, "link": link, "bridge": bridge, "via": via}
    DOCKEY = dict(DOCNUM, **{"VTR-T_発行目録.pdf": "VTR-T-INDEX"})
    std = {(ents[r["source"]]["props"].get("id"), ents[r["target"]]["props"].get("id")) for r in d["relationships"]
           if r["type"] == "REFERENCES" and "Standard" in ents[r["source"]]["labels"] and "Standard" in ents[r["target"]]["labels"]}
    gold_pairs = [(x["from"], x["to"]) for x in g.get("doc_references", [])]
    text_pairs = []
    for F, cs in file_chunks.items():
        for T, num in DOCNUM.items():
            if T == F: continue
            if any(occurrences(num, re.sub(r"^\[[^\]]*\]", "", norm2(chunks[c]["text"])).replace("HSVTR", "HS-VTR")) for c in cs): text_pairs.append((F, T))
    def table(pairs):
        rs = [dict(docref(F, T), pair=f"{F} → {T}", current=(DOCKEY[F], DOCKEY[T]) in std) for F, T in pairs]
        return {"n": len(rs), "current": sum(r["current"] for r in rs), "mention": sum(r["mention"] for r in rs),
                "link": sum(r["link"] for r in rs), "bridge": sum(r["bridge"] for r in rs), "rows": rs}
    if not HAS_DOCREFS:
        for r in rel_rows:
            r["reflink"] = False; r["final"] = r["hit"]
        R = out["relations"]
        R["ref_link"] = 0; R["desc_only"] = [r["gold"] for r in rel_rows if r["desc"] and not (r["direct"] or r["relay"] or r["value"])]
        R["final"] = sum(1 for r in rel_rows if r["final"]); R["final_miss"] = [r["gold"] for r in rel_rows if not r["final"]]
        R["final_prov_unique"] = sum(1 for r in rel_rows if r["final"] and r["prov_unique"]); R["final_prov_unique_text"] = sum(1 for r in rel_rows if r["final"] and r["prov_unique_text"])
        R["final_nonref"] = R["final"]
        out["structure"] = structure(d, ents, chunks, ent_chunks)
        return out
    out["doc_refs_gold"] = table(gold_pairs)
    num2file = {"HS-" + v: k for k, v in DOCNUM.items()}
    for r in rel_rows:
        r["reflink"] = False
        if r["is_ref"]:
            dr = docref(num2file[r["s"]], num2file[r["o"]]); r["reflink"] = dr["link"]; r["reflink_via"] = dr["via"]
        r["final"] = r["reflink"] if r["is_ref"] else r["hit"]
    R = out["relations"]
    R["ref_link"] = sum(1 for r in rel_rows if r["is_ref"] and r["reflink"])
    R["desc_only"] = [r["gold"] for r in rel_rows if r["desc"] and not (r["direct"] or r["relay"] or r["value"])]
    R["final"] = sum(1 for r in rel_rows if r["final"])
    R["final_miss"] = [r["gold"] for r in rel_rows if not r["final"]]
    R["final_prov_unique"] = sum(1 for r in rel_rows if not r["is_ref"] and r["final"] and r["prov_unique"])
    R["final_prov_unique_text"] = sum(1 for r in rel_rows if not r["is_ref"] and r["final"] and r["prov_unique_text"])
    R["final_nonref"] = sum(1 for r in rel_rows if not r["is_ref"] and r["final"])
    out["doc_refs_text_found_mechanically"] = len(text_pairs)
    text_pairs = sorted(set(text_pairs) | set(gold_pairs))
    out["doc_refs_text"] = table(text_pairs)
    out["doc_refs_text_not_in_gold"] = table(sorted(set(text_pairs) - set(gold_pairs)))
    out["doc_node_counts"] = {DOCNUM[f]: len(v) for f, v in docnodes.items()}
    # ── 出どころ（全部のリレーション）──
    dist = collections.Counter()
    for r in d["relationships"]:
        n = len(ent_chunks[r["source"]] & ent_chunks[r["target"]]); dist["0" if n == 0 else "1" if n == 1 else "2+"] += 1
    tot = sum(dist.values())
    out["provenance_all"] = {"relationships": tot, "0": dist["0"], "1": dist["1"], "2+": dist["2+"], "unique_ratio": round(dist["1"] / tot, 3)}
    # 内訳: 番号だけのノード（規定のノード）に付く線 / 文書番号のノードどうしの線 / それ以外（名前のあるエンティティどうしの線）
    alldoc = set().union(*docnodes.values())
    kinds = collections.defaultdict(collections.Counter)
    for r in d["relationships"]:
        a, b = r["source"], r["target"]
        k = "record" if (is_serial(nm(a) or "") or is_serial(nm(b) or "")) else "doc_doc" if (a in alldoc and b in alldoc) or ("Standard" in ents[a]["labels"] and "Standard" in ents[b]["labels"]) else "entity_entity"
        n = len(ent_chunks[a] & ent_chunks[b]); kinds[k]["0" if n == 0 else "1" if n == 1 else "2+"] += 1
    out["provenance_by_kind"] = {k: {"n": sum(v.values()), "1": v["1"], "0": v["0"], "2+": v["2+"]} for k, v in kinds.items()}
    out["structure"] = structure(d, ents, chunks, ent_chunks)
    return out

if __name__ == "__main__":
    res = {m: recount(m) for m in METHODS}
    json.dump(res, open(ARGS.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    def row(label, f):
        cells = []
        for m in METHODS:
            try: cells.append(str(f(res[m])))
            except Exception: cells.append("-")
        print(f"| {label} | " + " | ".join(cells) + " |")
    E = res[METHODS[0]]["entities"]; R = res[METHODS[0]]["relations"]
    print("| 見たもの | " + " | ".join(METHODS) + " |"); print("|" + "---|" * (len(METHODS) + 1))
    row(f"エンティティ: 本線（{E['gold']} 個のうち、strict＋修飾＋文の形）", lambda r: r["entities"]["plus_sentence"])
    row("　うち物（本線）", lambda r: f'{r["entities"]["things_main"]}/{r["entities"]["things"]}')
    row("　うち値（本線）", lambda r: f'{r["entities"]["values_main"]}/{r["entities"]["values"]}')
    row("エンティティ: その物のノード（strict）", lambda r: r["entities"]["strict"])
    row("1 つの物あたりのノードの数（strict）", lambda r: r["entities"]["nodes_per_thing_strict"])
    row(f"リレーション: 数え直しの合計（{R['gold']} 本のうち）", lambda r: r["relations"]["final"])
    row("　直接", lambda r: r["relations"]["direct"])
    row("　直接で種類も合う", lambda r: r["relations"]["typed_direct"])
    row("　うち値の線（数え直し）", lambda r: f'{r["relations"]["value_hit"]}/{r["relations"]["value_obj"]}')
    row("構造: チャンク / エンティティ / リレーション", lambda r: f'{r["structure"]["chunks"]} / {r["structure"]["entities"]} / {r["structure"]["relationships"]}')
    row("構造: リレーションの種類の数（うち 1 本だけ）", lambda r: f'{r["structure"]["rel_types"]}（{r["structure"]["rel_types_singleton"]}）')
    row("構造: RELATED_TO の割合", lambda r: f'{r["structure"]["related_to_share"]:.0%}')
    row("構造: チャンクあたりのリレーション", lambda r: r["structure"]["rels_per_chunk"])
    for m in METHODS:
        r = res[m]
        print(f"\n===== {m}")
        print(" 外れたリレーション:", r["relations"]["final_miss"])
        print(" 見つからない物:", [x["gold"] for x in r["entities"]["rows"] if x["best"] in ("none", "props", "compound")])
        print(" 多い種類:", r["structure"]["top_rel_types"][:10])
