"""判定を通した問いを、検索の設定を変えて LLM に答えさせる。

使い方: python3 answer_questions.py <判定の JSON（filter_questions.py の出力）> <出力の JSON> [採用,境目,...]
前提: バックエンド（:8440）、gemma4、埋め込み、リランカーが稼働中。1 問ずつ順に呼ぶ（並列にしない）。

設定:
  上位6        … 既定。質問で選んだ上位 6 件だけ
  上位20       … 件数を増やす。候補 20 件をすべて渡す
  上位6+参照2  … 上位 6 件から参照リンクをたどり、参照先を 2 件まで足す（この配備の既定）
  上位6+参照10 … 同、10 件まで
採点: 答えの語（answer_keys）がすべて回答に入っていれば正答
"""
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

BACKEND = "http://localhost:8440"
CRED = {"uri": "neo4j://database2:7687", "userName": "neo4j", "password": "kgb-please-change", "database": "neo4j", "email": "test@example.com"}
SETTINGS = {
    "上位6": {"reference_max": 0},
    "上位20": {"rerank_top_n": 20, "candidate_k": 20, "reference_max": 0},
    "上位6+参照2": {"reference_max": 2},
    "上位6+参照10": {"reference_max": 10},
}
norm = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")))


def ask(question: str, settings: dict, tag: str) -> dict:
    data = urllib.parse.urlencode({**CRED, "model": "gemma4", "mode": "graph_vector_fulltext", "document_names": "[]", "session_id": tag, "history": "false",
                                   "embedding_provider": "vllm", "embedding_model": "cl-nagoya/ruri-v3-310m", "question": question,
                                   "retrieval_settings": json.dumps(settings)}).encode()
    d = json.load(urllib.request.urlopen(urllib.request.Request(BACKEND + "/chat_bot", data=data), timeout=600))
    x = d.get("data") or {}
    info = x.get("info") or {}
    return {"answer": (x.get("message") or "").strip(), "context_chars": len((info.get("metric_details") or {}).get("contexts") or "")}


def main():
    rows = json.load(open(sys.argv[1], encoding="utf-8"))["questions"]
    wanted = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else {"採用", "境目"}
    rows = [r for r in rows if r["verdict"] in wanted or (r["type"] == "control" and "対照" in wanted)]
    out = []
    for r in rows:
        keys = [norm(k) for k in r["answer_keys"] if norm(k)]
        rec = dict(r, answers={}, correct={})
        for name, settings in SETTINGS.items():
            t0 = time.time()
            a = ask(r["question"], settings, f"route-{r['no']}-{int(t0)}")
            text = norm(a["answer"])
            rec["answers"][name] = a["answer"]
            rec["correct"][name] = bool(keys) and all(k in text for k in keys)
            print(f"{r['no']:2d} [{r['type']:9s} {r['verdict']}] {name:12s} {'○' if rec['correct'][name] else '×'} {time.time() - t0:5.1f}s {a['context_chars']:6d}字 | "
                  f"{' '.join(a['answer'].split())[:70]}", flush=True)
        out.append(rec)
        json.dump({"questions": out}, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for name in SETTINGS:
        by = {}
        for rec in out:
            key = (rec["type"], rec["verdict"])
            by.setdefault(key, [0, 0])
            by[key][0] += rec["correct"][name]
            by[key][1] += 1
        print(name, {f"{k[0]}/{k[1]}": f"{v[0]}/{v[1]}" for k, v in by.items()})


if __name__ == "__main__":
    main()
