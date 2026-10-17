#!/usr/bin/env bash
# 再構築: 旧X文書を削除 → 実ファイル名で3本をEDC抽出 → 後処理 → 指標
set -u
S=/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad; B=http://localhost:8440
CRED=(-F uri=neo4j://database:7687 -F userName=neo4j -F password=kgb-please-change -F database=neo4j -F email=test@example.com)
CY() { docker exec neo4j-kgb cypher-shell -u neo4j -p kgb-please-change --format plain "$1" 2>/dev/null | tail -n +2; }
echo "== 1) 旧X文書の削除 =="
docs=$(CY "MATCH (d:Document) WHERE d.fileName IS NOT NULL AND d.fileName <> 'TS3-5.pdf' RETURN d.fileName" | tr -d '"' | grep -v '^$' | python3 -c 'import json,sys;print(json.dumps([l.strip() for l in sys.stdin if l.strip()],ensure_ascii=False))')
n=$(python3 -c "import json;print(len(json.loads('$docs')))"); if [ "$n" -gt 0 ]; then types=$(python3 -c "import json;print(json.dumps(['local file']*$n))"); curl -s --max-time 300 -X POST $B/delete_document_and_entities "${CRED[@]}" -F "filenames=$docs" -F "source_types=$types" -F deleteEntities=true | head -c 100; echo; fi
CY "MATCH (e:__Entity__) WHERE NOT (e)<-[:HAS_ENTITY]-(:Chunk) DETACH DELETE e" >/dev/null
echo "== 2) 実ファイル名で3本を抽出 (EDC) =="
declare -A FILES=( ["HS-VTR-T001E.pdf"]="/home/uchida/docs/試運転要領/HS-VTR-T001E.pdf" ["VTR-T003D.pdf"]="/home/uchida/docs/試運転要領/VTR-T003 D.pdf" ["HS-RU-016A.pdf"]="/home/uchida/docs/RHAT/HS-RU-016A高周波焼入れ本文.pdf" )
for fn in "HS-VTR-T001E.pdf" "VTR-T003D.pdf" "HS-RU-016A.pdf"; do
  cp "${FILES[$fn]}" "$S/$fn"
  curl -s --max-time 120 -X POST $B/upload -F "file=@$S/$fn" -F chunkNumber=1 -F totalChunks=1 -F originalname="$fn" -F model=edc "${CRED[@]}" >/dev/null
  t0=$(date +%s); r=$(curl -s --max-time 1800 -X POST $B/extract "${CRED[@]}" -F model=edc -F source_type="local file" -F file_name="$fn" -F source_url= -F allowedNodes= -F allowedRelationship= -F token_chunk_size=500 -F chunk_overlap=50 -F chunks_to_combine=1 -F language=ja -F retry_condition= -F additional_instructions= -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m)
  echo "[$fn] $(echo "$r" | python3 -c 'import json,sys;d=json.load(sys.stdin);x=d.get("data") or {};print(d.get("status"),"| chunks",x.get("total_chunks"),"| status",x.get("status"))') ($(( $(date +%s)-t0 ))s)"
done
echo "== 3) 後処理 (ノイズ除去・名寄せ・REFERENCES) =="
docker exec -i backend python - < $S/kg_postprocess.py
echo "== 4) 指標 =="
CY "MATCH (d:Document) WHERE d.fileName IS NOT NULL OPTIONAL MATCH (d)<-[:PART_OF]-(c:Chunk) OPTIONAL MATCH (c)-[:HAS_ENTITY]->(e:__Entity__) RETURN d.fileName, d.docKey, count(DISTINCT c), count(DISTINCT e)"
CY "MATCH (a:Document)-[r:REFERENCES]->(b:Document) RETURN a.fileName+' -['+r.via+']-> '+b.fileName"
CY "MATCH (e:__Entity__)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d:Document) WHERE d.fileName <> 'TS3-5.pdf' WITH e, collect(DISTINCT d.fileName) AS docs WHERE size(docs)>1 RETURN size(docs), count(e) ORDER BY size(docs)"
echo "== 完了 =="
