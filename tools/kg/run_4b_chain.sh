#!/usr/bin/env bash
# 段 4b（enrich＋RELATED_TO 集約＋文の除外、語彙名の大文字化つき）の一連の処理。tools/kg で実行する。
#   1) 写しの adapter を backend に当てて作り直す  2) 実物 6 文書 → neo4j-kgb4、採点  3) 評価用 38 本 → neo4j-kgb7、参照リンク、書き出し
#   4) チャット評価 277 問を kgb6（enrich）と kgb7（4b）で流す
# 前提: チャット評価など backend を使う処理が他に動いていないこと（作り直しで切れる）。
set -u
K=/home/uchida/ai-stack/llm-graph-builder/tools/kg; R=/home/uchida/ai-stack/llm-graph-builder
W=/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/edc4b
log() { echo "== $* $(date '+%F %T')"; }
log "1) adapter を当てて backend を作り直す"
cp $W/edc_extraction.py $R/backend/src/edc_extraction.py
(cd $R && docker compose up -d --no-deps backend 2>&1 | tail -1)
for i in $(seq 1 40); do curl -s --max-time 3 http://localhost:8440/health >/dev/null 2>&1 && { echo "backend 復帰 ($((i*3))秒)"; break; }; sleep 3; done
docker exec backend sh -c 'printenv EDC_ENRICH_SCHEMA EDC_FOLD_UNKNOWN_RELATIONS EDC_DROP_SENTENCE_NAMES EDC_SCHEMAS_DIR; ls /edc_schemas | head -3; grep -c _load_vocabulary /code/src/edc_extraction.py'
log "2) 実物 6 文書 → kgb4"
cd $K && mkdir -p methods/edc_fold2 && ./run_methods.sh neo4j-kgb4 database4 edc edc_fold2 > methods/edc_fold2/run.log 2>&1; tail -3 methods/edc_fold2/run.log
docker cp $K/gold_graph.json backend:/tmp/gold_graph.json
docker exec -e NEO4J_URI=neo4j://database4:7687 -e NEO4J_USERNAME=neo4j -e NEO4J_PASSWORD=kgb-please-change -i backend python - /tmp/gold_graph.json < $K/score_gold.py > methods/edc_fold2/score_edc_fold2.json 2>/dev/null
python3 -c "import json;s=json.load(open('$K/methods/edc_fold2/score_edc_fold2.json',encoding='utf-8'));print('いまの数え方: エンティティ',s['entities']['found'],'リレーション',s['relations']['any_edge'],'型まで',s['relations']['typed'])"
log "3) 評価用 38 本 → kgb7"
docker exec neo4j-kgb7 cypher-shell -u neo4j -p kgb-please-change "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 500 ROWS" 2>&1 | tail -1
cd $K/synth && CN=neo4j-kgb7 AL=database7 LOG=$K/synth/ingest_edc_fold.log ./ingest_synth_edc.sh > /dev/null 2>&1; tail -2 ingest_edc_fold.log
curl -s --max-time 1800 -X POST http://localhost:8440/post_processing -F uri=neo4j://database7:7687 -F userName=neo4j -F password=kgb-please-change -F database=neo4j -F email=test@example.com -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m -F 'tasks=["materialize_entity_similarities","enable_hybrid_search_and_fulltext_search_in_bloom"]' | head -c 80; echo
docker exec -e NEO4J_URI=neo4j://database7:7687 -e NEO4J_PASSWORD=kgb-please-change -e CHUNK_REFERENCE_MODE=rule -e REFRULES_DIR=/nonexistent -i backend python - < link_refs.py 2>&1 | grep -E "'method'" | tail -4
docker exec -e NEO4J_URI=neo4j://database7:7687 -i backend python - < $K/routes/dump_graph.py > graph_dump7_edc_fold.json 2>/dev/null
python3 -c "import json;d=json.load(open('graph_dump7_edc_fold.json',encoding='utf-8'));print({k:len(v) for k,v in d.items()})"
log "4) チャット評価 kgb6（enrich）"
python3 eval_synth.py graph_dump6_edc_enrich.json eval_edc_enrich.json --settings 新・参照2 --db database6 > eval_edc_enrich.log 2>&1; tail -3 eval_edc_enrich.log | cut -c1-120
log "4) チャット評価 kgb7（4b）"
python3 eval_synth.py graph_dump7_edc_fold.json eval_edc_fold.json --settings 新・参照2 --db database7 > eval_edc_fold.log 2>&1; tail -3 eval_edc_fold.log | cut -c1-120
log "完了"
