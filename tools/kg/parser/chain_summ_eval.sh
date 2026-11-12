#!/bin/sh
# 節の要点と一覧選択の回答評価（計画 docs/section-summaries/PLAN.md の評価）。評価用の kgb4（HS 56 本、11/9 夜間の今の変換のグラフ、要点 581 件作成済み）で、
# 参照追跡の rerank（今の選び方）と summary_list（一覧選択）を比べる。評価用 backend（backend-parser）に接続先を明示して流す。本番には書かない。
set -u
cd /home/uchida/ai-stack/llm-graph-builder/tools/kg
export GB_BACKEND=http://localhost:8446
log() { echo "== $* $(date '+%F %T')"; }
docker start neo4j-kgb4 >/dev/null; sleep 25
log "81 問（filtered.json）を kgb4 で 参照追跡 rerank / summary_list"
(cd refsec && GB_URI=neo4j://database4:7687 GB_USER=neo4j GB_PASSWORD=kgb-please-change GB_DATABASE=neo4j EVAL_CONDITIONS=ref_mode,ref_list ALL=1 \
   python3 evaluate_night1109.py filtered.json summ/results81_kgb4.json > summ/eval81_kgb4.log 2>&1; python3 score.py summ/results81_kgb4.json > summ/score81_kgb4.log 2>&1)
tail -n 10 refsec/summ/score81_kgb4.log
log "HS 23 問＋対照 11 問を kgb4 で 参照追跡 / 参照追跡一覧"
(cd hs && python3 qa/eval_hs.py runs/full56_night1109/graph_dump_linked.json qa/eval_full56_summlist.json --db database4 \
   --settings 参照追跡,参照追跡一覧 --control-settings 参照追跡,参照追跡一覧 > qa/eval_full56_summlist.log 2>&1)
grep -E '^(agg|confusable|control|ent1|ent2|ref1)' hs/qa/eval_full56_summlist.log
docker stop neo4j-kgb4 >/dev/null
log "完了"
