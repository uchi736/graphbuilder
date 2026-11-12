#!/bin/bash
# tools/kg/registry の道具を一時コンテナで動かす（psycopg の入った terminology-dict のイメージを借りる。--network host でホストの
# Neo4j 7475・Postgres 5432 に届く）。接続情報は backend/.env と ~/ai-stack/secrets/document-registry.env から渡し、画面には出さない。
# 使い方（ai-stack/llm-graph-builder で）: bash tools/kg/registry/run.sh seed_from_graph.py [--dry-run] [--refresh-seed]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
SCRIPT="$1"; shift
exec docker run --rm --memory=512m --cpus=1 --network host --entrypoint python \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$HERE:/w:ro" -v "$ROOT/backend/.env:/backend.env:ro" \
  --env-file "$HOME/ai-stack/secrets/document-registry.env" \
  terminology-dict:latest "/w/$SCRIPT" "$@"
