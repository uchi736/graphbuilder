#!/usr/bin/env bash
# 起動した一式が、設定された接続先（Neo4j・生成・埋め込み・EDC・リランク・PDF 変換）に届くかを確かめる。
# backend のコンテナの中から確かめるので、コンテナから見た到達性そのものを見ている。
#
#   scripts/check.sh                 # 既定のプロジェクト
#   scripts/check.sh -p <プロジェクト名>   # docker compose に渡す引数をそのまま足せる
set -euo pipefail
cd "$(dirname "$0")/.."
exec docker compose -f docker-compose.full.yml "$@" exec -T backend python - < scripts/check_endpoints.py
