"""評価用の架空文書と問いを、取り込みの前に機械的に点検する。

使い方:
  python3 check_synth.py --docs <文書のディレクトリ>... --questions <問いの JSON>... [--rerank] [--json <出力>]

点検すること:
  文書 … 字数、実物の番号や型式名が入っていないか、見出しがあるか
  問い … 引用（起点・つなぎ・答え）が文書に実在するか、答えの語が答えの節にあり、質問と起点の節に無いか、
         答えの語がほかの節に現れないか
  --rerank … 稼働中のリランカー（:8006）で、質問に対する全チャンクの順位を出す。
             経路が要る問いは「起点が上位、答えが下位」、直接の問いは「答えが上位」であること

チャンクは実際の分割器の近似である（見出しで節に分け、長い節は段落の切れ目で 800 字以内に分ける。
各チャンクの先頭に [文書名 > 見出しの経路] を付ける）。最終の判定は、取り込んだ後の実測で行う。
"""
import argparse
import json
import pathlib
import re
import sys
import unicodedata
import urllib.request

RERANK_URL = "http://localhost:8006/v1/rerank"
RERANK_MODEL = "cl-nagoya/ruri-v3-reranker-310m"
CHUNK_CHARS = 800
ROUTE_TYPES = {"ref1", "ref2", "ent1", "ent2"}
DIRECT_TYPES = {"control"}
ALL_TYPES = ROUTE_TYPES | DIRECT_TYPES | {"confusable", "unanswerable"}
# 実物の文書にある番号・型式名・社名。架空の文書に入っていてはならない
FORBIDDEN = re.compile(r"HS-VTR|VTR|TPL|TPS|QC-KAI|HZTL|IHI|IHK|ABB|V00\d{3}|V02313|R[UH]3?形|AT形|石川島")
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
norm = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")))


def chunks_of(path: pathlib.Path) -> list:
    """見出しで節に分け、[文書名 > 見出しの経路] を先頭に付けたチャンクの並びを返す"""
    stem = path.stem
    sections, trail, body = [], [], []

    def flush():
        text = "\n".join(body).strip()
        if text:
            sections.append((list(trail), text))
        body.clear()

    fenced = False
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        m = None if fenced else HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            del trail[level - 1:]
            while len(trail) < level - 1:
                trail.append("")
            trail.append(m.group(2).strip())
        else:
            body.append(line)
    flush()

    out = []
    for heads, text in sections:
        crumb = "[" + " > ".join([stem] + [h for h in heads if h]) + "]"
        pieces, current = [], ""
        for para in re.split(r"\n{2,}", text):
            if current and len(current) + len(para) + 2 > CHUNK_CHARS:
                pieces.append(current)
                current = para
            else:
                current = (current + "\n\n" + para) if current else para
        if current:
            pieces.append(current)
        for piece in pieces:
            out.append({"doc": path.name, "heads": [h for h in heads if h], "text": crumb + "\n" + piece, "n": norm(crumb + piece),
                        "body_n": norm(piece), "position": len(out) + 1, "chars": len(piece)})
    return out


def rerank(query: str, docs: list) -> list:
    scores = [0.0] * len(docs)
    for start in range(0, len(docs), 48):
        batch = [d[:1500] for d in docs[start:start + 48]]
        body = json.dumps({"model": RERANK_MODEL, "query": query, "documents": batch, "truncate_prompt_tokens": -1}).encode()
        req = urllib.request.Request(RERANK_URL, data=body, headers={"Content-Type": "application/json"})
        for x in json.load(urllib.request.urlopen(req, timeout=300))["results"]:
            scores[start + x["index"]] = x["relevance_score"]
    return scores


