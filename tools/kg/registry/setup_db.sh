#!/bin/bash
# 文書台帳の正本の表を pgvector の Postgres に作る。何度流してもよい。
#   データベース document_registry、利用者 registry_writer（読み書き。tools の道具と人の修正用）・registry_reader（読み取りのみ。backend 用）
#   パスワードは初回に乱数で作り ~/ai-stack/secrets/document-registry.env（600）に書く。2 回目以降はそれを使う。画面には出さない。
# いつ: 初回の設置時と、schema.sql を直したとき。  使い方: bash tools/kg/registry/setup_db.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SECRETS="${HOME}/ai-stack/secrets/document-registry.env"
PG=pgvector
DB=document_registry

if [ ! -f "$SECRETS" ]; then
  umask 077
  W=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)
  R=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)
  cat > "$SECRETS" <<ENV
# 文書台帳の正本の表（pgvector の document_registry）。tools/kg/registry/setup_db.sh が作成
REGISTRY_WRITER_PASSWORD=$W
REGISTRY_READER_PASSWORD=$R
# backend（llm-graph-builder_net、extra_hosts の host.docker.internal → ホストの 5432）から読むときの接続先
DOCUMENT_REGISTRY_DSN=postgresql://registry_reader:$R@host.docker.internal:5432/$DB
# ホスト（--network host の一時コンテナ）から書くときの接続先
DOCUMENT_REGISTRY_WRITER_DSN=postgresql://registry_writer:$W@127.0.0.1:5432/$DB
ENV
  chmod 600 "$SECRETS"
  echo "パスワードを作成した: $SECRETS"
fi
# shellcheck disable=SC1090
source "$SECRETS"

psql_su() { docker exec -i "$PG" psql -U postgres -v ON_ERROR_STOP=1 -q "$@"; }

psql_su -d postgres <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'registry_writer') THEN CREATE ROLE registry_writer LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'registry_reader') THEN CREATE ROLE registry_reader LOGIN; END IF;
END \$\$;
ALTER ROLE registry_writer PASSWORD '${REGISTRY_WRITER_PASSWORD}';
ALTER ROLE registry_reader PASSWORD '${REGISTRY_READER_PASSWORD}';
SQL
if [ "$(docker exec "$PG" psql -U postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$DB'")" != "1" ]; then
  docker exec "$PG" psql -U postgres -q -c "CREATE DATABASE $DB OWNER registry_writer"
  echo "データベースを作成した: $DB"
fi
psql_su -d "$DB" <<SQL
REVOKE ALL ON DATABASE $DB FROM PUBLIC;
GRANT CONNECT ON DATABASE $DB TO registry_writer, registry_reader;
SQL
docker exec -i "$PG" psql -U registry_writer -d "$DB" -v ON_ERROR_STOP=1 -q < "$HERE/schema.sql"
psql_su -d "$DB" <<SQL
GRANT USAGE ON SCHEMA public TO registry_reader;
GRANT SELECT ON document_registry, document_registry_history TO registry_reader;
SQL
docker exec "$PG" psql -U postgres -d "$DB" -Atc "SELECT 'document_registry: ' || count(*) || ' 行' FROM document_registry"
