#!/bin/sh
# 評価用 backend（kgb5）で、LLM と結び先の選び方を選んで 参照リンク → 文書層 → 章・節の層 を直接流し、書き出して判定する。
# 使い方: run_direct.sh <名前> <LLM の名前（gemma4 など）> [outline|legacy]
set -eu
cd /home/uchida/ai-stack/llm-graph-builder
D=tools/kg/parser/eval_prod6/linkfix
docker cp $D/kgb5_before_linkfix.json backend-parser:/tmp/kgb5_before_linkfix.json
docker exec -e NEO4J_URI=neo4j://database5:7687 -e NEO4J_USERNAME=neo4j -e NEO4J_PASSWORD=kgb-please-change -i backend-parser python - < $D/restore_stored.py
docker cp $D/run_direct.py backend-parser:/tmp/run_direct.py
docker exec -e CHUNK_REFERENCE_MODEL=$2 -e CHUNK_REFERENCE_LINK_STYLE=${3:-outline} -e PYTHONPATH=/code -w /code backend-parser python /tmp/run_direct.py > $D/direct_$1.json
head -c 1500 $D/direct_$1.json; echo
$D/dump.sh $1
python3 $D/judge.py $D/refs_dump_$1.json $D/manual.json
