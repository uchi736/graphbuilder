#!/bin/sh
# 11/9 夜間: 本番グラフ（neo4j-kgb、6 文書）を gemma4 × EDC（試運転_検査基準・fixed）で作り直す。
#   wipe   … 文書・チャンク・エンティティ・節を消す（会話 Session/Message と __GraphLock__ は残す）。控えは backup/llm-graph-builder-night1109
#   ingest … 画面で取り込んだ 1 本（VTR-T004C）以外の 5 本を ingest.py で取り込み、後処理まで
#   score  … 書き出しと採点（正解グラフ v2、6 文書）
# 戻し方: backup/llm-graph-builder-night1109/README.txt
set -u
cd /home/uchida/ai-stack/llm-graph-builder
HS=tools/kg/hs; OUT=$HS/runs/prod_night1109; mkdir -p $OUT
log() { echo "== $* $(date '+%F %T')"; }
PW=$(grep -m1 '^NEO4J_PASSWORD=' backend/.env | cut -d= -f2-)
q() { docker exec neo4j-kgb cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

case "${1:-}" in
wipe)
  log "本番グラフを空にする（Session・Message・__GraphLock__ は残す）"
  q "MATCH (n) WHERE n:Document OR n:Chunk OR n:__Entity__ OR n:Section OR n:Evidence CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 500 ROWS"
  q "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c ORDER BY c DESC"
  ;;
ingest)
  log "残り 5 本を EDC（試運転_検査基準・fixed）で取り込む"
  python3 tools/ingest/ingest.py $OUT/pdf --extraction-method edc --edc-doctype 試運転_検査基準 --edc-schema-mode fixed > $OUT/ingest.log 2>&1
  tail -n 20 $OUT/ingest.log
  log "文書間の参照をエンティティの線にする（規則、10/17 の方式 4 と同じ手順。04:20 に足した）"
  docker exec -e NEO4J_URI=neo4j://database:7687 -i backend python - < $HS/doc_ref_entities.py > $OUT/doc_ref_entities.log 2>&1
  head -n 8 $OUT/doc_ref_entities.log
  log "全体の後処理をもう一度（画面で取り込んだ 1 本も含めて規則の層と由来情報をそろえる）"
  TASKS='["materialize_text_chunk_similarities","enable_hybrid_search_and_fulltext_search_in_bloom","link_chunk_references","build_document_layer","build_section_layer","attach_provenance","materialize_entity_similarities"]'
  curl -s --max-time 3600 -X POST http://localhost:8440/post_processing --form-string uri= --form-string userName= --form-string password= \
    --form-string database= --form-string email=night1109@example.com --form-string embedding_provider=vllm \
    --form-string embedding_model=cl-nagoya/ruri-v3-310m --form-string "tasks=$TASKS" > $OUT/post_processing.json
  head -c 1500 $OUT/post_processing.json; echo
  q "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c ORDER BY c DESC LIMIT 25"
  ;;
score)
  log "書き出しと採点"
  docker exec -e NEO4J_URI=neo4j://database:7687 -e NEO4J_PASSWORD="$PW" -i backend python - < tools/kg/routes/dump_graph.py > $OUT/graph_dump.json 2>/dev/null
  python3 -c "import json;d=json.load(open('$OUT/graph_dump.json',encoding='utf-8'));print({k:len(v) for k,v in d.items()})"
  cd $HS
  python3 score/recount_hs.py --gold ../gold_graph.json \
    --run "luna 標準（11/7 本番）=runs/luna_prod_pre_night1109/graph_dump.json@files_vtrt6.txt" \
    --run "gemma4 EDC（10/17 56 本の部分）=runs/full56_edc_quality/graph_dump_linked.json@files_vtrt6.txt" \
    --run "gemma4 EDC 改修後（11/9 本番）=runs/prod_night1109/graph_dump.json@files_vtrt6.txt" \
    --out score_out/night1109/prod6.json > score_out/night1109/prod6.md 2>&1
  head -n 16 score_out/night1109/prod6.md
  ;;
*) echo "使い方: $0 wipe|ingest|score"; exit 2 ;;
esac
