"""架空の文書群の問いを、検索の設定を変えて流し、型ごとに「起点・答えが文脈に入ったか」と「正答したか」を出す。

使い方:
  python3 eval_synth.py <グラフの書き出し JSON> <出力 JSON> [--settings 上位6,上位20,...] [--types ref1,ref2,...] [--no-llm] [--limit N]
前提: backend（:8440）、埋め込み、リランカーが稼働中。--no-llm でなければ gemma4 も使う。1 問ずつ順に呼ぶ（並列にしない）。
グラフの書き出しは routes/dump_graph.py の出力（チャンクの本文から、起点と答えのチャンクを引用で特定する）。

設定:
  上位6        … 既定。質問で選んだ上位 6 件だけ
  上位20       … 件数を増やす。候補 20 件をすべて渡す
  上位50       … さらに増やす（候補 200 件から 50 件）。--no-llm のときに、答えのチャンクの順位を見るのに使う
  上位6+参照2  … 上位 6 件から参照リンクをたどり、参照先を 2 件まで足す
  上位6+参照10 … 同、10 件まで
採点:
  正答     … answer_keys がすべて回答に入っている（数字は前後の桁を見て、19 年の中の 9 年を数えない）。
             confusable は、きょうだいの値（wrong_values）が回答に入っていたら正答にしない（並べただけの回答を除く）
  取り違え … 正答でなく、wrong_values（きょうだいの値）のどれかが回答に入っている
  unanswerable は、回答が「書かれていない」と述べていれば正答（言い回しの照合による簡易判定）
"""
import argparse
import os
import glob
import json
import pathlib
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

HERE = pathlib.Path(__file__).parent
BACKEND = os.environ.get("GB_BACKEND", "http://localhost:8440")   # 11/10（X24）: 評価用の backend（:8446）に向けられるように
CRED = {"uri": "neo4j://database3:7687", "userName": "neo4j", "password": "kgb-please-change", "database": "neo4j", "email": "test@example.com"}
SETTINGS = {
    "上位6": {"reference_max": 0},
    "上位20": {"rerank_top_n": 20, "candidate_k": 20, "reference_max": 0},
    "上位50": {"rerank_top_n": 50, "candidate_k": 200, "candidate_min_score": 0.0, "reference_max": 0},
    "上位6+参照2": {"reference_max": 2},
    "上位6+参照10": {"reference_max": 10},
    # 参照の一文で選ぶ・2 回目もたどる・参照元の直後に置く・回答の指示に参照の扱いを足す（直した後の推す設定）
    "新・参照2": {"reference_max": 2, "reference_select": "both", "reference_depth": 2, "reference_presentation": "inline", "reference_prompt": True},
    # 切り分け用: 選び方だけ新しくする（見せ方と指示は従来）
    "新・選び方のみ": {"reference_max": 2, "reference_select": "both", "reference_depth": 2},
    # 11/10（X24）: 独立した検索方式「参照追跡」（graph_vector_fulltext_ref、方式の既定値）。文書だけを指す参照の行き先の選び方 2 通り
    "参照追跡": {},
    "参照追跡・一覧": {"reference_doc_select": "summary_list"},
}
# 設定ごとの検索方式（無ければ従来の graph_vector_fulltext）
MODES = {"参照追跡": "graph_vector_fulltext_ref", "参照追跡・一覧": "graph_vector_fulltext_ref"}
# 応答の references から控える項目（X24: 一覧選択が働いたか・戻したか）
REF_KEYS = ("chunk_id", "target_kind", "file_name", "position", "hop", "select", "locator", "via_label", "selection", "selection_fallback", "selection_stage", "selected_sections", "summary_coverage")
ROUTE = ("ref1", "ref2", "ent1", "ent2")
REFUSAL = re.compile(r"(記載|情報|記述|明記|言及|定め|規定|データ|内容|数値|値).{0,24}(ありません|ございません|無い|ない|なし|見当たりません|見つかりません|含まれていません|含まれておりません|されていません|されておりません|確認できません|存在しません)"
                     r"|わかりません|分かりません|お答えできません|お答えすることができません|回答できません|特定できません|判断できません|見当たりません|書かれていない|書かれていません")
