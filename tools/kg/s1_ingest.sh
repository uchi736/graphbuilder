#!/usr/bin/env bash
# S0: 検証データ削除(TS3-5は残す)  S1: 試運転要領6本を標準設定(gemma4, 100tok/20)で逐次投入
S=/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/std; B=http://localhost:8440
CRED=(-F uri=neo4j://database:7687 -F userName=neo4j -F password=kgb-please-change -F database=neo4j -F email=test@example.com)
CY() { docker exec neo4j-kgb cypher-shell -u neo4j -p kgb-please-change --format plain "$1" 2>/dev/null | tail -n +2; }
echo "== S0 削除 =="
docs=$(CY "MATCH (d:Document) WHERE d.fileName IS NOT NULL AND d.fileName <> 'TS3-5.pdf' RETURN d.fileName" | tr -d '"' | grep -v '^$' | python3 -c 'import json,sys;print(json.dumps([l.strip() for l in sys.stdin if l.strip()],ensure_ascii=False))')
n=$(python3 -c "import json;print(len(json.loads('$docs')))"); if [ "$n" -gt 0 ]; then types=$(python3 -c "import json;print(json.dumps(['local file']*$n))"); curl -s --max-time 300 -X POST $B/delete_document_and_entities "${CRED[@]}" -F "filenames=$docs" -F "source_types=$types" -F deleteEntities=true | head -c 90; echo; fi
CY "MATCH (e:__Entity__) WHERE NOT (e)<-[:HAS_ENTITY]-(:Chunk) DETACH DELETE e" >/dev/null
echo "  残エンティティ: $(CY 'MATCH (e:__Entity__) RETURN count(e)')"
echo "== S1 標準投入 (gemma4, chunk 100/20) =="
declare -a NAMES=("HS-VTR-T001E.pdf" "HS-VTR-T002F.pdf" "VTR-T003D.pdf" "VTR-T004C.pdf" "VTR-T005B.pdf" "VTR-T_発行目録.pdf")
declare -a SRCS=("/home/uchida/docs/試運転要領/HS-VTR-T001E.pdf" "/home/uchida/docs/試運転要領/HS-VTR-T002F.pdf" "/home/uchida/docs/試運転要領/VTR-T003 D.pdf" "/home/uchida/docs/試運転要領/VTR-T004C.pdf" "/home/uchida/docs/試運転要領/VTR-T005B.pdf" "/home/uchida/docs/試運転要領/試運転検査基準(HS-VTR-T)発行目録xdw.pdf")
T0=$(date +%s)
for i in 0 1 2 3 4 5; do fn=${NAMES[$i]}; cp "${SRCS[$i]}" "$S/$fn"
  curl -s --max-time 120 -X POST $B/upload -F "file=@$S/$fn" -F chunkNumber=1 -F totalChunks=1 -F originalname="$fn" -F model=gemma4 "${CRED[@]}" >/dev/null
  t0=$(date +%s); r=$(curl -s --max-time 2400 -X POST $B/extract "${CRED[@]}" -F model=gemma4 -F source_type="local file" -F file_name="$fn" -F source_url= -F allowedNodes= -F allowedRelationship= -F token_chunk_size=100 -F chunk_overlap=20 -F chunks_to_combine=1 -F language= -F retry_condition= -F additional_instructions= -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m)
  echo "[$fn] $(echo "$r" | python3 -c 'import json,sys;d=json.load(sys.stdin);x=d.get("data") or {};print(d.get("status"),"| chunks",x.get("total_chunks"),"| nodes",x.get("nodeCount"),"| rels",x.get("relationshipCount"),"| status",x.get("status"),"|",(d.get("message") or "")[:60])') ($(( $(date +%s)-t0 ))s)"
done
echo "== S1 完了 総所要 $(( $(date +%s)-T0 ))s =="
