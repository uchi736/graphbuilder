#!/usr/bin/env bash
# 文書層・節の層だけを作り直し、エンティティの埋め込みを足してから採点する（参照リンクはやり直さない）。backend は再起動しない・exec しない
set -u
cd "$(dirname "$0")/../../.."
echo "== 文書層・節の層"
# 埋め込みの接続先は backend コンテナと同じ値を使う（compose がルートの .env から渡すので backend/.env には無い）。文書カードの埋め込みに要る
EMB_ENV=$(docker inspect backend --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(VLLM_EMBEDDING_ENDPOINT|EMBEDDING_PROVIDER|EMBEDDING_MODEL)=' | sed 's/^/-e /' | tr '\n' ' ')
# shellcheck disable=SC2086
docker run --rm --name gb-layers-runner --memory=3g --memory-swap=3g --cpus=2 --network llm-graph-builder_net --env-file backend/.env $EMB_ENV \
  -v "$PWD/backend:/code:ro" -v "$PWD/backend/logs:/code/logs" -v "$PWD/tools/kg/refs:/refs:ro" -e PYTHONDONTWRITEBYTECODE=1 -w /code --entrypoint python3 \
  llm-graph-builder-backend /refs/run_layers_after_refs.py 2>&1 | grep -v notification | tail -4 | cut -c1-1500
echo "== エンティティの埋め込みと類似（backend の API）"
PW=$(grep -E "^NEO4J_PASSWORD=" backend/.env | cut -d= -f2- | tr -d '"'"'")
curl -s --max-time 1800 -X POST http://localhost:8440/post_processing --form-string "uri=neo4j://database:7687" --form-string "userName=neo4j" --form-string "password=$PW" \
  --form-string "database=neo4j" --form-string "email=ingest@local" \
  --form-string 'tasks=["materialize_entity_similarities"]' \
  --form-string "embedding_provider=vllm" --form-string "embedding_model=cl-nagoya/ruri-v3-310m" | cut -c1-160; echo
echo "== 採点: 参照の正解 43 件"; python3 tools/kg/refs/score_refs.py tools/kg/refs/out/chunk_refs_luna_v2.json 2>&1 | grep -vE '^\s+\[' | tail -15
echo "== 採点: 文書どうしの 15 組"
docker run --rm --memory=1g --memory-swap=1g --cpus=1 --network llm-graph-builder_net --env-file backend/.env -e GOLD_REFS=/refs/gold_refs.json \
  -v "$PWD/tools/kg/refs:/refs:ro" -w /tmp --entrypoint python3 llm-graph-builder-backend /refs/score_doclayer.py 2>&1 | grep -v notification | tail -8
echo "== 段 3 の数"; tools/ingest/verify_stage3.sh 2>&1
