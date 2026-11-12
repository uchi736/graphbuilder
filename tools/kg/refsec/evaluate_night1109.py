"""11/9 夜間版（evaluate.py の写し）。作り直した本番グラフで、既定の検索と参照追跡の方式の 2 条件の検索と回答を取る（X20）。
元の説明: 採用した問いで、3 条件（現行／段 1 章・節／段 1＋段 2 文書層）の検索と回答を取る（X18）。判定は別に行う（judge 用の材料を書き出す）。

使い方: python3 evaluate.py filtered.json results.json
前提: 本番の backend（:8440）。設定は 1 問ごとの上書き（retrieval_settings）で切り替え、本番の既定は変えない。
会話の履歴は使わず（history=false）、問いごとに別の会話 ID を使い、最後に /clear_chat_bot で消す。
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BACKEND = os.environ.get("GB_BACKEND", "http://localhost:8440")   # 11/9: 評価用の backend に向けられるように
# 条件 = (検索方式, 画面の上書き)。既定の検索は参照をたどらない（11/8 の方式の分離後）、参照追跡は方式の既定値でたどる
CONDITIONS = {
    "default": ("graph_vector_fulltext", {}),
    "ref_mode": ("graph_vector_fulltext_ref", {}),
    # 11/9: 参照追跡で、文書だけを指す参照の行き先を要点の一覧から LLM に選ばせる（reference_doc_select=summary_list）
    "ref_list": ("graph_vector_fulltext_ref", {"reference_doc_select": "summary_list"}),
}
# 流す条件を環境変数 EVAL_CONDITIONS（カンマ区切り）で選べる。既定は従来の 2 条件
CONDITIONS = {k: v for k, v in CONDITIONS.items() if k in (os.environ.get("EVAL_CONDITIONS") or "default,ref_mode").split(",")}
# 11/9: 評価用 DB を明示できるように（GB_URI・GB_USER・GB_PASSWORD・GB_DATABASE）
COMMON = {"uri": os.environ.get("GB_URI", ""), "userName": os.environ.get("GB_USER", ""), "password": os.environ.get("GB_PASSWORD", ""), "database": os.environ.get("GB_DATABASE", ""), "document_names": "[]", "mode": "graph_vector_fulltext",
          "model": "gemma4", "embedding_provider": "vllm", "embedding_model": "cl-nagoya/ruri-v3-310m"}


def post(path, fields, timeout=300):
    data = urllib.parse.urlencode({**COMMON, **fields}).encode()
    return json.load(urllib.request.urlopen(urllib.request.Request(BACKEND + path, data=data), timeout=timeout))


def main():
    import os
    qs = [q for q in json.load(open(sys.argv[1], encoding="utf-8"))["questions"] if os.environ.get("ALL") or q["verdict"] == "採用"]
    out = []
    for n, q in enumerate(qs, 1):
        rec = {k: q.get(k) for k in ("id", "type", "question", "answer", "answer_keys", "answer_chunk_ids", "entry_chunk_ids", "ref", "verdict", "answer_in_top6")}
        rec["runs"] = {}
        for name, (mode, settings) in CONDITIONS.items():
            r = post("/retrieve_context", {"question": q["question"], "mode": mode, "retrieval_settings": json.dumps(settings)})["data"]
            context_ids = [c["id"] for c in r["chunks"]]
            session = f"x20-{q['id']}-{name}-{int(time.time())}"
            t0 = time.time()
            for attempt in range(3):   # 11/9: 回答 API が一時的に失敗（data が文字列）したら送り直す
                a = post("/chat_bot", {"question": q["question"], "session_id": f"{session}-{attempt}", "email": "", "history": "false",
                                       "mode": mode, "retrieval_settings": json.dumps(settings)})
                if isinstance(a.get("data"), dict):
                    break
                print(f"  {q['id']} {name} 回答の失敗（{attempt + 1} 回目）: {str(a.get('data') or a.get('error'))[:160]}", flush=True)
                time.sleep(10)
            elapsed = time.time() - t0
            try:
                post("/clear_chat_bot", {"session_id": session}, timeout=30)
            except Exception:
                pass
            data = a.get("data") if isinstance(a.get("data"), dict) else {}
            rec["runs"][name] = {
                "answer": data.get("message"), "elapsed": round(elapsed, 2),
                "context_chars": len(r.get("context") or ""), "context_chunk_ids": context_ids,
                "answer_in_context": bool(set(q.get("answer_chunk_ids") or []) & set(context_ids)),
                "references": [{"chunk_id": x.get("chunk_id"), "kind": x.get("target_kind"), "file": x.get("file_name")} for x in (r.get("references") or [])],
            }
        out.append(rec)
        runs = rec["runs"]
        print(f"{n:3} {q['id']:5} {q['type']:15} 文脈に答え " + " / ".join(f"{k}:{'○' if v['answer_in_context'] else '×'}" for k, v in runs.items()), flush=True)
        json.dump({"results": out}, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
