"""HS 基準の問い（questions_hs.json）を、検索の条件を変えて Graph Builder のチャットに流し、型ごとの正答を出す。

synth/eval_synth.py の写し。違いは次のとおり。
  - 問いのファイルと Neo4j を引数で選ぶ（--questions、--db）。問いの文書名（元のファイル名）は docs.json で取り込み名に読み替える
  - 条件ごとにチャットのモードも切り替える。候補の取り方（ベクトルと全文索引で 20 件 → リランカーで 6 件）はどの条件も同じ
      本文だけ     … fulltext モード（チャンクの本文だけ）。参照リンクはたどらない
      参照あり     … fulltext モード＋参照リンク（参照の一文で選ぶ・2 回たどる・参照元の直後に置く・回答の指示に参照の扱い）
      グラフあり   … graph_vector_fulltext モード（本文＋選んだチャンクのエンティティから広げたエンティティとリレーションの行）
      参照＋グラフ … graph_vector_fulltext モード＋参照リンク
  - 集約（agg）は answer_items（基準ごとに、番号か部品名のどれか）を何本挙げたかで採点する。全部挙げたら正答
使い方:
  python3 eval_hs.py <グラフの書き出し> <出力> --db database4 [--settings 本文だけ,参照あり,グラフあり,参照＋グラフ] [--control-settings 本文だけ]
"""
import argparse
import glob
import json
import os
import pathlib
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

HERE = pathlib.Path(__file__).parent
BACKEND = os.environ.get("GB_BACKEND", "http://localhost:8440")   # 11/9: 評価用の backend に向けられるように
CRED = {"uri": "neo4j://database4:7687", "userName": "neo4j", "password": "kgb-please-change", "database": "neo4j", "email": "test@example.com"}
REF2 = {"reference_max": 2, "reference_select": "both", "reference_depth": 2, "reference_presentation": "inline", "reference_prompt": True}
SETTINGS = {
    "本文だけ": ("fulltext", {"reference_max": 0}),
    "参照あり": ("fulltext", REF2),
    "グラフあり": ("graph_vector_fulltext", {"reference_max": 0}),
    "参照＋グラフ": ("graph_vector_fulltext", REF2),
    # 11/9 追加: 独立した検索方式「参照追跡」（方式の既定値で、章・節と文書層の参照もたどる。上書きはしない）
    "参照追跡": ("graph_vector_fulltext_ref", {}),
    # 11/9: 文書だけを指す参照の行き先を要点の一覧から LLM に選ばせる
    "参照追跡一覧": ("graph_vector_fulltext_ref", {"reference_doc_select": "summary_list"}),
}
ROUTE = ("ref1", "ref2", "ent1", "ent2")
REFUSAL = re.compile(r"(記載|情報|記述|明記|言及|定め|規定|データ|内容|数値|値).{0,24}(ありません|ございません|無い|ない|なし|見当たりません|見つかりません|含まれていません|含まれておりません|されていません|されておりません|確認できません|存在しません)"
                     r"|わかりません|分かりません|お答えできません|お答えすることができません|回答できません|特定できません|判断できません|見当たりません|書かれていない|書かれていません")
EXTERNAL = []  # HS では使わない
_UNUSED = [unicodedata.normalize("NFKC", str(x["id"])).replace(" ", "") for x in json.load(open(HERE / "world.json", encoding="utf-8")).get("external_docs", [])] if (HERE / "world.json").exists() else []


def unanswerable_ok(answer: str) -> dict:
    """答えが文書群に無い問いの判定（言い回しの照合による簡易判定）。

    断り … 「書かれていない」「記載がない」と述べている。
    委ね … 手元に無い文書（world.json の external_docs）の名前を挙げて、そちらによると答えている。
    どちらかなら正答とする。値を作っていないかまでは見ていないので、結果は抜き取りで確かめること。
    """
    text = unicodedata.normalize("NFKC", answer or "")
    refused = bool(REFUSAL.search(text))
    deferred = any(name and name in text.replace(" ", "") for name in EXTERNAL)
    return {"refused": refused, "deferred": deferred, "ok": refused or deferred}


