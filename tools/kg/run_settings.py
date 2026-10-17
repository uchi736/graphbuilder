"""検索設定を指定して 16 問を流し、回答・文脈・適用された設定を記録する。使い方: python3 run_settings.py <tag> '<設定JSON>'"""
import json, os, sys, time, urllib.request, urllib.parse
tag = sys.argv[1]; settings = sys.argv[2] if len(sys.argv) > 2 else "{}"
B = "http://localhost:8440"
CRED = {"uri": os.environ.get("GB_URI", "neo4j://database:7687"), "userName": "neo4j", "password": "kgb-please-change", "database": "neo4j", "email": "test@example.com"}
qs = json.load(open("eval_multihop.json", encoding="utf-8")); out = []
for q in qs:
    t0 = time.time()
    data = urllib.parse.urlencode({**CRED, "model": "gemma4", "mode": "graph_vector_fulltext", "document_names": "[]", "session_id": f"set-{tag}-{q['id']}-{int(t0)}",
                                   "embedding_provider": "vllm", "embedding_model": "cl-nagoya/ruri-v3-310m", "question": q["q"], "retrieval_settings": settings}).encode()
    d = json.load(urllib.request.urlopen(urllib.request.Request(B + "/chat_bot", data=data), timeout=600))
    x = d.get("data") or {}; info = x.get("info") or {}; ctx = ((info.get("metric_details") or {}).get("contexts") or "")
    rec = {"id": q["id"], "q": q["q"], "gold": q["gold"], "answer": (x.get("message") or "").strip(), "context": ctx, "context_chars": len(ctx),
           "settings": info.get("retrieval_settings"), "sec": round(time.time() - t0, 1)}
    out.append(rec); print(q["id"], rec["sec"], "s", len(ctx), "chars |", " ".join(rec["answer"].split())[:90], flush=True)
json.dump(out, open(f"trace_{tag}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
