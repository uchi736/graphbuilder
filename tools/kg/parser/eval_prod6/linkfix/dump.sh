#!/bin/sh
# 評価用グラフ（kgb5）の章・節への参照を書き出す。使い方: dump.sh <名前>  → linkfix/refs_dump_<名前>.json
set -eu
cd /home/uchida/ai-stack/llm-graph-builder
D=tools/kg/parser/eval_prod6/linkfix
docker exec backend-parser mkdir -p /out
docker exec -e NEO4J_URI=neo4j://database5:7687 -e NEO4J_USERNAME=neo4j -e NEO4J_PASSWORD=kgb-please-change -i backend-parser python - < tools/kg/refsec/dump_refs.py
docker cp backend-parser:/out/refs_dump.json $D/refs_dump_$1.json
docker exec -e NEO4J_URI=neo4j://database5:7687 -e NEO4J_USERNAME=neo4j -e NEO4J_PASSWORD=kgb-please-change -i backend-parser python - snap_$1 < $D/snapshot.py
docker cp backend-parser:/out/snap_$1.json $D/kgb5_snap_$1.json
