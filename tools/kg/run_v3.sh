#!/usr/bin/env bash
# Markdown 経路（PDF_CHUNK_STRATEGY=markdown）で 6 文書を 2 つ目の Neo4j（database2）へ取り込み直し、
# 規定レコードのグラフを作って 16 問を流す。database2 の中身は最初に消す。
#   使い方: run_v3.sh [トークン数=512] [重なり=50]   （tools/kg で実行すること）
#   前提: backend・gemma4・埋め込み・リランカーが稼働中。
set -u
TOK=${1:-512}; OVL=${2:-50}
K=/home/uchida/ai-stack/llm-graph-builder/tools/kg; L=$K/v3; mkdir -p $L/pdf
B=http://localhost:8440
E=(-e NEO4J_URI=neo4j://database2:7687)
CRED=(-F uri=neo4j://database2:7687 -F userName=neo4j -F password=kgb-please-change -F database=neo4j -F email=test@example.com)
D=/home/uchida/docs/試運転要領
declare -a NAMES=("VTR-T_発行目録.pdf" "VTR-T003D.pdf" "HS-VTR-T001E.pdf" "HS-VTR-T002F.pdf" "VTR-T004C.pdf" "VTR-T005B.pdf")
declare -a SRCS=("$D/試運転検査基準(HS-VTR-T)発行目録xdw.pdf" "$D/VTR-T003 D.pdf" "$D/HS-VTR-T001E.pdf" "$D/HS-VTR-T002F.pdf" "$D/VTR-T004C.pdf" "$D/VTR-T005B.pdf")
echo "== 0) database2 を空にする $(date +%T)"
docker exec neo4j-kgb2 cypher-shell -u neo4j -p kgb-please-change "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 500 ROWS" 2>&1 | tail -1
docker exec backend rm -f /tmp/req_extract.json
echo "== 1) 取り込み (token_chunk_size=$TOK) $(date +%T)"
for i in 0 1 2 3 4 5; do fn=${NAMES[$i]}; cp "${SRCS[$i]}" "$L/pdf/$fn"
  curl -s --max-time 120 -X POST $B/upload -F "file=@$L/pdf/$fn" -F chunkNumber=1 -F totalChunks=1 -F originalname="$fn" -F model=gemma4 "${CRED[@]}" >/dev/null
  t0=$(date +%s); r=$(curl -s --max-time 3000 -X POST $B/extract "${CRED[@]}" -F model=gemma4 -F source_type="local file" -F file_name="$fn" -F source_url= -F allowedNodes= -F allowedRelationship= -F token_chunk_size=$TOK -F chunk_overlap=$OVL -F chunks_to_combine=1 -F language= -F retry_condition= -F additional_instructions= -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m)
  echo "[$fn] $(echo "$r" | python3 -c 'import json,sys;d=json.load(sys.stdin);x=d.get("data") or {};print(d.get("status"),"| chunks",x.get("total_chunks"),"| nodes",x.get("nodeCount"),"| rels",x.get("relationshipCount"),"| status",x.get("status"),"|",(d.get("message") or "")[:80])') ($(( $(date +%s)-t0 ))s)"
done
echo "== 2) 規定レコードの抽出 (gemma4) $(date +%T)"
docker exec "${E[@]}" -i backend python - < $K/req_extract.py > $L/extract.log 2>&1; tail -1 $L/extract.log
docker cp backend:/tmp/req_extract.json $L/req_extract_v3.json
echo "== 3) グラフの組み立て $(date +%T)"
docker exec "${E[@]}" -i backend python - clean < $K/req_materialize.py 2>&1 | grep -vE 'notification|Warning' | tail -2
docker exec "${E[@]}" -i backend python - p4 < $K/req_materialize.py 2>&1 | grep -vE 'notification|Warning' | tail -3
echo "== 4) エンティティの埋め込みと索引 $(date +%T)"
curl -s --max-time 900 -X POST $B/post_processing "${CRED[@]}" -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m -F 'tasks=["materialize_entity_similarities","enable_hybrid_search_and_fulltext_search_in_bloom"]' | head -c 160; echo
echo "== 5) 16 問 $(date +%T)"
cd $K && GB_URI=neo4j://database2:7687 python3 $K/run_settings.py ${TRACE_NAME:-v3_markdown} '{}' | tail -17
echo "== 完了 $(date +%T)"
