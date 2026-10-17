# 作問者向けの点検（検索は呼ばない）。引用の照合、必須の欄、質問に書いてはいけない語、既出の事実との重なりを見る。
#   python3 check_quotes.py out2/cand_A.json
# 出力: 候補ごとに OK か、直すべき点。引用は chunks.json の本文と、空白を除き NFKC・小文字で照合する（judge.py と同じ）。
import json, re, sys, unicodedata, pathlib, collections

HERE = pathlib.Path(__file__).resolve().parent
CH = json.load(open(HERE / "chunks.json", encoding="utf-8"))
IDS = sorted(CH, key=lambda i: (CH[i]["doc"], CH[i]["position"]))
NT = {i: re.sub(r"\s+", "", unicodedata.normalize("NFKC", CH[i]["text"]).lower()) for i in IDS}
USED = collections.defaultdict(list)
for line in open(HERE / "out2/used_facts.tsv", encoding="utf-8").read().splitlines()[1:]:
    qid, s, cat, golds, q = line.split("\t")[:5]
    for g in golds.split(";"):
        USED[g].append(f"{qid}（{q[:30]}）")

CATS = {"lookup", "vocab_gap", "applies", "ref_hop", "entity_hop", "confusable", "table_cond", "negation", "version", "unanswerable", "crosscheck"}
BANNED = [
    ("文書番号", re.compile(r"(?:HS[-‐ ]?)?(?:RU|TP)[-‐ ]?\d{3}|(?:HS[-‐ ]?)?VTR[-‐ ]?[TP]?\d{3}|QC-KAI-\d+|HZTL\d+", re.I)),
    ("節番号", re.compile(r"\d+-\d+\s*項|(?<![\d.])\d-\d{1,2}(?![\d.])|表\s*[-‐]?\s*\d|注\s*\d|別表\s*\d|付表|第\s*\d+\s*項")),
    ("基準表記の型式", re.compile(r"[A-Z]{2,4}\s*・\s*・\s*\d|[A-Z]{2,4}\d{2,3}\s*[~〜～]\s*[A-Z]{0,4}\d{2,3}")),
]


def norm(t):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", t or "").lower())


def label(i):
    return f"{CH[i]['doc'].replace('.pdf', '')}#{CH[i]['position']}"


def resolve(q):
    k = norm(q)
    return [i for i in IDS if k and k in NT[i]]


def main():
    items = json.load(open(sys.argv[1], encoding="utf-8"))
    seen = set()
    n_bad = 0
    for it in items:
        P = []
        qid = it.get("qid", "?")
        if qid in seen:
            P.append("qid が重複")
        seen.add(qid)
        cat = it.get("category")
        if cat not in CATS:
            P.append(f"分類が不明: {cat}")
        for k in ("question", "answer", "answer_keys", "gold_groups", "role", "archetype", "expected_route", "why_hybrid_misses"):
            if not it.get(k):
                P.append(f"欄が空: {k}")
        if cat != "lookup" and not it.get("twin_question"):
            P.append("対照の問い twin_question が無い")
        if cat == "vocab_gap":
            ph = it.get("phrasings") or []
            if len(ph) != 3 or ph[0].get("question") != it.get("question"):
                P.append("言い回しは 3 通りで、p1 は question と同じ文")
        if cat in ("applies", "ref_hop") and not it.get("entry_quote"):
            P.append("起点 entry_quote が無い")
        if cat in ("confusable", "unanswerable") and not it.get("trap_quotes"):
            P.append("罠 trap_quotes が無い")
        golds = []
        for g in it.get("gold_groups") or []:
            hit = []
            for qt in g.get("quotes", []):
                h = resolve(qt)
                if not h:
                    P.append(f"引用が本文に無い（gold「{g.get('name', '')}」）: {qt[:50]}")
                hit += h
            hit += [i for i in g.get("ids", []) if i in CH]
            hit = list(dict.fromkeys(hit))
            docs = {CH[i]["doc"] for i in hit}
            if len(docs) > 1 and not g.get("any_doc"):
                P.append(f"gold「{g.get('name', '')}」が複数文書に当たる: {', '.join(label(i) for i in hit)}（複製なら any_doc: true）")
            golds += hit
        for qt in ([it["entry_quote"]] if it.get("entry_quote") else []) + list(it.get("trap_quotes") or []):
            if not resolve(qt):
                P.append(f"引用が本文に無い（entry/trap）: {qt[:50]}")
        allowed = [norm(a) for a in it.get("printed_terms") or []]
        qs = [("question", it.get("question", ""))] + [(p.get("label", "phrasing"), p.get("question", "")) for p in it.get("phrasings") or []]
        for name, q in qs:
            for bname, rx in BANNED:
                for m in rx.finditer(q):
                    tok = norm(m.group(0))
                    if not any(a in tok or tok in a for a in allowed):
                        P.append(f"{name} に{bname}: {m.group(0)}（lookup 以外は不可。手元の文書に印字された番号なら printed_terms に書く）")
        dup = sorted({u for i in golds for u in USED.get(label(i), [])})
        tag = "要確認" if P else "OK"
        if P:
            n_bad += 1
        print(f"[{tag}] {qid} {cat} gold={', '.join(label(i) for i in golds) or '-'}")
        for p in P:
            print("    - " + p)
        if dup:
            print("    · 既出の事実と同じチャンク（同じ事実を聞いていないか確かめる）: " + " / ".join(dup[:4]))
    print(f"\n{len(items)} 件中 要確認 {n_bad} 件")


if __name__ == "__main__":
    main()