EXTERNAL = [unicodedata.normalize("NFKC", str(x["id"])).replace(" ", "") for x in json.load(open(HERE / "world.json", encoding="utf-8")).get("external_docs", [])] if (HERE / "world.json").exists() else []


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


def call(question: str, settings: dict, llm: bool, tag: str, mode: str = "graph_vector_fulltext") -> dict:
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
        refs = [{k: e.get(k) for k in REF_KEYS if e.get(k) is not None} for e in (info.get("references") or [])]
        return {"answer": (x.get("message") or "").strip(), "chunk_ids": ids, "context": (info.get("metric_details") or {}).get("contexts") or "", "via": [e.get("chunk_id") for e in refs], "references": refs}
    return {"answer": "", "chunk_ids": [c["id"] for c in x["chunks"]], "context": x.get("context") or "", "via": [c["id"] for c in x["chunks"] if c.get("via_reference")],
            "references": [{k: e.get(k) for k in REF_KEYS if e.get(k) is not None} for e in (x.get("references") or [])]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("out")
    ap.add_argument("--settings", default="上位6,上位20")
    ap.add_argument("--types", default="")
    ap.add_argument("--control-settings", default="上位6", help="control と unanswerable に使う設定（時間の節約）")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--questions", default=str(HERE / "questions" / "*.json"))
    ap.add_argument("--qids", default="", help="流す問いの qid（カンマ区切り、または @ファイル（1 行 1 件））。X24 で絞り込みに使う")
    ap.add_argument("--db", default="database3", help="検索に使う Neo4j の別名（database3 / database5 / database6 ...）。書き出し JSON と同じグラフを指すこと")
    args = ap.parse_args()
    CRED["uri"] = f"neo4j://{args.db}:7687"
    llm = not args.no_llm

    dump = json.load(open(args.dump, encoding="utf-8"))
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
    if args.qids:
        keep = [x.strip() for x in (open(args.qids[1:], encoding="utf-8").read().split() if args.qids.startswith("@") else args.qids.split(",")) if x.strip()]
        questions = [q for q in questions if q["qid"] in set(keep)]
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
        find = lambda doc, quote: [c["id"] for c in by_doc.get(doc or "", []) if contains(quote, c["n"])]
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
        use = names if q["type"] in ROUTE or q["type"] == "confusable" else [n for n in names if n in light] or light[:1]
        for name in use:
            if name in rec["runs"]:
                continue
            t0 = time.time()
            try:
                r = call(q["question"], SETTINGS[name], llm, f"synth-{q['qid']}-{int(t0)}", MODES.get(name, "graph_vector_fulltext"))
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
                   "context_chars": len(r["context"]), "sec": round(time.time() - t0, 1), "references": r.get("references") or []}
            if llm:
                wrong = [norm(w) for w in q.get("wrong_values") or [] if norm(w)]
                run["answer"] = r["answer"]
                if q["type"] == "unanswerable":
                    judged = unanswerable_ok(r["answer"])
                    run["correct"], run["deferred"] = judged["ok"], judged["deferred"]
                else:
                    run["correct"] = bool(keys) and all(has_key(text, k) for k in keys)
                run["keys_in_answer"] = run["correct"] if q["type"] != "unanswerable" else None
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
    for (t, name), a in sorted(table.items()):
        print(f"{t:13s} {name:12s} n={a['n']:3d} 起点 {a['entry']:3d} 答え {a['answer']:3d} 本文 {a['text']:3d} 行 {a['lines']:3d}" + (f" 正答 {a['correct']:3d} 取り違え {a['confused']:3d}" if llm else ""))


if __name__ == "__main__":
    main()
