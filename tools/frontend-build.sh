#!/usr/bin/env bash
# フロントを作り直して入れ替える。ビルドはメモリ上限付きのコンテナで行う（統合メモリの機体で学習を落とさないため）。
#   いつ : frontend/src や .env の VITE_ 値を変えたとき
#   守る : ビルドのメモリは 8GB・CPU 4 本まで。入れ替え前のイメージは :bak-<日時> で残す
#   手順 : tools/frontend-build.sh            （ビルド → nginx イメージ → frontend を入れ替え）
#          tools/frontend-build.sh --no-deploy（ビルドまで）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${WORK:-/tmp/gb-frontend-build-$$}"; mkdir -p "$WORK/out" "$WORK/ctx"
trap 'rm -rf "$WORK"' EXIT

# ビルド時の設定値: .env の VITE_ 行 ＋ Dockerfile の既定
grep -E '^VITE_' "$ROOT/.env" > "$WORK/vite.env"
for kv in \
  'VITE_BLOOM_URL=https://workspace-preview.neo4j.io/workspace/explore?connectURL={CONNECT_URL}&search=Show+me+a+graph&featureGenAISuggestions=true&featureGenAISuggestionsInternal=true' \
  'VITE_TIME_PER_PAGE=50' 'VITE_CHUNK_SIZE=5242880' 'VITE_LARGE_FILE_SIZE=5242880' 'VITE_BATCH_SIZE=2' \
  'VITE_GOOGLE_CLIENT_ID=' 'VITE_AUTH0_CLIENT_ID=' 'VITE_AUTH0_DOMAIN=' 'VITE_MAX_TOKEN_CHUNK_SIZE=200,000'; do
  k="${kv%%=*}"; grep -q "^$k=" "$WORK/vite.env" || echo "$kv" >> "$WORK/vite.env"
done

docker volume create gb-frontend-node_modules >/dev/null
docker run --rm --name gb-frontend-build --memory=8g --memory-swap=8g --cpus=4 --env-file "$WORK/vite.env" \
  -v "$ROOT/frontend:/src:ro" -v gb-frontend-node_modules:/app/node_modules -v "$WORK/out:/out" node:20 \
  sh -c 'set -e; mkdir -p /app; cd /src && tar --exclude=node_modules --exclude=dist -cf - . | tar -xf - -C /app; cd /app && yarn install --frozen-lockfile --network-timeout 600000 && yarn run build && rm -rf /out/dist && cp -r dist /out/dist && chown -R '"$(id -u)":"$(id -g)"' /out/dist'

cp -r "$ROOT/frontend/nginx" "$WORK/ctx/" && cp -r "$WORK/out/dist" "$WORK/ctx/dist"
cat > "$WORK/ctx/Dockerfile" <<'EOF'
FROM nginx:alpine
ARG DEPLOYMENT_ENV="local"
ENV DEPLOYMENT_ENV=$DEPLOYMENT_ENV
COPY dist /usr/share/nginx/html
COPY nginx/nginx.${DEPLOYMENT_ENV}.conf /etc/nginx/templates/nginx.conf.template
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
EOF
if docker image inspect llm-graph-builder-frontend:latest >/dev/null 2>&1; then
  docker tag llm-graph-builder-frontend:latest "llm-graph-builder-frontend:bak-$(date +%Y%m%d-%H%M)"
fi
docker build -q -t llm-graph-builder-frontend:latest "$WORK/ctx"
echo "イメージ llm-graph-builder-frontend:latest を作った"
if [ "${1:-}" != "--no-deploy" ]; then
  cd "$ROOT"
  docker rm -f frontend >/dev/null 2>&1 || true
  docker compose up -d --no-deps --no-build frontend
  docker update --memory 512m --memory-swap 512m frontend >/dev/null
  sleep 2; curl -s -o /dev/null -w "frontend http %{http_code}\n" http://localhost:8441/
fi
