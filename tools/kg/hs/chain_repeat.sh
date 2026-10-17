#!/bin/sh
cd /home/uchida/ai-stack/llm-graph-builder/tools/kg/hs
docker start neo4j-kgb4 > /dev/null
for i in $(seq 1 40); do docker exec neo4j-kgb4 cypher-shell -u neo4j -p kgb-please-change "RETURN 1" > /dev/null 2>&1 && break; sleep 3; done
python3 qa/repeat_runs.py qa/repeat_runs.json --db database4 --times 5 > qa/repeat_runs.log 2>&1
docker stop neo4j-kgb4 > /dev/null
echo REPEAT_DONE >> qa/repeat_runs.log
