#!/bin/sh
# パーサー置き換えの評価（方針 7Vmc2ijKYSF2JCCNHGwe5T の段 2・3）。本番には書かない。
#   A: 本番 6 文書を新方式（regions、図は text）で評価用 kgb5（database5）に gemma4 × EDC で取り込み → 採点・参照の書き出し・81 問 2 条件
#   B: HS 56 本を同じく kgb6（database6）に取り込み → 規則の層・採点・問い 23 本＋対照を 5 条件（参照追跡は再起動後の既定）
# 比較の相手は 11/9 夜間の本番（prod_night1109）と kgb4（full56_night1109）。どちらも今の変換（pymupdf4llm）。
set -u
cd /home/uchida/ai-stack/llm-graph-builder
K=tools/kg; HS=$K/hs; P=$K/parser; PW=kgb-please-change
export GB_BACKEND=http://localhost:8446 GB_BACKEND_CONTAINER=backend-parser
log() { echo "== $* $(date '+%F %T')"; }
cred() { echo "--form-string uri=neo4j://$1:7687 --form-string userName=neo4j --form-string password=$PW --form-string database=neo4j --form-string email=parser@example.com --form-string embedding_provider=vllm --form-string embedding_model=cl-nagoya/ruri-v3-310m"; }

stageA() {
  OUT=$P/eval_prod6; mkdir -p $OUT
  log "A0 kgb5 を起こして空にする"
  docker start neo4j-kgb5 >/dev/null; sleep 25
  docker exec neo4j-kgb5 cypher-shell -u neo4j -p $PW "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 1000 ROWS" >/dev/null
  log "A1 評価用 backend（既定の接続先 database5）"
  sh $P/eval_backend.sh up database5
  log "A2 6 文書を取り込む（EDC 試運転_検査基準・fixed、後処理は既定一式）"
  python3 tools/ingest/ingest.py $HS/runs/prod_night1109/pdf --backend $GB_BACKEND --neo4j-uri neo4j://database5:7687 --neo4j-user neo4j \
    --neo4j-password $PW --neo4j-database neo4j --extraction-method edc --edc-doctype 試運転_検査基準 --edc-schema-mode fixed --force > $OUT/ingest.log 2>&1
  tail -n 12 $OUT/ingest.log
  log "A3 文書間の参照をエンティティの線に（夜間と同じ）→ 後処理をもう一度"
  docker exec -e NEO4J_URI=neo4j://database5:7687 -e NEO4J_PASSWORD=$PW -i backend-parser python - < $HS/doc_ref_entities.py > $OUT/doc_ref_entities.log 2>&1
  curl -s --max-time 3600 -X POST $GB_BACKEND/post_processing $(cred database5) \
    --form-string 'tasks=["build_document_layer","build_section_layer","attach_provenance","materialize_entity_similarities"]' > $OUT/post_processing.json
  log "A4 書き出しと採点（正解グラフ v2、6 文書）"
  docker exec -e NEO4J_URI=neo4j://database5:7687 -e NEO4J_PASSWORD=$PW -i backend-parser python - < $K/routes/dump_graph.py > $OUT/graph_dump.json 2>/dev/null
  (cd $HS && python3 score/recount_hs.py --gold ../gold_graph.json \
    --run "今の変換（11/9 夜間の本番）=runs/prod_night1109/graph_dump.json@files_vtrt6.txt" \
    --run "新方式 regions（図は text）=../parser/eval_prod6/graph_dump.json@files_vtrt6.txt" \
    --out ../parser/eval_prod6/score.json > ../parser/eval_prod6/score.md 2>&1); head -n 16 $OUT/score.md
  log "A5 章・節と文書層の参照の書き出し"
  docker exec backend-parser mkdir -p /out
  docker exec -e NEO4J_URI=neo4j://database5:7687 -e NEO4J_USERNAME=neo4j -e NEO4J_PASSWORD=$PW -i backend-parser python - < $K/refsec/dump_refs.py
  docker cp backend-parser:/out/refs_dump.json $OUT/refs_dump.json
  python3 -c "import json;d=json.load(open('$OUT/refs_dump.json'));print({k:len(v) for k,v in d.items()})"
  log "A6 81 問を 2 条件で（既定の検索・参照追跡）"
  (cd $K/refsec && ALL=1 python3 evaluate_night1109.py filtered.json ../parser/eval_prod6/results81.json > ../parser/eval_prod6/eval81.log 2>&1; python3 score.py ../parser/eval_prod6/results81.json > ../parser/eval_prod6/score81.log 2>&1); tail -n 12 $OUT/score81.log
  docker stop neo4j-kgb5 >/dev/null
  log "A 完了"
}

