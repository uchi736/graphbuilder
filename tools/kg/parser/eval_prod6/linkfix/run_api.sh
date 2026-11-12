#!/bin/sh
# 評価用 backend（:8446、kgb5）で 参照リンク → 文書層 → 章・節の層 を流し、書き出して判定する。使い方: run_api.sh <名前>
set -eu
cd /home/uchida/ai-stack/llm-graph-builder
D=tools/kg/parser/eval_prod6/linkfix
docker restart backend-parser >/dev/null
for i in $(seq 1 40); do curl -s -o /dev/null -w '%{http_code}' http://localhost:8446/health 2>/dev/null | grep -q 200 && break; sleep 3; done
# 各回を同じ抽出の結果（11/9 の取り込みの抽出）から始める
docker cp $D/kgb5_before_linkfix.json backend-parser:/tmp/kgb5_before_linkfix.json
docker exec -e NEO4J_URI=neo4j://database5:7687 -e NEO4J_USERNAME=neo4j -e NEO4J_PASSWORD=kgb-please-change -i backend-parser python - < $D/restore_stored.py
curl -s --max-time 3000 -X POST http://localhost:8446/post_processing --form-string uri= --form-string userName= --form-string password= --form-string database= \
  --form-string email=linkfix1110@example.com --form-string embedding_provider=vllm --form-string embedding_model=cl-nagoya/ruri-v3-310m \
  --form-string 'tasks=["link_chunk_references","build_document_layer","build_section_layer"]' > $D/post_processing_$1.json
python3 -c "import json;d=json.load(open('$D/post_processing_$1.json'));print(d.get('status'), str(d.get('message'))[:800])"
docker logs backend-parser 2>&1 | grep -E "section layer: \{" | tail -1 | cut -c1-900
$D/dump.sh $1
python3 $D/judge.py $D/refs_dump_$1.json $D/manual.json
