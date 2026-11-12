#!/bin/sh
# 11/9 夜間: HS 56 本を gemma4 × EDC（文書種別は自動判定、運用モード fixed）で取り込み、規則の層・由来情報まで作り、採点と問いの評価を流す。
# 前提: backend と edc-api は 11/9 の改修を配備済み。評価用の Neo4j は neo4j-kgb4（database4）だけを使う。本番の設定は変えない。
# 10/17 の chain_phase2.sh と同じ手順に、build_document_layer・build_section_layer・attach_provenance と、問いの 5 つ目の条件「参照追跡」を足した。
set -u
cd /home/uchida/ai-stack/llm-graph-builder/tools/kg/hs
K=..; NAME=full56_night1109; OUT=runs/$NAME; mkdir -p $OUT score_out/night1109
PW=kgb-please-change
log() { echo "== $* $(date '+%F %T')"; }
cred() { echo "--form-string uri=neo4j://database4:7687 --form-string userName=neo4j --form-string password=$PW --form-string database=neo4j --form-string email=test@example.com --form-string embedding_provider=vllm --form-string embedding_model=cl-nagoya/ruri-v3-310m"; }

log "1) 56 本を取り込む（EDC、文書種別 auto、fixed）"
HS_EDC_DOCTYPE=auto HS_EDC_SCHEMA_MODE=fixed python3 run_hs_method.py edc neo4j-kgb4 database4 $NAME --keep-running > runs_${NAME}.log 2>&1 \
  || { log "取り込みが中止した（runs_${NAME}.log を見る）"; exit 1; }
tail -n 5 runs_${NAME}.log

log "2) チャンク間の参照リンクを規則で張る"
docker exec -e NEO4J_URI=neo4j://database4:7687 -e CHUNK_REFERENCE_MODE=rule -e REFRULES_DIR=/nonexistent -i backend python - < $K/synth/link_refs.py > $OUT/link_refs.log 2>&1
tail -n 6 $OUT/link_refs.log

log "3) 文書間の参照をエンティティの線にする"
docker exec -e NEO4J_URI=neo4j://database4:7687 -i backend python - < doc_ref_entities.py > $OUT/doc_ref_entities.log 2>&1
head -n 6 $OUT/doc_ref_entities.log

log "4) 後処理（文書層・章・節の層・由来情報・埋め込みと索引）"
curl -s --max-time 3600 -X POST http://localhost:8440/post_processing $(cred) \
  --form-string 'tasks=["build_document_layer","build_section_layer","attach_provenance","materialize_entity_similarities","enable_hybrid_search_and_fulltext_search_in_bloom"]' > $OUT/post_processing.json
head -c 1500 $OUT/post_processing.json; echo

log "5) 書き出し"
docker exec -e NEO4J_URI=neo4j://database4:7687 -i backend python - < $K/routes/dump_graph.py > $OUT/graph_dump_linked.json 2>/dev/null
python3 -c "import json;d=json.load(open('$OUT/graph_dump_linked.json',encoding='utf-8'));print({k:len(v) for k,v in d.items()})"

log "6) 採点（採点できる 10 本）"
python3 score/recount_hs.py --gold $K/gold_graph.json \
  --run "10/17 EDC＋品質定義（gemma4、enrich）=runs/full56_edc_quality/graph_dump_linked.json@files_vtrt6.txt" \
  --run "11/9 EDC 改修後（gemma4、fixed）=$OUT/graph_dump_linked.json@files_vtrt6.txt" \
  --out score_out/night1109/full56_vtrt6.json > score_out/night1109/full56_vtrt6.md 2>&1
python3 score/recount_hs.py --gold gold_hs.json \
  --run "10/17 EDC＋品質定義（gemma4、enrich）=runs/full56_edc_quality/graph_dump_linked.json@files_hs4.txt" \
  --run "11/9 EDC 改修後（gemma4、fixed）=$OUT/graph_dump_linked.json@files_hs4.txt" \
  --out score_out/night1109/full56_hs4.json > score_out/night1109/full56_hs4.md 2>&1
head -n 14 score_out/night1109/full56_vtrt6.md; head -n 14 score_out/night1109/full56_hs4.md

log "7) 問い 23 本を 5 条件で流す"
python3 qa/eval_hs.py $OUT/graph_dump_linked.json qa/eval_full56_night1109.json --db database4 \
  --settings 本文だけ,参照あり,グラフあり,参照＋グラフ,参照追跡 > qa/eval_full56_night1109.log 2>&1
tail -n 30 qa/eval_full56_night1109.log

log "完了（neo4j-kgb4 は評価の続きのため起動したまま）"
echo DONE > $OUT/chain.done
