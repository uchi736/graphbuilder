#!/usr/bin/env bash
# 抽出完了を待ち、④→評価→③→評価 を逐次実行
S=/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/std
until grep -q '^done:' $S/extract.log 2>/dev/null; do sleep 20; done
echo "抽出完了 $(date +%H:%M:%S)"; docker cp $S/req_extract.json backend:/tmp/req_extract.json
score() { docker cp $S/gold_graph.json backend:/tmp/gold_graph.json; docker exec -i backend python - /tmp/gold_graph.json < $S/score_gold.py 2>&1 | grep -vE 'notification|Warning' > $S/gold_$1.json; python3 -c "
import json,re,sys; d=json.loads(re.search(r'\{.*\}',open('$S/gold_$1.json',encoding='utf-8').read(),re.S).group(0)); e=d['entities']; r=d['relations']; dr=d['doc_references']
print(f'  [$1] ents {e[\"found\"]}/{e[\"gold\"]} frag {e[\"fragmented(>1node)\"]} avg {e[\"avg_nodes_per_found\"]} type_ok {e[\"type_match\"]} | rels any {r[\"any_edge\"]}/{r[\"gold\"]} typed {r[\"typed\"]} | refs std {dr.get(\"standard_refs\")}/{dr[\"gold\"]}')"; }
for P in p4 p3; do
  echo "===== $P ====="
  docker exec -i backend python - clean < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
  docker exec -i backend python - $P < $S/req_materialize.py 2>&1 | grep -vE 'notification|Warning'
  $S/metrics.sh "$P" 2>&1 | sed -n '/--- 全体/,/--- ラベル/p' | head -4
  score $P
  for m in graph_vector_fulltext graph_vector; do echo "--- multihop $m ---"; QFILE=$S/eval_multihop.json QTAG=multihop_$P $S/run_eval.sh $m '[]' 2>&1 | tail -17; done
done
echo "===== 完了 ====="