stageB() {
  NAME=full56_regions_text; OUT=$HS/runs/$NAME; mkdir -p $OUT
  log "B1 56 本を取り込む（EDC、文書種別 auto、fixed）→ kgb6"
  HS_EDC_DOCTYPE=auto HS_EDC_SCHEMA_MODE=fixed python3 $HS/run_hs_method.py edc neo4j-kgb6 database6 $NAME --keep-running > $HS/runs_${NAME}.log 2>&1 \
    || { log "取り込みが中止した"; return 1; }
  tail -n 5 $HS/runs_${NAME}.log
  log "B2 参照リンク（規則）・文書間参照・後処理"
  docker exec -e NEO4J_URI=neo4j://database6:7687 -e CHUNK_REFERENCE_MODE=rule -e REFRULES_DIR=/nonexistent -i backend-parser python - < $K/synth/link_refs.py > $OUT/link_refs.log 2>&1
  docker exec -e NEO4J_URI=neo4j://database6:7687 -e NEO4J_PASSWORD=$PW -i backend-parser python - < $HS/doc_ref_entities.py > $OUT/doc_ref_entities.log 2>&1
  curl -s --max-time 3600 -X POST $GB_BACKEND/post_processing $(cred database6) \
    --form-string 'tasks=["build_document_layer","build_section_layer","attach_provenance","materialize_entity_similarities","enable_hybrid_search_and_fulltext_search_in_bloom"]' > $OUT/post_processing.json
  log "B3 書き出しと採点"
  docker exec -e NEO4J_URI=neo4j://database6:7687 -e NEO4J_PASSWORD=$PW -i backend-parser python - < $K/routes/dump_graph.py > $OUT/graph_dump_linked.json 2>/dev/null
  (cd $HS && python3 score/recount_hs.py --gold ../gold_graph.json \
      --run "今の変換（11/9 夜間 kgb4）=runs/full56_night1109/graph_dump_linked.json@files_vtrt6.txt" \
      --run "新方式 regions（図は text）=runs/$NAME/graph_dump_linked.json@files_vtrt6.txt" --out ../parser/eval_full56_vtrt6.json > ../parser/eval_full56_vtrt6.md 2>&1
   python3 score/recount_hs.py --gold gold_hs.json \
      --run "今の変換（11/9 夜間 kgb4）=runs/full56_night1109/graph_dump_linked.json@files_hs4.txt" \
      --run "新方式 regions（図は text）=runs/$NAME/graph_dump_linked.json@files_hs4.txt" --out ../parser/eval_full56_hs4.json > ../parser/eval_full56_hs4.md 2>&1)
  head -n 14 $P/eval_full56_vtrt6.md; head -n 14 $P/eval_full56_hs4.md
  log "B4 問い 23 本＋対照を 5 条件で"
  (cd $HS && python3 qa/eval_hs.py runs/$NAME/graph_dump_linked.json qa/eval_full56_regions_text.json --db database6 \
     --settings 本文だけ,参照あり,グラフあり,参照＋グラフ,参照追跡 --control-settings 本文だけ,参照追跡 > qa/eval_full56_regions_text.log 2>&1)
  grep -E '^(agg|confusable|control|ent1|ent2|ref1)' $HS/qa/eval_full56_regions_text.log
  log "B 完了（kgb6 は起動したまま）"
}

case "${1:-all}" in
A) stageA ;;
B) stageB ;;
all) stageA; stageB ;;
esac
echo DONE > $P/chain_parser_eval.done
