#!/usr/bin/env bash
# 架空の文書群（synth/docs/*/*.md）を、3 つ目の Neo4j（neo4j-kgb3、別名 database3）へ取り込む。
#   使い方: ingest_synth.sh [トークン数=512] [重なり=50] [ファイル名の絞り込み（正規表現）]
#   前提: backend・gemma4・埋め込みが稼働中。取り込み済みの文書（Document があり状態が Completed）は飛ばす。
#   実物の 6 文書のグラフ（database2）には触れない。
set -u
TOK=${1:-512}; OVL=${2:-50}; ONLY=${3:-.}
K=/home/uchida/ai-stack/llm-graph-builder/tools/kg/synth
B=http://localhost:8440
CRED=(-F uri=neo4j://database3:7687 -F userName=neo4j -F password=kgb-please-change -F database=neo4j -F email=test@example.com)
LOG=$K/ingest.log
done_names=$(docker exec neo4j-kgb3 cypher-shell -u neo4j -p kgb-please-change --format plain "MATCH (d:Document) WHERE d.status = 'Completed' RETURN d.fileName" 2>/dev/null | tr -d '"')
echo "== 取り込み開始 $(date +%T) token_chunk_size=$TOK overlap=$OVL" | tee -a "$LOG"
for f in $(ls $K/docs/*/*.md | sort); do
  fn=$(basename "$f")
  echo "$fn" | grep -Eq "$ONLY" || continue
  if echo "$done_names" | grep -qx "$fn"; then echo "[$fn] 取り込み済み。飛ばす" | tee -a "$LOG"; continue; fi
  curl -s --max-time 120 -X POST $B/upload -F "file=@$f" -F chunkNumber=1 -F totalChunks=1 -F originalname="$fn" -F model=gemma4 "${CRED[@]}" >/dev/null
  t0=$(date +%s)
  r=$(curl -s --max-time 6000 -X POST $B/extract "${CRED[@]}" -F model=gemma4 -F source_type="local file" -F file_name="$fn" -F source_url= -F allowedNodes= -F allowedRelationship= -F token_chunk_size=$TOK -F chunk_overlap=$OVL -F chunks_to_combine=1 -F language= -F retry_condition= -F additional_instructions= -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m)
  echo "[$fn] $(echo "$r" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); x=d.get("data") or {}
    print(d.get("status"),"| chunks",x.get("total_chunks"),"| nodes",x.get("nodeCount"),"| rels",x.get("relationshipCount"),"| status",x.get("status"),"|",(d.get("message") or "")[:80])
except Exception as e:
    print("応答を読めない:", e)') ($(( $(date +%s)-t0 ))s) $(date +%T)" | tee -a "$LOG"
done
echo "== 取り込み終了 $(date +%T)" | tee -a "$LOG"
