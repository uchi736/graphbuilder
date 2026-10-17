#!/usr/bin/env bash
# 使い方: run_eval.sh <mode> [document_names JSON]  → eval_<mode>.jsonl
S=${EVAL_DIR:-/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/std}; B=http://localhost:8440
mode=${1:-graph_vector_fulltext}; docs=${2:-[]}; out=$S/eval_${QTAG:-single}_${mode}.jsonl; : > $out
python3 -c 'import json,sys;[print(q["id"]+"\t"+q["q"]+"\t"+q["gold"]) for q in json.load(open(sys.argv[1],encoding="utf-8"))]' "${QFILE:-$S/eval_questions.json}" | while IFS=$'\t' read -r id q gold; do
  t0=$(date +%s.%N); tmp=$(mktemp)
  curl -s --max-time 300 -X POST $B/chat_bot -F uri=neo4j://database:7687 -F userName=neo4j -F password=kgb-please-change -F database=neo4j -F email=test@example.com -F model=gemma4 -F mode=$mode -F "document_names=$docs" -F session_id=eval-$mode-$id -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m -F "question=$q" > "$tmp"
  python3 - "$id" "$q" "$gold" "$t0" "$out" "$tmp" <<'PY'
import json,sys,time
id,q,gold,t0,out,tmp=sys.argv[1:7]; raw=open(tmp,encoding="utf-8",errors="replace").read()
try:
    d=json.loads(raw); x=d.get("data") or {}; i=x.get("info") or {}
    rec={"id":id,"q":q,"gold":gold,"answer":(x.get("message") or "").strip(),"sources":i.get("sources"),"sec":round(time.time()-float(t0),1)}
except Exception:
    rec={"id":id,"q":q,"gold":gold,"answer":"<ERROR> "+raw[:200],"sources":None,"sec":0}
open(out,"a",encoding="utf-8").write(json.dumps(rec,ensure_ascii=False)+"\n")
print(f"  {id} {rec['sec']:>5}s src={rec['sources']} | {rec['answer'][:70].replace(chr(10),' ')}")
PY
  rm -f "$tmp"
done
echo "saved: $out ($(wc -l < $out) 件)"
