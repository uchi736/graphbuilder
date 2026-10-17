#!/usr/bin/env bash
S=/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/std
docker cp $S/diag_retriever2.py backend:/tmp/diag_retriever2.py
docker cp $S/req_extract.json backend:/tmp/req_extract.json
echo "=== ③ 再構築"
docker exec -i backend python - clean < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
docker exec -i backend python - p3 < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
echo "=== ③ リトリーバ診断"
docker exec backend python /tmp/diag_retriever2.py p3 2>&1 | grep -vE 'notification|Warning|^\s*$'
docker cp backend:/tmp/diag_retriever_p3.json $S/diag_retriever_p3.json
echo "=== ④ に戻す"
docker exec -i backend python - clean < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
docker exec -i backend python - p4 < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
curl -s --max-time 900 -X POST http://localhost:8440/post_processing -F uri=neo4j://database:7687 -F userName=neo4j -F password=kgb-please-change -F database=neo4j -F email=test@example.com -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m -F 'tasks=["materialize_entity_similarities"]' | head -c 100; echo
docker exec neo4j-kgb cypher-shell -u neo4j -p kgb-please-change --format plain "MATCH (e:__Entity__) RETURN count(e) AS total, count(e.embedding) AS embedded"
echo "=== ④ リトリーバ診断"
docker exec backend python /tmp/diag_retriever2.py p4 2>&1 | grep -vE 'notification|Warning|^\s*$'
docker cp backend:/tmp/diag_retriever_p4.json $S/diag_retriever_p4.json
echo "=== 完了"
