#!/bin/sh
# 比較の採点。書き出しから数える（Neo4j は使わない）。
#   試運転 6 本: 前回の正解グラフ（gold_graph.json v2）。前のチャンクの 3 方式（methods/ の書き出し）も同じ道具で並べる
#   HS 4 本    : 今回の正解グラフ（hs/gold_hs.json）
cd /home/uchida/ai-stack/llm-graph-builder/tools/kg/hs
K=..; R=runs; OUT=score_out; mkdir -p $OUT
runs6=""; runs4=""
for m in p1_std:標準 p1_schema:人手スキーマ p1_edc_current:EDC現行 p1_edc_quality:EDC＋品質定義; do
  dir=${m%%:*}; label=${m##*:}
  [ -s $R/$dir/graph_dump.json ] || continue
  runs6="$runs6 --run $label=$R/$dir/graph_dump.json@files_vtrt6.txt"
  runs4="$runs4 --run $label=$R/$dir/graph_dump.json@files_hs4.txt"
done
python3 score/recount_hs.py --gold $K/gold_graph.json \
  --run "標準(前のチャンク)=$K/methods/gemma4/graph_dump.json" --run "人手スキーマ(前)=$K/methods/schema_names/graph_dump.json" --run "EDC 4b(前)=$K/methods/edc_fold2/graph_dump.json" \
  $runs6 --out $OUT/vtrt6.json > $OUT/vtrt6.md
python3 score/recount_hs.py --gold gold_hs.json $runs4 --out $OUT/hs4.json > $OUT/hs4.md
head -n 16 $OUT/vtrt6.md; echo; head -n 16 $OUT/hs4.md
