#!/bin/sh
# パーサー置き換えの評価用 backend（本番とは別の容器 backend-parser、ポート 8446）。
#   up   … 本番 backend の環境変数を写し、PDF_CONVERTER=regions・PDF_REGIONS_FIGURES=text を足して起動する。
#          既定の接続先は評価用の Neo4j（引数 2 の別名、例 database5）。本番の Neo4j には書かない
#   down … 止めて消す
# 環境変数の写しは一時ファイル（権限 600）に書き、起動後すぐ消す。中身は表示しない。
set -eu
case "${1:-}" in
up)
  ALIAS=${2:-database5}
  T=$(mktemp); chmod 600 "$T"
  docker inspect backend --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep -v -E '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|LD_LIBRARY_PATH|NEO4J_URI|NEO4J_PASSWORD|NEO4J_USERNAME|NEO4J_DATABASE)=' > "$T"
  # backend は起動時に /code/.env を上書き読み込みする（score.py の load_dotenv(override=True)）。
  # 環境変数だけでは既定の接続先が本番に戻るので、/code/.env を評価用の写しで覆う（写しは secrets の下、権限 600）
  E=/home/uchida/ai-stack/secrets/gb-parser-eval.env
  umask 077
  grep -v -E '^(NEO4J_URI|NEO4J_USERNAME|NEO4J_PASSWORD|NEO4J_DATABASE|PDF_CONVERTER|PDF_REGIONS_FIGURES)=' /home/uchida/ai-stack/llm-graph-builder/backend/.env > "$E"
  printf 'NEO4J_URI=neo4j://%s:7687\nNEO4J_USERNAME=neo4j\nNEO4J_PASSWORD=kgb-please-change\nNEO4J_DATABASE=neo4j\nPDF_CONVERTER=regions\nPDF_REGIONS_FIGURES=text\n' "$ALIAS" >> "$E"
  docker rm -f backend-parser >/dev/null 2>&1 || true
  docker run -d --name backend-parser --memory=6g --network llm-graph-builder_net \
    --add-host host.docker.internal:host-gateway -p 8446:8000 \
    -v /home/uchida/ai-stack/llm-graph-builder/backend:/code -v "$E":/code/.env:ro -v /home/uchida/ai-stack/EDC/schemas:/edc_schemas:ro \
    --env-file "$T" -e NEO4J_URI=neo4j://$ALIAS:7687 -e NEO4J_USERNAME=neo4j -e NEO4J_PASSWORD=kgb-please-change -e NEO4J_DATABASE=neo4j \
    -e PDF_CONVERTER=regions -e PDF_REGIONS_FIGURES=text -w /code llm-graph-builder-backend:latest \
    gunicorn score:app --workers 2 --threads 8 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 600 >/dev/null
  rm -f "$T"
  for i in $(seq 1 60); do curl -s -o /dev/null -w '%{http_code}' http://localhost:8446/health 2>/dev/null | grep -q 200 && break; sleep 3; done
  curl -s http://localhost:8446/health; echo
  docker exec backend-parser python -c "import dotenv,os;dotenv.load_dotenv('/code/.env',override=True);u=os.environ['NEO4J_URI'];print('既定の接続先', u);assert 'database' in u and u!='neo4j://database:7687'"
  ;;
down) docker rm -f backend-parser ;;
*) echo "使い方: $0 up [別名] | down"; exit 2 ;;
esac