def find(quote: str, pool: list) -> list:
    q = norm(quote)
    return [c for c in pool if q and q in c["n"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", nargs="+", required=True)
    ap.add_argument("--questions", nargs="*", default=[])
    ap.add_argument("--rerank", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--entry-top", type=int, default=3, help="経路が要る問いで、起点のチャンクが入るべき順位")
    ap.add_argument("--answer-below", type=int, default=0, help="経路が要る問いで、答えのチャンクがこの順位より下であること（0 なら チャンク数の 2 割と 15 の大きい方）")
    args = ap.parse_args()

    files = sorted(p for d in args.docs for p in pathlib.Path(d).rglob("*.md"))
    chunks, problems = [], []
    print(f"== 文書 {len(files)} 本")
    for p in files:
        raw = p.read_text(encoding="utf-8")
        cs = chunks_of(p)
        chunks.extend(cs)
        notes = []
        if not 4000 <= len(raw) <= 9000:
            notes.append(f"字数 {len(raw)}（目安 4500〜8000）")
        bad = sorted(set(FORBIDDEN.findall(unicodedata.normalize("NFKC", raw))))
        if bad:
            notes.append(f"実物の番号・型式名・社名: {bad}")
        if not any(c["heads"] for c in cs):
            notes.append("見出しが無い")
        long = [c["position"] for c in cs if c["chars"] > CHUNK_CHARS + 200]
        if long:
            notes.append(f"段落で切れない長いチャンク: {long}")
        print(f"  {p.name}: {len(raw)} 字、{len(cs)} チャンク" + ("  ← " + "; ".join(notes) if notes else ""))
        problems += [f"{p.name}: {n}" for n in notes if "実物" in n or "見出し" in n]
    names = [p.name for p in files]
    if len(names) != len(set(names)):
        problems.append("同じファイル名の文書がある")
    print(f"   合計 {len(chunks)} チャンク")

    questions = []
    for q in args.questions:
        data = json.load(open(q, encoding="utf-8"))
        questions += data["questions"] if isinstance(data, dict) else data
    below = args.answer_below or max(15, len(chunks) // 5)
    results = []
    ids = [q.get("qid") for q in questions]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"qid が重複: {dup}")
    for q in questions:
        errs, warns = [], []
        t = q.get("type")
        if t not in ALL_TYPES:
            errs.append(f"type が不正: {t}")
        keys = [norm(k) for k in q.get("answer_keys") or [] if norm(k)]
        qn = norm(q.get("question"))
        of_doc = lambda name: [c for c in chunks if c["doc"] == name]
        entry = find(q.get("entry_quote"), of_doc(q.get("entry_doc")))
        answer = find(q.get("answer_quote"), of_doc(q.get("answer_doc"))) if q.get("answer_doc") else []
        if not entry:
            errs.append("起点の引用が文書に無い")
        if t != "unanswerable":
            if not answer:
                errs.append("答えの引用が文書に無い")
            if not keys:
                errs.append("answer_keys が空")
            answer_strict = [c for c in answer if all(k in c["n"] for k in keys)]
            if answer and not answer_strict:
                errs.append("答えの語が、答えの引用のあるチャンクにそろっていない")
            for k in keys:
                if k in qn:
                    errs.append(f"答えの語が質問に入っている: {k}")
            elsewhere = [f"{c['doc']}#{c['position']}" for c in chunks if keys and all(k in c["n"] for k in keys) and c not in answer]
            if elsewhere:
                (errs if t in ROUTE_TYPES else warns).append(f"答えの語がほかのチャンクにもある: {elsewhere[:5]}")
        else:
            answer_strict = []
        if t in ROUTE_TYPES:
            if any(c in entry for c in answer_strict):
                errs.append("起点と答えが同じチャンクにある")
            if keys and any(all(k in c["n"] for k in keys) for c in entry):
                errs.append("答えの語が起点のチャンクにもある")
            kind = q.get("bridge_kind")
            if kind == "reference":
                if not any(norm(q.get("bridge_quote")) and norm(q.get("bridge_quote")) in c["n"] for c in entry):
                    errs.append("参照の一文が起点のチャンクに無い")
            elif kind == "entity":
                name = norm(q.get("bridge_entity"))
                ends = [name] + [norm(a) for a in q.get("bridge_aliases") or []]
                if not name or not any(name in c["n"] for c in entry):
                    errs.append("つなぎのエンティティの名前が起点のチャンクに無い")
                if t == "ent1" and not any(any(e and e in c["n"] for e in ends) for c in answer_strict):
                    errs.append("つなぎのエンティティの名前（別名を含む）が答えのチャンクに無い")
            else:
                errs.append(f"bridge_kind が不正: {kind}")
            for step in q.get("path") or []:
                if not find(step.get("quote"), of_doc(step.get("doc"))):
                    errs.append(f"中継の引用が文書に無い: {step.get('doc')}")
            if t in ("ref2", "ent2") and not q.get("path"):
                errs.append("2 回たどる問いなのに path（中継）が空")
        rec = {"qid": q.get("qid"), "type": t, "twin_of": q.get("twin_of"),
               "entry": [f"{c['doc']}#{c['position']}" for c in entry], "answer": [f"{c['doc']}#{c['position']}" for c in answer_strict]}
        if args.rerank and chunks and q.get("question"):
            scores = rerank(q["question"], [c["text"] for c in chunks])
            order = sorted(range(len(chunks)), key=lambda i: -scores[i])
            rank_of = {id(chunks[i]): n + 1 for n, i in enumerate(order)}
            best = lambda cs: min((rank_of[id(c)] for c in cs), default=None)
            rec["entry_rank"], rec["answer_rank"] = best(entry), best(answer_strict)
            rec["top3"] = [f"{chunks[i]['doc']}#{chunks[i]['position']}" for i in order[:3]]
            leak = [f"{chunks[i]['doc']}#{chunks[i]['position']}" for i in order[:6] if keys and all(k in chunks[i]["n"] for k in keys) and chunks[i] not in answer_strict]
            if t in ROUTE_TYPES:
                if rec["entry_rank"] is None or rec["entry_rank"] > args.entry_top:
                    errs.append(f"起点が上位に来ない（{rec['entry_rank']} 位。{args.entry_top} 位以内が条件）")
                if rec["answer_rank"] is not None and rec["answer_rank"] <= below:
                    errs.append(f"答えが質問で直接引ける（{rec['answer_rank']} 位。{below} 位より下が条件）")
                if leak:
                    errs.append(f"上位 6 件に答えの語を含む別のチャンクがある: {leak}")
            elif t in DIRECT_TYPES:
                if rec["answer_rank"] is None or rec["answer_rank"] > 3:
                    errs.append(f"直接の問いなのに答えが上位に来ない（{rec['answer_rank']} 位）")
            elif t == "unanswerable":
                if rec["entry_rank"] is None or rec["entry_rank"] > args.entry_top:
                    errs.append(f"起点が上位に来ない（{rec['entry_rank']} 位）")
        rec["errors"], rec["warnings"] = errs, warns
        results.append(rec)
        mark = "NG" if errs else "ok"
        ranks = f" 起点 {rec.get('entry_rank')} 位・答え {rec.get('answer_rank')} 位/{len(chunks)}" if args.rerank else ""
        print(f"  [{mark}] {rec['qid']} {t}{ranks}" + ("  ← " + "; ".join(errs) if errs else "") + ("  （注意: " + "; ".join(warns) + "）" if warns else ""))

    twins = {q.get("twin_of") for q in questions if q.get("twin_of")}
    for q in questions:
        if q.get("type") in ROUTE_TYPES and q.get("qid") not in twins:
            problems.append(f"{q.get('qid')}: 直接聞く対の問い（twin_of）が無い")
    for q in questions:
        if q.get("twin_of") and q["twin_of"] not in ids:
            problems.append(f"{q.get('qid')}: twin_of の先 {q['twin_of']} が無い")
    ng = [r for r in results if r["errors"]]
    by = {}
    for r in results:
        by.setdefault(r["type"], [0, 0])
        by[r["type"]][0] += not r["errors"]
        by[r["type"]][1] += 1
    print("== まとめ: 問い " + "、".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(by.items())) + f"。文書の問題 {len(problems)} 件")
    for p in problems:
        print("  問題:", p)
    if args.json:
        json.dump({"chunks": len(chunks), "questions": results, "problems": problems}, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    sys.exit(1 if ng or problems else 0)


if __name__ == "__main__":
    main()