def norm(s) -> str:
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"(?<=\d),(?=\d{3})", "", s)          # 桁区切りのカンマを外す（1,079 と 1079 を同じに）
    return re.sub(r"\s+", "", s)


def has_key(text: str, key: str) -> bool:
    """text、key とも norm 済み。数字で始まる・終わる語は、前後が数字や小数点でない位置だけを数える"""
    start = 0
    while key:
        i = text.find(key, start)
        if i < 0:
            return False
        before = text[i - 1] if i > 0 else ""
        j = i + len(key)
        after = text[j] if j < len(text) else ""
        after2 = text[j + 1] if j + 1 < len(text) else ""
        if key[0].isdigit() and (before.isdigit() or (before == "." and i > 1 and text[i - 2].isdigit())):
            start = i + 1
            continue
        if key[-1].isdigit() and (after.isdigit() or (after == "." and after2.isdigit())):
            start = i + 1
            continue
        return True
    return False


def contains(quote: str, text: str, share: float = 0.7) -> bool:
    q = norm(quote)
    if not q:
        return False
    if q in text:
        return True
    m = SequenceMatcher(None, q, text, autojunk=False).find_longest_match(0, len(q), 0, len(text))
    return m.size >= max(10, int(len(q) * share))


def graph_lines(context: str) -> str:
    return "\n".join(re.findall(r"(?:Entities:|Relationships:)(.*?)(?=Document start|Text Content:|\Z)", context or "", flags=re.S))


