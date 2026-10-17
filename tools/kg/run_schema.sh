#!/usr/bin/env bash
# 検証の段 2: 人手のスキーマ（EDC の試運転の定義と同じ型 16・種類 17）を、標準の抽出の指示に入れて実物 6 文書を取り込む。
#   使い方: run_schema.sh neo4j-kgb6 database6
#   型と種類は methods/schema/allowed.json（組は EDC の実物のグラフに現れた型の組＋正解の組）。
#   additional_instructions には EDC の試運転の定義の extract_instructions と同じ文を渡す（手本の例文は渡せない）。
#   指定のグラフは最初に空にする。対象は neo4j-kgb6 だけ。tools/kg で実行すること。
set -u
CN=${1:?コンテナ名}; AL=${2:?別名}; MODE=${3:-pairs}; MODEL=gemma4
# MODE: pairs = 種類を（型, 種類, 型）の組で渡す（組に無い線は落とされる）。names = 型だけ渡し、種類の名前は追加の指示に書く（線は落とさない）。
case "$CN" in neo4j-kgb6) ;; *) echo "このグラフは対象にできない: $CN" >&2; exit 2;; esac
K=/home/uchida/ai-stack/llm-graph-builder/tools/kg; L=$K/methods/schema; mkdir -p $L/pdf; [ "$MODE" = names ] && { L=$K/methods/schema_names; mkdir -p $L/pdf; cp $K/methods/schema/allowed.json $L/; }
B=http://localhost:8440
CRED=(-F uri=neo4j://$AL:7687 -F userName=neo4j -F password=kgb-please-change -F database=neo4j -F email=test@example.com)
AN=$(python3 -c "import json;print(json.load(open('$L/allowed.json',encoding='utf-8'))['allowedNodes'])")
AR=$(python3 -c "import json;print(json.load(open('$L/allowed.json',encoding='utf-8'))['allowedRelationship'])")
AI=$(python3 -c "import json;r=json.load(open('/home/uchida/ai-stack/EDC/schemas/registry.json',encoding='utf-8'));print([d for d in r['doctypes'] if d['name']=='試運転_検査基準'][0]['extract_instructions'])")
if [ "$MODE" = names ]; then RN=$(python3 -c "import json;print(', '.join(sorted({p[1] for p in json.load(open('$L/allowed.json',encoding='utf-8'))['pairs']})))"); AR=""; AI="$AI (5) Use ONLY these relationship types and no others: $RN."; fi
D=/home/uchida/docs/試運転要領
declare -a NAMES=("VTR-T_発行目録.pdf" "VTR-T003D.pdf" "HS-VTR-T001E.pdf" "HS-VTR-T002F.pdf" "VTR-T004C.pdf" "VTR-T005B.pdf")
declare -a SRCS=("$D/試運転検査基準(HS-VTR-T)発行目録xdw.pdf" "$D/VTR-T003 D.pdf" "$D/HS-VTR-T001E.pdf" "$D/HS-VTR-T002F.pdf" "$D/VTR-T004C.pdf" "$D/VTR-T005B.pdf")
echo "== 0) $CN を空にする $(date +%T)"
docker exec $CN cypher-shell -u neo4j -p kgb-please-change "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 500 ROWS" 2>&1 | tail -1
echo "== 1) 取り込み model=$MODEL mode=$MODE 型 $(echo "$AN" | tr ',' '\n' | wc -l) 組 $(( $(echo "$AR" | tr ',' '\n' | grep -c .) / 3 )) $(date +%T)"
for i in 0 1 2 3 4 5; do fn=${NAMES[$i]}; cp "${SRCS[$i]}" "$L/pdf/$fn"
  curl -s --max-time 120 -X POST $B/upload -F "file=@$L/pdf/$fn" -F chunkNumber=1 -F totalChunks=1 -F originalname="$fn" -F model=$MODEL "${CRED[@]}" >/dev/null
  t0=$(date +%s); r=$(curl -s --max-time 6000 -X POST $B/extract "${CRED[@]}" -F model=$MODEL -F source_type="local file" -F file_name="$fn" -F source_url= -F "allowedNodes=$AN" -F "allowedRelationship=$AR" -F token_chunk_size=512 -F chunk_overlap=50 -F chunks_to_combine=1 -F language= -F retry_condition= -F "additional_instructions=$AI" -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m)
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
