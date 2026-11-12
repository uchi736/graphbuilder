# 開発用の画面（frontend）の起動手順

対象: 本番の作業ツリー `~/ai-stack/llm-graph-builder`（グラフの手動修正の 1 期を反映した後）。設計の正本は同じ場所の `docs/graph-edit/DESIGN.md`（6 章が画面）である。

## 目的と構成

グラフの手動修正の画面を、稼働中の画面（:8441）と稼働中のバックエンド（:8440）に触れずに確かめるための手順である。

- 画面は vite の開発サーバーで出す。node:20 のコンテナを `--network host` で動かし、`127.0.0.1:8451` で待ち受ける（この機体の中からだけ開ける）。
- 画面が呼ぶバックエンドは本番のコードの試験用バックエンド `backend-c6test`（`http://127.0.0.1:8452`）とし、`VITE_BACKEND_API_URL` で指定する。`backend-c6test` は本番の `backend/` を読み取り専用で載せ、`/code/.env` を試験用の接続先に差し替えたものである（ホストの 8003 は常駐の埋め込みの番号なので使わない）。
- バックエンドの接続先は試験用 Neo4j（`neo4j-gbdev`、ネットワーク上の名前 `database-gbdev:7687`）である。
- 部品（node_modules）は作業ツリーではなくボリューム `gbdev-frontend-node-modules` に置く（yarn.lock は dev の作業ツリーと同じなので、そのまま使える）。作業ツリーに root 所有の部品のファイルを作らないためである。

守ること:
- 開発用の画面を稼働中の Neo4j（neo4j-kgb、neo4j-kgb2）につながない。接続先の表示が `database-gbdev` であることを確かめてから操作する。
- 一時コンテナには必ず `--memory` を付ける。開発サーバーと試験用バックエンドは確かめ終えたら止める（常駐させない）。
- 社外へ通信するのは手順 1 の部品の導入だけである（npm のレジストリ。版は yarn.lock で固定）。手順 2 以降は `--network none`、または機体の中の宛先だけである。
- 本番の画面のビルド（`tools/frontend-build.sh`）と、同じ部品のボリュームを使う型検査を同時に流さない。

## 索引

| 手順 | いつ | なぜ |
|---|---|---|
| 1. 部品の導入 | ボリュームが無いときと、yarn.lock が変わったとき | 型検査・リント・開発サーバーに node_modules が要る |
| 2. 型検査とリント | 画面のコードを変えたとき | 型の誤りと、変更したファイルのリントを見つける |
| 3. 起動 | 画面で動きを確かめるとき | 稼働中の画面とバックエンドに触れずに確かめる |
| 4. 接続と操作 | 起動の後 | 試験用 Neo4j に向いていることを確かめてから操作する |
| 5. 停止 | 確かめ終えたとき | 常駐させない。試験用 Neo4j を写しに戻す |

## 1. 部品の導入

```sh
docker volume create gbdev-frontend-node-modules
docker run --rm --memory=4g --cpus=4 \
  -v /home/uchida/ai-stack/llm-graph-builder/frontend:/app \
  -v gbdev-frontend-node-modules:/app/node_modules -w /app \
  node:20 sh -c "yarn install --frozen-lockfile --ignore-scripts"
```

`--ignore-scripts` は husky の導入（postinstall）を走らせないためである。

## 2. 型検査とリント

作業ツリーを読み取り専用で載せ、コンテナの中の写しで検査する（作業ツリーに何も書かない）。部品は本番の画面のビルドと同じボリューム `gb-frontend-node_modules` を読み取り専用で使う。

```sh
# 型検査（全体）
docker run --rm --network none --memory=4g --memory-swap=4g --cpus=4 \
  -v /home/uchida/ai-stack/llm-graph-builder/frontend:/src:ro \
  -v gb-frontend-node_modules:/app/node_modules:ro node:20 \
  sh -c 'mkdir -p /app && cd /src && tar --exclude=node_modules --exclude=dist -cf - . | tar -xf - -C /app && cd /app && ./node_modules/.bin/tsc --noEmit -p .'

# リント（変更したファイルだけ。例）
docker run --rm --network none --memory=4g --memory-swap=4g --cpus=4 \
  -v /home/uchida/ai-stack/llm-graph-builder/frontend:/src:ro \
  -v gb-frontend-node_modules:/app/node_modules:ro node:20 \
  sh -c 'mkdir -p /app && cd /src && tar --exclude=node_modules --exclude=dist -cf - . | tar -xf - -C /app && cd /app && ./node_modules/.bin/eslint src/components/Graph/GraphViewModal.tsx src/components/Graph/GraphEdit/*.tsx src/services/GraphEdit.ts src/utils/GraphEditUtils.ts'
```

`npx` ではなく `./node_modules/.bin/` を直接呼ぶ。`npx` は npm の更新の確認で社外へ通信するためである。

整形は lint-staged と同じく prettier → `eslint --fix` の順に当てる（このプロジェクトは `no-confusing-arrow` を有効にしており、prettier が外した括弧を `eslint --fix` が付け直す）。もともと prettier の形になっていないファイル（例: `Deduplication/index.tsx`、`utils/Constants.ts`、`GraphViewModal.tsx`）には prettier をかけず、追加した行だけを prettier の形で書く。

