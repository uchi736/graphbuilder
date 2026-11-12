"""X24 の切り分け（検索だけ、回答なし）。文書だけを指す参照の 14 問で、参照の下限 reference_min_score を 0.3（既定）→ 0.1 に下げると、
一覧選択が働く件数と答えのチャンクの到達がどう変わるか。eval_synth.py の書き出し（graph_dump3.json）で答えのチャンクを引く。
使い方: GB_BACKEND=http://localhost:8446 python3 diag_minscore.py results_x24.json diag_minscore.json
"""
import json, os, sys, time, urllib.parse, urllib.request
from analyze_x24 import DOC_ONLY
B = os.environ.get("GB_BACKEND", "http://localhost:8446")
CRED = {"uri": "neo4j://database3:7687", "userName": "neo4j", "password": os.environ.get("KGB_PW", "kgb-please-change"), "database": "neo4j"}
dump = json.load(open(os.path.join(os.path.dirname(__file__), "../../synth/graph_dump3.json"), encoding="utf-8"))
key = {f"{c['fileName']}#{c['position']}": c["id"] for c in dump["chunks"]}
recs = [r for r in json.load(open(sys.argv[1], encoding="utf-8"))["questions"] if r["qid"] in DOC_ONLY]
conds = {f"{sel}@{ms}": {"reference_doc_select": sel, "reference_min_score": ms} for ms in (0.3, 0.1) for sel in ("rerank", "summary_list")}
out = []
for r in recs:
    ans = {key[a] for a in r["answer_chunks"] if a in key}
    row = {"qid": r["qid"], "answer_chunks": r["answer_chunks"], "runs": {}}
    for name, s in conds.items():
        form = {**CRED, "question": r["question"], "document_names": "[]", "mode": "graph_vector_fulltext_ref", "model": "gemma4",
                "embedding_provider": "vllm", "embedding_model": "cl-nagoya/ruri-v3-310m", "retrieval_settings": json.dumps(s)}
        t0 = time.time()
        d = json.load(urllib.request.urlopen(urllib.request.Request(B + "/retrieve_context", data=urllib.parse.urlencode(form).encode()), timeout=600))["data"]
        ids = [c["id"] for c in d["chunks"]]
        refs = [{k: e.get(k) for k in ("target_kind", "select", "file_name", "position", "hop", "selection", "selection_fallback", "via_label")} for e in d.get("references") or []]
        row["runs"][name] = {"answer_in": bool(ans & set(ids)), "sec": round(time.time() - t0, 1), "references": refs,
                             "summary_list": sum(e.get("selection") == "summary_list" for e in refs)}
        print(r["qid"], name, int(row["runs"][name]["answer_in"]), row["runs"][name]["summary_list"], row["runs"][name]["sec"], flush=True)
    out.append(row)
    json.dump({"questions": out}, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for name in conds:
    print(name, "到達", sum(x["runs"][name]["answer_in"] for x in out), "/", len(out), "一覧選択が働いた問い", sum(x["runs"][name]["summary_list"] > 0 for x in out))
