#!/usr/bin/env bash
# 参照リンク（gb-refs-runner）が終わった後の連鎖: 文書層・節の層 → 索引と埋め込み → 採点。backend は再起動しない
set -u
cd "$(dirname "$0")/../../.."
docker wait gb-refs-runner >/dev/null 2>&1
echo "== 参照リンクの結果"; docker logs gb-refs-runner 2>&1 | grep -v notification | tail -3 | cut -c1-400; docker rm gb-refs-runner >/dev/null 2>&1
echo "== 文書層・節の層"
docker run --rm --name gb-layers-runner --memory=3g --memory-swap=3g --cpus=2 --network llm-graph-builder_net --env-file backend/.env \
  -v "$PWD/backend:/code" -v "$PWD/tools/kg/refs:/refs:ro" -w /code --entrypoint python3 llm-graph-builder-backend /refs/run_layers_after_refs.py 2>&1 | grep -v notification | tail -3 | cut -c1-400
echo "== 索引と埋め込み（backend の API）"
PW=$(grep -E "^NEO4J_PASSWORD=" backend/.env | cut -d= -f2- | tr -d '"'"'")
curl -s --max-time 1800 -X POST http://localhost:8440/post_processing --form-string "uri=neo4j://database:7687" --form-string "userName=neo4j" --form-string "password=$PW" \
  --form-string "database=neo4j" --form-string "email=ingest@local" \
  --form-string 'tasks=["materialize_text_chunk_similarities","enable_hybrid_search_and_fulltext_search_in_bloom","materialize_entity_similarities"]' \
  --form-string "embedding_provider=vllm" --form-string "embedding_model=cl-nagoya/ruri-v3-310m" | cut -c1-120; echo
echo "== 採点: 参照の正解 43 件"; python3 tools/kg/refs/score_refs.py tools/kg/refs/out/chunk_refs_luna_v2.json 2>&1 | tail -12
echo "== 採点: 文書どうしの 15 組"; docker cp tools/kg/refs/gold_refs.json backend:/tmp/gold_refs.json >/dev/null; docker exec -i -e GOLD_REFS=/tmp/gold_refs.json -w /code backend python3 - < tools/kg/refs/score_doclayer.py 2>&1 | grep -v notification | tail -6
echo "== 段 3 の数"; tools/ingest/verify_stage3.sh 2>&1
echo "== 費用"; docker exec luna-meter python -c "import json;d=json.load(open('/ledger/luna_usage.json'));print('usd',d['usd'],'calls',d['calls'],'refused',d['refused'],'errors',d['errors'])"