## 3. 起動

```sh
S=<作業場>   # gbtest.env と gbtest.runtime.env（DESIGN.md の 7 章。権限 600）を置く所
# 試験用バックエンド（本番の backend を読み取り専用で載せ、接続先は試験用 Neo4j）
docker run -d --rm --name backend-c6test --memory=4g --cpus=4 --network llm-graph-builder_net \
  -p 127.0.0.1:8452:8000 --env-file $S/gbtest.runtime.env -e PYTHONDONTWRITEBYTECODE=1 \
  -v /home/uchida/ai-stack/llm-graph-builder/backend:/code:ro -v $S/gbtest.env:/code/.env:ro \
  --tmpfs /code/logs --tmpfs /code/chunks --tmpfs /code/merged_files \
  -v /home/uchida/ai-stack/EDC/schemas:/edc_schemas:ro -w /code llm-graph-builder-backend:latest \
  gunicorn score:app --workers 2 --threads 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 300
curl -s -m5 -X POST 127.0.0.1:8452/backend_connection_configuration   # database-gbdev を指すことを確かめる

# 画面（VITE_* は本番の画面のビルド引数と同じ値にし、VITE_BACKEND_API_URL だけを試験用バックエンドに替える）
grep -E '^VITE_' /home/uchida/ai-stack/llm-graph-builder/.env | grep -v '^VITE_BACKEND_API_URL=' > $S/vite-c6.env
echo VITE_BACKEND_API_URL=http://127.0.0.1:8452 >> $S/vite-c6.env
docker run --rm -d --name c6-frontend-dev --network host --memory=2g --cpus=2 --env-file $S/vite-c6.env \
  -v /home/uchida/ai-stack/llm-graph-builder/frontend:/app \
  -v gbdev-frontend-node-modules:/app/node_modules -w /app \
  node:20 sh -c "./node_modules/.bin/vite --host 127.0.0.1 --port 8451 --strictPort"

# 応答の確認（200 が返れば起動している。初回は依存の事前変換で数十秒かかる）
until curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8451/ | grep -q 200; do sleep 2; done
docker logs --tail 20 c6-frontend-dev
```

- 別の端末のブラウザで開くときは、2 つのポートを両方とも転送する（画面がブラウザから 8452 を直接呼ぶため）: `ssh -L 8451:127.0.0.1:8451 -L 8452:127.0.0.1:8452 <ユーザー>@<この機体>`

## 4. 接続と操作

1. ブラウザで `http://127.0.0.1:8451` を開く。試験用バックエンドは環境変数の接続情報を返すので、画面は自動で `neo4j://database-gbdev:7687` に接続する。画面上部の「Neo4j接続」の表示が `neo4j://database-gbdev:7687 / neo4j` であることを確かめる。
2. 接続の画面が出たときは、プロトコル `neo4j`、URI `database-gbdev:7687`、データベース `neo4j`、ユーザー名 `neo4j`、パスワード `gbdev-test-only` を入れる。
3. 文書の一覧からグラフを開き（文書を選んで「グラフをプレビュー」、または各行の「グラフ表示」のボタン）、グラフの右上のボタン列の鉛筆のボタン（「グラフを編集」）で編集の状態に切り替える。
   - クリックで 1 つを選び、Ctrl か Shift を押しながらのクリックで追加して選ぶ。
   - ノードかリレーションの右クリックで操作の一覧を開く（エンティティ: 編集・統合の対象に追加・選んだ 2 つの間にリレーションを追加・削除、リレーション: 種類を変更・向きを反転・削除）。
   - 右の詳細欄の「エンティティを名前で探す」で選んだエンティティは、1 ホップ先と一緒に表示に加わる（文書で絞っていても表示に残る）。
   - 章・節（`Section`）を右クリックすると操作は無効で、理由が出る。規則で作った文書番号・型式（`method = 'rule'`）は「編集」「削除」が無効で、「統合の対象に追加」が使える。
4. 操作の履歴と取り消し、修正台帳の行の一覧と削除は「グラフ設定」の「保守」タブの中の「修正台帳」にある。重複ノードのタブ（「グラフの整備」の中）の各行には「別物として登録」がある。
5. 読み取り専用の接続では編集の操作を出さない。確かめるときは `http://127.0.0.1:8451/readonly` を開く。

写しの文書ノードの日付（`createdAt`・`updatedAt`）が文字列になる問題（APOC の書き出しと読み込みによる）は、`restore_gbdev.sh` の最後で DateTime に直す。

編集の操作は試験用バックエンドの `/graph_edit/*`（DESIGN.md の 3 章）を呼ぶ。

## 5. 停止

```sh
docker stop c6-frontend-dev backend-c6test    # --rm 付きで起動しているので、止めると消える
/home/uchida/ai-stack/gbdev-testbed/restore_gbdev.sh gbdev_1031   # 試験用 Neo4j を書き換えたときだけ
```

使い終えた `gbtest.env`・`gbtest.runtime.env`・`vite-c6.env` は消す。
