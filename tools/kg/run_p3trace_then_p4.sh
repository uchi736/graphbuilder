#!/usr/bin/env bash
# ③を組み直して既定モードの全量トレース → ④に戻して実体埋め込み
S=/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/std
set -u
docker cp $S/req_extract.json backend:/tmp/req_extract.json
echo "=== ③ 再構築 ==="
docker exec -i backend python - clean < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
docker exec -i backend python - p3 < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
echo "=== ③ トレース（既定） ==="
cd $S && python3 trace_qa.py p3full graph_vector_fulltext
echo "=== ④ に戻す ==="
docker exec -i backend python - clean < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
docker exec -i backend python - p4 < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
echo "=== 実体埋め込み ==="
curl -s --max-time 900 -X POST http://localhost:8440/post_processing -F uri=neo4j://database:7687 -F userName=neo4j -F password=kgb-please-change -F database=neo4j -F email=test@example.com -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m -F 'tasks=["materialize_entity_similarities"]' | head -c 300; echo
docker exec neo4j-kgb cypher-shell -u neo4j -p kgb-please-change --format plain "MATCH (n:Requirement) WITH count(n) AS req MATCH (e:__Entity__) WHERE e.embedding IS NOT NULL RETURN req, count(e) AS embedded"
echo "=== 完了 ==="
