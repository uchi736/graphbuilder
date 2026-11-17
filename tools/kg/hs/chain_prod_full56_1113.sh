#!/bin/sh
# 11/13: 本番グラフ（neo4j-kgb）に HS 基準の残り 50 本を足し、56 本にそろえる。
#   既存の 6 文書（試運転系）は取り込み済みのため対象外（ingest.py が Completed を飛ばす）。グラフは消さない。
#   抽出は gemma4 × EDC（文書種別 auto、語彙 fixed。X21・X22 の 56 本と同じ指定）。PDF 変換は本番の設定（regions）。
#   控え: ~/ai-stack/backup/llm-graph-builder-full56-20261113（APOC の全体書き出し。戻し方は README.txt）
# 使い方: sh tools/kg/hs/chain_prod_full56_1113.sh
set -u
cd "$(dirname "$0")/../../.." || exit 1
HS=tools/kg/hs; OUT=$HS/runs/prod_full56_1113
log() { echo "== $* $(date '+%F %T')"; }
PW=$(grep -m1 '^NEO4J_PASSWORD=' backend/.env | cut -d= -f2-)
q() { docker exec neo4j-kgb cypher-shell -u neo4j -p "$PW" --format plain "$1"; }

log "50 本を EDC（auto・fixed）で取り込む"
python3 tools/ingest/ingest.py $OUT/pdf --extraction-method edc --edc-doctype auto --edc-schema-mode fixed > $OUT/ingest.log 2>&1
tail -n 12 $OUT/ingest.log

log "文書間の参照をエンティティの線にする（規則）"
docker exec -e NEO4J_URI=neo4j://database:7687 -i backend python - < $HS/doc_ref_entities.py > $OUT/doc_ref_entities.log 2>&1
head -n 8 $OUT/doc_ref_entities.log

log "全体の後処理をもう一度（56 本で規則の層と由来情報をそろえる）"
TASKS='["materialize_text_chunk_similarities","enable_hybrid_search_and_fulltext_search_in_bloom","link_chunk_references","build_document_layer","build_section_layer","attach_provenance","materialize_entity_similarities"]'
curl -s --max-time 3600 -X POST http://localhost:8440/post_processing --form-string uri= --form-string userName= --form-string password= \
  --form-string database= --form-string email=full56@example.com --form-string embedding_provider=vllm \
  --form-string embedding_model=cl-nagoya/ruri-v3-310m --form-string "tasks=$TASKS" > $OUT/post_processing.json
head -c 1200 $OUT/post_processing.json; echo

log "件数"
q "MATCH (d:Document) WHERE d.fileName IS NOT NULL RETURN d.status AS s, count(*) AS c" | tee $OUT/counts_after.txt
q "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c ORDER BY c DESC LIMIT 30" | tee -a $OUT/counts_after.txt
log "完了"
