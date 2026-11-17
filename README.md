# Knowledge Graph Builder（日本語化・オンプレ構成）

[neo4j-labs/llm-graph-builder](https://github.com/neo4j-labs/llm-graph-builder) をベースに、**日本語化・完全ローカル（オンプレ）運用・EDC抽出エンジン統合・共有グラフ対応**を加えたフォークです。
非構造化データ（PDF / テキスト / Markdown / Web / Wikipedia / YouTube）から Neo4j 上にナレッジグラフを構築し、チャットで検索します。

> 本家のオリジナルREADME（英語・上流の汎用手順）は [README.upstream.md](README.upstream.md) に退避しています。
> 本家との差分の全体像・3リポジトリ構成・稼働サービスは **[docs/SYSTEM.md](docs/SYSTEM.md)** を参照してください。

## 本家からの主な変更点

| 分類 | 内容 |
|---|---|
| UI | フロントエンド全体を日本語化 |
| 抽出エンジン | **EDC**（Extract-Define-Canonicalize）をHTTPサイドカーとして統合。画面の「抽出方式」で選ぶ（統制スキーマ抽出。LLM の選択とは別で、EDC の LLM は EDC 側の設定で固定） |
| LLM | ローカル gemma4（vLLM・OpenAI互換）対応。モデル名 `gemma4` |
| 埋め込み | vLLM上の **ruri-v3-310m**（日本語特化・768次元）へ移行。EDCと共用 |
| 名寄せ | エンティティ埋め込み（`ENTITY_EMBEDDING`）による重複検出を有効化 |
| 前処理 | EDC `/convert`（PaddleX OCR）でスキャンPDF対応、Markdown見出しパンくずチャンキング |
| 共有グラフ | 別プロジェクト [graphrag](https://github.com/uchi736/graphrag) のGraph-First検索を同一Neo4j上で動かすための共存ハードニング |

## システム構成

Neo4j・backend・frontend・EDC は本リポジトリの `docker-compose.full.yml` 1 本で起動する。モデルの配信は含まず、`.env` に接続先を書く（詳細は [docs/SYSTEM.md](docs/SYSTEM.md)）。

| コンポーネント | 既定のポート | 提供元 |
|---|---|---|
| frontend | 8441 | 本リポジトリ（`frontend/`） |
| backend | 8440 | 本リポジトリ（`backend/`） |
| Neo4j 5.26 Community + APOC | 7475 / 7688 | 本リポジトリ（compose） |
| edc-api（スキーマ統制の抽出） | 8081 | 本リポジトリ（`edc-api/`。[EDC](https://github.com/uchi736/EDC) の複製、取り込み元は `edc-api/VENDORED.md`） |
| 生成（OpenAI 互換） | 8000 | 外部。`.env` の `LLM_BASE_URL` |
| 埋め込み（ruri-v3-310m、768 次元） | 8003 | 外部。`.env` の `EMBED_BASE_URL` |
| リランク（ruri-v3-reranker-310m） | 8006 | 外部。`.env` の `RERANK_URL`（無い環境は `CHAT_RERANK_ENABLED=false`） |
| PDF の構造の読み取り（Nemotron Parse） | 8022 | 外部・任意。`.env` の `PARSE_URL`（届かないときは文字層だけの簡易変換に戻る） |
| OCR（PaddleX） | 8005 | 外部・任意。`.env` の `OCR_URL`（スキャン PDF を EDC の `/convert` で読む場合のみ） |
| 文書台帳（PostgreSQL） | 5432 | 外部・任意。`.env` の `DOCUMENT_REGISTRY_DSN`（作り方は `tools/kg/registry/setup_db.sh`） |

## 起動手順（オンプレ / docker compose）

### 前提

- Docker Compose v2.24 以降
- 生成・埋め込み・リランクの各サーバーが稼働しており、コンテナから届くこと

### 1. 設定

```bash
cp .env.example .env
```

`.env` の「1. モデルの接続先」と「2. 画面」（ブラウザから見たホスト名 `PUBLIC_HOST`）を書く。同じホストでモデルを配信している場合、接続先は既定値（`host.docker.internal`）のままでよい。`backend/.env` と `frontend/.env` は不要である（`backend/.env` を置いた場合はその値が優先される）。

### 2. トークナイザーの取得（初回のみ）

```bash
scripts/fetch_tokenizers.sh
```

チャンクの長さを数えるトークナイザーと tiktoken のキャッシュを `backend/tokenizers/`（git の追跡外）に置く。**導入時にこのスクリプトだけが社外へ通信する**（huggingface.co と openaipublic.blob.core.windows.net）。社外へ出られない環境では、出られる環境で作った `backend/tokenizers/` をフォルダごと運ぶ。

### 3. ビルドと起動

```bash
docker compose -f docker-compose.full.yml up -d --build
```

### 4. 接続先の点検

```bash
scripts/check.sh
```

backend のコンテナの中から、Neo4j・生成・埋め込み・EDC・リランク・トークナイザーを確かめる。`NG` が出た項目は `.env` を直して 3. をやり直す。

ブラウザで `http://<PUBLIC_HOST>:8441` を開き、接続する（Neo4j の接続先は `neo4j://database:7687`、利用者 `neo4j`、パスワードは `.env` の `NEO4J_PASSWORD`）。

### 5. 初回のみ

1. 接続時にベクトル索引の次元不一致アラートが出たら索引を再作成（旧384 → ruri 768）
2. 文書を取り込み（抽出方式は「EDC（スキーマ統制）」推奨、「標準」も可。LLM は `gemma4`）
3. グラフ強化メニューから**後処理を実行**（フルテキスト索引・SIMILAR・エンティティ埋め込み）

### 補足

- **LLM の名前 `gemma4`**: 画面と設定に出る `gemma4` は接続先の枠の名前であり、`.env` の `LLM_MODEL` に別のモデルを書いた場合もこの名前のまま使う。
- **frontend の作り直し**: `PUBLIC_HOST`・`BACKEND_PORT`・`VITE_*` はビルド時に画面へ焼き込む。変えた場合は `docker compose -f docker-compose.full.yml up -d --build frontend`。
- **EDC の更新**: `edc-api/` は直接編集せず、取り込み元を直してから `scripts/sync_edc.sh <EDC の checkout>` で取り込み直す。文書タイプとスキーマ（`edc-api/schemas/`）はマウントしているので、イメージの作り直しは不要である。
- **`all predefined address pools have been fully subnetted` で起動できない場合**: Docker の既定のアドレスを使い切っている。`networks.net.ipam.config` に空いているサブネットを書いた compose ファイルを `-f` で重ねる。
- **`docker-compose.yml`（従来）**: EDC を別の compose で起動する従来の構成。新しく構築する環境では `docker-compose.full.yml` を使う。

## 抽出方式と LLM

| 選ぶもの | 値 | 用途 | 備考 |
|---|---|---|---|
| 抽出方式 | 標準（standard） | LLM でエンティティとリレーションを抽出（スキーマは任意） | LLM は画面で選んだもの |
| 抽出方式 | EDC（edc） | 統制スキーマで抽出（抽出→定義→正規化） | EDC の LLM は EDC 側の設定で固定。Document.extraction_method に edc、Document.model に EDC の LLM を残す |
| LLM | gemma4 | 標準の抽出・チャット生成・後処理 | ローカル vLLM |

抽出方式は取り込みごとに選べるため、EDC と標準の結果を同じ Neo4j に混在させられる（名寄せで統合可能）。続きからの再処理は前回と同じ抽出方式に限る。API では /extract の extraction_method（旧来の model=edc も EDC として扱う）、選択肢は GET /extraction_methods。

## 図解・ドキュメント

- [docs/SYSTEM.md](docs/SYSTEM.md) — 3リポジトリ構成・稼働サービス・リポジトリ間の接点
- EDC連携アダプタ: [backend/src/edc_extraction.py](backend/src/edc_extraction.py)
- 共有グラフ検証手順は graphrag リポジトリの `docs/gb_shared_graph_eval.md`

## ライセンス

本家 [neo4j-labs/llm-graph-builder](https://github.com/neo4j-labs/llm-graph-builder) に準拠（Apache-2.0）。
