#!/usr/bin/env bash
# 抽出方式の採点（改造計画の段階 0）のために、実物 6 文書を指定の方式で取り込む。
#   使い方: run_methods.sh <コンテナ名> <別名> <model>    例: run_methods.sh neo4j-kgb4 database4 edc
#   model: edc = スキーマを統制する抽出（文書の種類は自動で振り分け）、gemma4 = 標準の抽出（スキーマの指定なし）
#   指定のグラフは最初に空にする。評価用の元のグラフ（neo4j-kgb）と、検証用の neo4j-kgb2・neo4j-kgb3 は対象にできない。
#   分割は Markdown の経路（512 トークン、重なり 50）。tools/kg で実行すること。
set -u
CN=${1:?コンテナ名}; AL=${2:?別名}; MODEL=${3:?model}; OUT=${4:-$MODEL}
# 4 つ目の引数は出力先の名前（methods/<OUT>）。省略時は model と同じ。
case "$CN" in neo4j-kgb4|neo4j-kgb5|neo4j-kgb6) ;; *) echo "このグラフは対象にできない: $CN" >&2; exit 2;; esac
K=/home/uchida/ai-stack/llm-graph-builder/tools/kg; L=$K/methods/$OUT; mkdir -p $L/pdf
B=http://localhost:8440
CRED=(-F uri=neo4j://$AL:7687 -F userName=neo4j -F password=kgb-please-change -F database=neo4j -F email=test@example.com)
D=/home/uchida/docs/試運転要領
declare -a NAMES=("VTR-T_発行目録.pdf" "VTR-T003D.pdf" "HS-VTR-T001E.pdf" "HS-VTR-T002F.pdf" "VTR-T004C.pdf" "VTR-T005B.pdf")
declare -a SRCS=("$D/試運転検査基準(HS-VTR-T)発行目録xdw.pdf" "$D/VTR-T003 D.pdf" "$D/HS-VTR-T001E.pdf" "$D/HS-VTR-T002F.pdf" "$D/VTR-T004C.pdf" "$D/VTR-T005B.pdf")
echo "== 0) $CN を空にする $(date +%T)"
docker exec $CN cypher-shell -u neo4j -p kgb-please-change "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 500 ROWS" 2>&1 | tail -1
echo "== 1) 取り込み model=$MODEL $(date +%T)"
for i in 0 1 2 3 4 5; do fn=${NAMES[$i]}; cp "${SRCS[$i]}" "$L/pdf/$fn"
  curl -s --max-time 120 -X POST $B/upload -F "file=@$L/pdf/$fn" -F chunkNumber=1 -F totalChunks=1 -F originalname="$fn" -F model=$MODEL "${CRED[@]}" >/dev/null
  t0=$(date +%s); r=$(curl -s --max-time 6000 -X POST $B/extract "${CRED[@]}" -F model=$MODEL -F source_type="local file" -F file_name="$fn" -F source_url= -F allowedNodes= -F allowedRelationship= -F token_chunk_size=512 -F chunk_overlap=50 -F chunks_to_combine=1 -F language= -F retry_condition= -F additional_instructions= -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m)
  echo "[$fn] $(echo "$r" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); x=d.get("data") or {}
    print(d.get("status"),"| chunks",x.get("total_chunks"),"| nodes",x.get("nodeCount"),"| rels",x.get("relationshipCount"),"| status",x.get("status"),"|",(d.get("message") or "")[:100])
except Exception as e:
    print("応答を読めない:", e)') ($(( $(date +%s)-t0 ))s)"
done
echo "== 2) エンティティの埋め込みと索引 $(date +%T)"
curl -s --max-time 900 -X POST $B/post_processing "${CRED[@]}" -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m -F 'tasks=["materialize_entity_similarities","enable_hybrid_search_and_fulltext_search_in_bloom"]' | head -c 120; echo
echo "== 3) 書き出し $(date +%T)"
docker exec -e NEO4J_URI=neo4j://$AL:7687 -i backend python - < $K/routes/dump_graph.py > $L/graph_dump.json 2>/dev/null
python3 -c "import json;d=json.load(open('$L/graph_dump.json',encoding='utf-8'));print({k:len(v) for k,v in d.items()})"
echo "== 完了 $(date +%T)"