def call(question: str, setting, llm: bool, tag: str) -> dict:
    mode, settings = setting
    form = {**CRED, "question": question, "document_names": "[]", "mode": mode, "model": "gemma4",
            "embedding_provider": "vllm", "embedding_model": "cl-nagoya/ruri-v3-310m", "retrieval_settings": json.dumps(settings)}
    if llm:
        form.update({"session_id": tag, "history": "false"})
    req = urllib.request.Request(BACKEND + ("/chat_bot" if llm else "/retrieve_context"), data=urllib.parse.urlencode(form).encode())
    d = json.load(urllib.request.urlopen(req, timeout=900))
    if d.get("status") != "Success":
        raise RuntimeError(str(d)[:300])
    x = d["data"]
    if llm:
        info = x.get("info") or {}
        ids = [c.get("id") for c in ((info.get("nodedetails") or {}).get("chunkdetails") or [])]
        return {"answer": (x.get("message") or "").strip(), "chunk_ids": ids, "context": (info.get("metric_details") or {}).get("contexts") or "", "via": []}
    return {"answer": "", "chunk_ids": [c["id"] for c in x["chunks"]], "context": x.get("context") or "", "via": [c["id"] for c in x["chunks"] if c.get("via_reference")]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("out")
    ap.add_argument("--settings", default="本文だけ,参照あり,グラフあり,参照＋グラフ")
    ap.add_argument("--types", default="")
    ap.add_argument("--control-settings", default="本文だけ", help="control に使う設定（時間の節約）")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--questions", default=str(HERE / "questions_hs.json"))
    ap.add_argument("--db", default="database3", help="検索に使う Neo4j の別名（database3 / database5 / database6 ...）。書き出し JSON と同じグラフを指すこと")
    args = ap.parse_args()
    CRED["uri"] = f"neo4j://{args.db}:7687"
    llm = not args.no_llm

    dump = json.load(open(args.dump, encoding="utf-8"))
    docs = json.load(open(HERE.parent / "docs.json", encoding="utf-8"))
    rename = {pathlib.Path(x["src"]).name: x["name"] for x in docs}
    chunks = {c["id"]: dict(c, n=norm(c["text"])) for c in dump["chunks"]}
    by_doc = {}
    for c in chunks.values():
        by_doc.setdefault(c["fileName"], []).append(c)
    refs = {}
    for r in dump.get("refers_to", []):
        refs.setdefault(r["source"], set()).add(r["target"])

    questions = []
    for path in sorted(glob.glob(args.questions)):
        data = json.load(open(path, encoding="utf-8"))
        questions += [q for q in (data["questions"] if isinstance(data, dict) else data) if q.get("status") != "dropped"]
    wanted = set(args.types.split(",")) if args.types else None
    if wanted:
        questions = [q for q in questions if q["type"] in wanted]
    if args.limit:
        questions = questions[:args.limit]
    names = args.settings.split(",")
    light = args.control_settings.split(",")
    done = {}
    if pathlib.Path(args.out).exists():
        done = {r["qid"]: r for r in json.load(open(args.out, encoding="utf-8"))["questions"]}

    out = []
    for q in questions:
        rec = done.get(q["qid"]) or {"qid": q["qid"], "type": q["type"], "twin_of": q.get("twin_of"), "question": q["question"], "answer_keys": q.get("answer_keys") or [], "runs": {}}
        find = lambda doc, quote: [c["id"] for c in by_doc.get(rename.get(doc or "", doc or ""), []) if contains(quote, c["n"])]
        items = [[norm(a) for a in alts] for alts in q.get("answer_items") or []]
        evidence = [find(e["doc"], e["quote"]) for e in q.get("evidence") or []]
        keys = [norm(k) for k in q.get("answer_keys") or [] if norm(k)]
        entry = find(q.get("entry_doc"), q.get("entry_quote"))
        answer = find(q.get("answer_doc"), q.get("answer_quote")) if q.get("answer_doc") else []
        strict = [i for i in answer if all(has_key(chunks[i]["n"], k) for k in keys)] or answer
        mids = [i for step in q.get("path") or [] for i in find(step.get("doc"), step.get("quote"))]
        rec.update({"entry_chunks": [f"{chunks[i]['fileName']}#{chunks[i]['position']}" for i in entry],
                    "answer_chunks": [f"{chunks[i]['fileName']}#{chunks[i]['position']}" for i in strict],
                    "link_entry_to_answer": any(set(strict) & refs.get(i, set()) for i in entry),
                    "link_entry_to_mid": any(set(mids) & refs.get(i, set()) for i in entry) if mids else None,
                    "link_mid_to_answer": any(set(strict) & refs.get(i, set()) for i in mids) if mids else None})
        use = names if q["type"] in ROUTE or q["type"] in ("confusable", "agg") else [n for n in names if n in light] or light[:1]
        for name in use:
            if name in rec["runs"]:
                continue
            t0 = time.time()
            try:
                r = call(q["question"], SETTINGS[name], llm, f"synth-{q['qid']}-{int(t0)}")
            except Exception as e:
                print(f"{q['qid']} {name} 失敗: {str(e)[:120]}", flush=True)
                continue
            ids = r["chunk_ids"]
            text = norm(r["answer"])
            ctx_chunks = norm(" ".join(chunks[i]["text"] for i in ids if i in chunks))
            lines = norm(graph_lines(r["context"]))
            run = {"entry_in": bool(set(entry) & set(ids)), "answer_in": bool(set(strict) & set(ids)), "mid_in": bool(set(mids) & set(ids)) if mids else None,
                   "answer_rank": min((ids.index(i) + 1 for i in strict if i in ids), default=None), "entry_rank": min((ids.index(i) + 1 for i in entry if i in ids), default=None),
                   "answer_via_reference": bool(set(strict) & set(r["via"])), "chunks": len(ids),
                   "keys_in_chunk_text": bool(keys) and all(has_key(ctx_chunks, k) for k in keys), "keys_in_graph_lines": bool(keys) and all(has_key(lines, k) for k in keys),
                   "context_chars": len(r["context"]), "sec": round(time.time() - t0, 1)}
            if llm:
                wrong = [norm(w) for w in q.get("wrong_values") or [] if norm(w)]
                run["answer"] = r["answer"]
                if q["type"] == "agg":
                    found = [any(has_key(text, a) for a in alts) for alts in items]
                    run["items_found"] = sum(found); run["items_total"] = len(items)
                    run["correct"] = bool(items) and all(found)
                elif q["type"] == "unanswerable":
                    judged = unanswerable_ok(r["answer"])
                    run["correct"], run["deferred"] = judged["ok"], judged["deferred"]
                else:
                    run["correct"] = bool(keys) and all(has_key(text, k) for k in keys)
                run["keys_in_answer"] = run["correct"] if q["type"] != "unanswerable" else None
            if q["type"] == "agg":
                run["evidence_docs_in"] = sum(1 for ev in evidence if set(ev) & set(ids)); run["evidence_docs"] = len(evidence)
                mentions_wrong = any(has_key(text, w) for w in wrong)
                if q["type"] == "confusable" and mentions_wrong:
                    # きょうだいの値を並べただけの回答は、正しいものを選べていないので正答にしない
                    run["correct"] = False
                run["confused"] = (not run["correct"]) and mentions_wrong
                run["refused"] = bool(REFUSAL.search(unicodedata.normalize("NFKC", r["answer"])))
            rec["runs"][name] = run
            mark = ("○" if run.get("correct") else "×") if llm else "-"
            print(f"{q['qid']:9s} {q['type']:12s} {name:12s} {mark} 起点={int(run['entry_in'])} 答え={int(run['answer_in'])} 順位={run['answer_rank']} 本文に語={int(run['keys_in_chunk_text'])} 行に語={int(run['keys_in_graph_lines'])} "
                  f"{run['sec']:5.1f}s | {' '.join(run.get('answer', '').split())[:60]}", flush=True)
        out.append(rec)
        json.dump({"questions": out}, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n== 型 × 設定（n、起点が文脈に、答えのチャンクが文脈に、答えの語が本文に、答えの語がグラフの行に" + ("、正答、取り違え" if llm else "") + "）")
    table = {}
    for rec in out:
        t = "control(対)" if rec["twin_of"] else rec["type"]
        for name, run in rec["runs"].items():
            a = table.setdefault((t, name), {"n": 0, "entry": 0, "answer": 0, "text": 0, "lines": 0, "correct": 0, "confused": 0})
            a["n"] += 1
            a["entry"] += run["entry_in"]
            a["answer"] += run["answer_in"]
            a["text"] += run["keys_in_chunk_text"]
            a["lines"] += run["keys_in_graph_lines"]
            a["correct"] += bool(run.get("correct"))
            a["confused"] += bool(run.get("confused"))
    agg = {}
    for rec in out:
        if rec["type"] != "agg":
            continue
        for name, run in rec["runs"].items():
            a = agg.setdefault(name, {"found": 0, "total": 0, "ev": 0, "evt": 0})
            a["found"] += run.get("items_found", 0); a["total"] += run.get("items_total", 0)
            a["ev"] += run.get("evidence_docs_in", 0); a["evt"] += run.get("evidence_docs", 0)
    for name, a in agg.items():
        print(f"集約     {name:12s} 挙げた基準 {a['found']}/{a['total']}  文脈に入った証拠の基準 {a['ev']}/{a['evt']}")
    for (t, name), a in sorted(table.items()):
        print(f"{t:13s} {name:12s} n={a['n']:3d} 起点 {a['entry']:3d} 答え {a['answer']:3d} 本文 {a['text']:3d} 行 {a['lines']:3d}" + (f" 正答 {a['correct']:3d} 取り違え {a['confused']:3d}" if llm else ""))


if __name__ == "__main__":
    main()
