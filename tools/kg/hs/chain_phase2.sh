#!/bin/sh
# 56 本を方式 4（EDC＋品質要求基準の定義、規則の直し 3 つ入り）で取り込み、参照リンクを規則で張り、問いを 4 条件で流す。
# 評価用の Neo4j は neo4j-kgb4 だけを使い、最後に止める。本番の参照リンクの設定（CHUNK_REFERENCE_MODE）は変えない。
set -u
cd /home/uchida/ai-stack/llm-graph-builder/tools/kg/hs
K=..; OUT=runs/full56_edc_quality; mkdir -p $OUT
log() { echo "== $* $(date '+%F %T')"; }
log "1) backend を再起動（直したコードを読み込む）"
docker restart backend > /dev/null
for i in $(seq 1 40); do curl -s --max-time 3 http://localhost:8440/health > /dev/null 2>&1 && break; sleep 3; done
log "2) 56 本を取り込む"
python3 run_hs_method.py edc neo4j-kgb4 database4 full56_edc_quality --keep-running > runs_full56.log 2>&1 || { log "取り込みが中止した"; docker stop neo4j-kgb4 > /dev/null; exit 1; }
log "3) チャンク間の参照リンクを規則で張る"
docker exec -e NEO4J_URI=neo4j://database4:7687 -e CHUNK_REFERENCE_MODE=rule -e REFRULES_DIR=/nonexistent -i backend python - < $K/synth/link_refs.py > $OUT/link_refs.log 2>&1
tail -n 12 $OUT/link_refs.log
log "4) 文書間の参照をエンティティの線にする"
docker exec -e NEO4J_URI=neo4j://database4:7687 -i backend python - < doc_ref_entities.py > $OUT/doc_ref_entities.log 2>&1
head -n 8 $OUT/doc_ref_entities.log
log "5) 足したエンティティの埋め込みと索引"
curl -s --max-time 1800 -X POST http://localhost:8440/post_processing --form-string uri=neo4j://database4:7687 --form-string userName=neo4j --form-string password=kgb-please-change --form-string database=neo4j --form-string email=test@example.com --form-string embedding_provider=vllm --form-string embedding_model=cl-nagoya/ruri-v3-310m --form-string 'tasks=["materialize_entity_similarities","enable_hybrid_search_and_fulltext_search_in_bloom"]' | head -c 160; echo
log "6) 書き出し（リンク入り）"
docker exec -e NEO4J_URI=neo4j://database4:7687 -i backend python - < $K/routes/dump_graph.py > $OUT/graph_dump_linked.json 2>/dev/null
python3 -c "import json;d=json.load(open('$OUT/graph_dump_linked.json',encoding='utf-8'));print({k:len(v) for k,v in d.items()})"
log "7) 採点（採点できる 10 本の部分）"
python3 score/recount_hs.py --gold $K/gold_graph.json --run "EDC＋品質定義(直し前)=runs/p1_edc_quality/graph_dump.json@files_vtrt6.txt" --run "EDC＋品質定義＋規則の直し=$OUT/graph_dump_linked.json@files_vtrt6.txt" --out score_out/full56_vtrt6.json > score_out/full56_vtrt6.md
python3 score/recount_hs.py --gold gold_hs.json --run "EDC＋品質定義(直し前)=runs/p1_edc_quality/graph_dump.json@files_hs4.txt" --run "EDC＋品質定義＋規則の直し=$OUT/graph_dump_linked.json@files_hs4.txt" --out score_out/full56_hs4.json > score_out/full56_hs4.md
head -n 12 score_out/full56_vtrt6.md; head -n 12 score_out/full56_hs4.md
log "8) 問いを 4 条件で流す"
python3 qa/eval_hs.py $OUT/graph_dump_linked.json qa/eval_full56.json --db database4 > qa/eval_full56.log 2>&1
tail -n 30 qa/eval_full56.log
log "9) Neo4j を止める"
docker stop neo4j-kgb4 > /dev/null
log "完了"
echo PHASE2_DONE > phase2.done
