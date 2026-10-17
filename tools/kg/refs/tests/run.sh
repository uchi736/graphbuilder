#!/usr/bin/env bash
# 参照リンク（規則と LLM）の試験と採点を、backend のイメージの一時コンテナで動かす。
# この機体は GPU と CPU が同じメモリを使うので、一時コンテナには必ずメモリの上限を付ける（4GB）。ネットワークなし・GPU なし。
# 稼働中のサービスには触れない。リポジトリは読み取り専用で見せる。
#
#   run.sh            全部（規則の単体 → つなぎ込み → LLM 方式の試験 → 実物の採点）
#   run.sh rules      規則の単体の試験
#   run.sh link       つなぎ込みの試験（実物 72 チャンクの通しを含む）
#   run.sh llm        LLM 方式の試験（mode=llm。LLM とグラフは差し替え）
#   run.sh score ...  実物の正解 41 件の採点（score_real.py の引数をそのまま渡せる。例: score --ablation）
set -u
REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
what="${1:-all}"
[ $# -gt 0 ] && shift
case "$what" in
  rules) cmd="python /tests/t_rules.py" ;;
  link)  cmd="python /tests/t_link.py /kg/routes/graph_dump.json" ;;
  llm)   cmd="CHUNK_REFERENCE_MODE=llm python /tests/t_llm.py" ;;
  score) cmd="python /tests/score_real.py $*" ;;
  all)   cmd="python /tests/t_rules.py && python /tests/t_link.py /kg/routes/graph_dump.json && CHUNK_REFERENCE_MODE=llm python /tests/t_llm.py && python /tests/score_real.py --ablation" ;;
  *) echo "使い方: $0 [all|rules|link|llm|score] [引数]" >&2; exit 2 ;;
esac
exec docker run --rm --memory=4g --memory-swap=4g --cpus=4 --pids-limit=1024 --network none --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 -e KG_DIR=/kg -e CODE_DIR=/code \
  -v "$REPO/backend":/code:ro -v "$REPO/tools/kg/refs/tests":/tests:ro -v "$REPO/tools/kg":/kg:ro \
  --entrypoint sh llm-graph-builder-backend -c "cd /code && $cmd"
