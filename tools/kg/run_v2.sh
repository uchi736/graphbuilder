#!/usr/bin/env bash
# 新しい分割で取り込んだ 2 つ目の Neo4j（database2）に、規定レコードのグラフを作り、16 問を流す。
#   前提: ingest2.sh で 6 文書を取り込み済み。backend・gemma4・埋め込み・リランカーが稼働中。
set -u
K=/home/uchida/ai-stack/llm-graph-builder/tools/kg; L=$K/v2; mkdir -p $L
E=(-e NEO4J_URI=neo4j://database2:7687)
CRED=(-F uri=neo4j://database2:7687 -F userName=neo4j -F password=kgb-please-change -F database=neo4j -F email=test@example.com)
echo "== 1) 規定レコードの抽出 (gemma4) $(date +%T)"
# 途中から再開できるよう、抽出済みの結果は消さない
docker exec "${E[@]}" -i backend python - < $K/req_extract.py > $L/extract.log 2>&1; tail -1 $L/extract.log
docker cp backend:/tmp/req_extract.json $L/req_extract_v2.json
echo "== 2) グラフの組み立て $(date +%T)"
docker exec "${E[@]}" -i backend python - clean < $K/req_materialize.py 2>&1 | grep -vE 'notification|Warning' | tail -2
docker exec "${E[@]}" -i backend python - p4 < $K/req_materialize.py 2>&1 | grep -vE 'notification|Warning' | tail -3
echo "== 3) エンティティの埋め込みと索引 $(date +%T)"
curl -s --max-time 900 -X POST http://localhost:8440/post_processing "${CRED[@]}" -F embedding_provider=vllm -F embedding_model=cl-nagoya/ruri-v3-310m -F 'tasks=["materialize_entity_similarities","enable_hybrid_search_and_fulltext_search_in_bloom"]' | head -c 160; echo
echo "== 4) 16 問 $(date +%T)"
GB_URI=neo4j://database2:7687 python3 $K/run_settings.py v2_newchunk '{}' | tail -17
echo "== 完了 $(date +%T)"
