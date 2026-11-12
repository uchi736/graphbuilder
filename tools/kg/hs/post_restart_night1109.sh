#!/bin/sh
# 11/9 夜間の仕上げ: 81 問の評価と 56 本の連続処理が終わってから流す（どちらも本番の backend を使うため）。
#   1) backend を再起動して、未反映の変更（文書ノードへの edc_doctype 記録、参照先が参照元と同じチャンクだけの節参照を張らない）を読み込む
#   2) 本番（database）と評価用（database4）で章・節の層と由来情報を作り直す
#   3) 本番の参照を書き出して X20 を数え直す
#   4) 56 本の文書ノードに、取り込み時に EDC が判定した文書種別を記録する（run.json の doctype）
set -u
cd /home/uchida/ai-stack/llm-graph-builder
HS=tools/kg/hs; RS=tools/kg/refsec; OUT=$HS/runs/prod_night1109
log() { echo "== $* $(date '+%F %T')"; }
PW=$(grep -m1 '^NEO4J_PASSWORD=' backend/.env | cut -d= -f2-)
TASKS='["build_section_layer","attach_provenance"]'
form() { echo "--form-string email=night1109@example.com --form-string embedding_provider=vllm --form-string embedding_model=cl-nagoya/ruri-v3-310m"; }

log "1) backend を再起動"
docker restart backend >/dev/null
for i in $(seq 1 60); do curl -s -o /dev/null -w '%{http_code}' http://localhost:8440/health 2>/dev/null | grep -q 200 && break; sleep 5; done
curl -s http://localhost:8440/health; echo

log "2a) 本番: 章・節の層と由来情報"
curl -s --max-time 3600 -X POST http://localhost:8440/post_processing --form-string uri= --form-string userName= --form-string password= --form-string database= $(form) \
  --form-string "tasks=$TASKS" > $OUT/post_processing_restart.json
python3 -c "import json;d=json.load(open('$OUT/post_processing_restart.json'));print(d.get('status'), str(d.get('message'))[:1200])"

log "2b) 評価用 database4: 章・節の層と由来情報"
curl -s --max-time 3600 -X POST http://localhost:8440/post_processing --form-string uri=neo4j://database4:7687 --form-string userName=neo4j \
  --form-string password=kgb-please-change --form-string database=neo4j $(form) --form-string "tasks=$TASKS" > $HS/runs/full56_night1109/post_processing_restart.json
python3 -c "import json;d=json.load(open('$HS/runs/full56_night1109/post_processing_restart.json'));print(d.get('status'), str(d.get('message'))[:1200])"

log "3) 本番の参照を書き出して X20 を数え直す"
docker exec backend mkdir -p /out
docker exec -e NEO4J_URI=neo4j://database:7687 -e NEO4J_USERNAME=neo4j -e NEO4J_PASSWORD="$PW" -i backend python - < $RS/dump_refs.py
docker cp backend:/out/refs_dump.json $RS/refs_dump_night1109_restart.json
python3 $RS/x20_recount.py $RS/refs_dump_night1109_restart.json | tee $RS/x20_recount_restart.json

log "4) 56 本の文書ノードに文書種別を記録"
python3 -c "import json;d=json.load(open('tools/kg/hs/runs/full56_night1109/run.json',encoding='utf-8'));print(json.dumps([{'f':x['name'],'t':x.get('doctype') or ''} for x in d['docs'] if x.get('status')=='Success'],ensure_ascii=False))" > /tmp/claude-1000/full56_doctypes.json
docker cp /tmp/claude-1000/full56_doctypes.json backend:/tmp/full56_doctypes.json
docker exec -e NEO4J_URI=neo4j://database4:7687 -i backend python - <<'PY'
import json, os
from neo4j import GraphDatabase
rows = json.load(open("/tmp/full56_doctypes.json", encoding="utf-8"))
drv = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=("neo4j", "kgb-please-change"))
with drv.session() as s:
    n = s.run("UNWIND $rows AS r MATCH (d:Document {fileName: r.f}) SET d.edc_doctype = r.t, d.edc_schema_mode = 'fixed', "
              "d.edc_options_note = '11/9 夜間の一括取り込み（文書種別は EDC の自動判定）。値は取り込み記録 run.json から' RETURN count(d) AS n", rows=rows).single()["n"]
print("記録した文書", n, "/", len(rows))
PY
log "完了"
