# フロントエンド（React + Vite）

Knowledge Graph Builder のフロントエンド。React + Vite 製で、Neo4j Design Language（NDL）を用いたUI。
ファイルのアップロード、抽出進捗の表示、グラフ可視化、チャットを提供する。**UIは日本語化済み。**

> 全体構成・起動手順はリポジトリルートの [../README.md](../README.md) と [../docs/SYSTEM.md](../docs/SYSTEM.md) を参照。
> 本ドキュメントはフロントエンド単体の開発者向け。

## 機能

- ドラッグ&ドロップでのファイル取り込み（ローカル / S3 / GCS / Web / Wikipedia / YouTube）
- モデル選択（`gemma4` / `edc`）— 一覧は `VITE_LLM_MODELS` で制御
- グラフ可視化・スキーマ表示・チャット（複数モード）
- Neo4j 自動再接続（localStorage のセッション利用）

## ローカル開発

```bash
yarn install
yarn run dev     # Vite 開発サーバ（--host 0.0.0.0）
```

ビルド / プレビュー:

```bash
yarn run build   # tsc + vite build
yarn run preview
```

## docker-compose 起動（推奨）

ルートの docker-compose 経由で起動する（ルート [../README.md](../README.md) 参照）。
frontend はコンテナ内 :8080（nginx配信）、ホスト側は **:8441** に公開される。

## 環境変数（`VITE_*` はビルド時に焼き込み）

`VITE_*` 変数は **ビルド時に静的に埋め込まれる**ため、変更したら frontend の再ビルドが必要。
compose ではビルド引数として渡す（ルート `.env` / `docker-compose.yml` 参照）。

| 変数 | 用途 |
|---|---|
| `VITE_BACKEND_API_URL` | バックエンドAPIのURL（例 `http://<ホストIP>:8440`） |
| `VITE_LLM_MODELS` | モデル選択肢（本フォークでは `gemma4,edc`） |
| `VITE_CHAT_MODES` | チャットモード一覧 |
| `VITE_REACT_APP_SOURCES` | 入力ソース一覧（local,youtube,wiki,s3 など） |
| `VITE_CHUNK_SIZE` / `VITE_CHUNK_OVERLAP` / `VITE_TOKENS_PER_CHUNK` | チャンク設定の既定値 |

`.env` は **gitignore 済み**。必要な変数はルート `.env` にまとめて定義し、compose のビルド引数から渡す。
