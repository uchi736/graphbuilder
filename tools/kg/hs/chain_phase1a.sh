#!/bin/sh
# 比較の前半: 語彙の要らない 2 方式を、採点できる 10 本で流す（1 本ずつ Neo4j を起こし、済んだら止める）
cd /home/uchida/ai-stack/llm-graph-builder/tools/kg/hs
ONLY=$(cat gold_docs.txt)
python3 run_hs_method.py edc neo4j-kgb5 database5 p1_edc_current --only "$ONLY" > runs_p1_edc_current.log 2>&1
python3 run_hs_method.py std neo4j-kgb4 database4 p1_std --only "$ONLY" > runs_p1_std.log 2>&1
echo PHASE1A_DONE > phase1a.done
