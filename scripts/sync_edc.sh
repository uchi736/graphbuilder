#!/usr/bin/env bash
# EDC（https://github.com/uchi736/EDC）の実行に要るファイルを edc-api/ へ取り込み直す。
#
#   scripts/sync_edc.sh <EDC の checkout のパス>
#
# 取り込むもの: API 本体・edc パッケージ・プロンプト・手本・スキーマ・試験・Dockerfile。
# 取り込まないもの: CLI（run.py）・Web UI（app.py）・データセット・評価・文書。
set -euo pipefail
SRC="${1:?EDC の checkout のパスを渡す}"
cd "$(dirname "$0")/.."
[ -f "$SRC/api.py" ] || { echo "$SRC に api.py が無い" >&2; exit 1; }

if [ -n "$(git -C "$SRC" status --porcelain -- api.py doctype_router.py pdf_processor.py edc prompt_templates few_shot_examples schemas tests Dockerfile requirements-api.txt)" ]; then
  echo "注意: 取り込み元に未コミットの変更がある（そのまま取り込む）" >&2
fi

mkdir -p edc-api
rsync -a --delete --exclude='*.bak*' --exclude='__pycache__' --exclude='*.pyc' \
  "$SRC"/edc "$SRC"/prompt_templates "$SRC"/few_shot_examples "$SRC"/schemas "$SRC"/tests edc-api/
rsync -a "$SRC"/{Dockerfile,.dockerignore,requirements-api.txt,api.py,doctype_router.py,pdf_processor.py,LICENSE} edc-api/

cat > edc-api/VENDORED.md <<MD
# EDC の取り込み元

このフォルダは EDC（Extract-Define-Canonicalize）の API の複製である。直接は編集せず、取り込み元を直してから
\`scripts/sync_edc.sh <EDC の checkout>\` で取り込み直す。

| 項目 | 値 |
|---|---|
| 取り込み元 | $(git -C "$SRC" remote get-url origin 2>/dev/null || echo "$SRC") |
| コミット | $(git -C "$SRC" rev-parse HEAD) |
| コミットの日付 | $(git -C "$SRC" log -1 --format=%cd --date=short) |
| ライセンス | MIT（LICENSE） |

文書タイプとスキーマは \`schemas/\`（\`registry.json\` が一覧）。compose がこのフォルダを edc-api と backend の両方に
読み取り専用でマウントするので、追加・変更はイメージを作り直さずに反映される。
MD
echo "取り込み完了: $(git -C "$SRC" rev-parse --short HEAD)"
