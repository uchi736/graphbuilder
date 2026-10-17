#!/usr/bin/env bash
S=/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/std
docker cp $S/req_extract.json backend:/tmp/req_extract.json
echo "=== ③ 再構築（トレース時と同じ: 実体埋め込みなし）"
docker exec -i backend python - clean < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
docker exec -i backend python - p3 < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
echo "=== 診断"
docker cp $S/trace_p3full.json backend:/tmp/trace_p3full.json; docker exec -i backend python - < $S/diag_steps2.py 2> $S/diag_steps.err > $S/diag_steps2.json; echo "diag exit=$? size=$(wc -c < $S/diag_steps2.json)"; grep -vE 'notification|Warning' $S/diag_steps.err | tail -3
echo "=== ④ に戻す"
docker exec -i backend python - clean < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
docker exec -i backend python - p4 < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
curl -s --max-time 900 -X POST http://localhost:8440/post_processing -F uri=neo4j://database:7687 -F userName=neo4j -F password=kgb-please-change -F database=neo4j -F email=test@example.com -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m -F 'tasks=["materialize_entity_similarities"]' | head -c 120; echo
docker exec neo4j-kgb cypher-shell -u neo4j -p kgb-please-change --format plain "MATCH (e:__Entity__) RETURN count(e) AS total, count(e.embedding) AS embedded"
echo "=== 完了"
