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

3リポジトリ + ホスト常駐サービスで構成される（詳細は [docs/SYSTEM.md](docs/SYSTEM.md)）。

| コンポーネント | ポート | 提供元 |
|---|---|---|
| frontend | 8441 | 本リポジトリ（docker-compose） |
| backend | 8440 | 本リポジトリ（docker-compose） |
| Neo4j 5.26 Community + APOC | 7475 / 7688 | 本リポジトリ（docker-compose） |
| edc-api | 8080 | [EDC](https://github.com/uchi736/EDC)（別compose） |
| vLLM chat (gemma4) | 8000 | ホスト常駐プロセス |
| vLLM embedding (ruri-v3-310m) | 8003 | ホスト常駐プロセス（GB / EDC / graphrag 共用） |
| PaddleX OCR | 8005 | ホスト常駐プロセス（EDC `/convert` 用） |

## 起動手順（オンプレ / docker-compose）

### 前提

- Neo4j 5.23 以降相当（本構成は同梱の 5.26 Community）
- ホスト側で vLLM（chat :8000 / embedding :8003）が稼働していること
- EDCを使う場合は EDCコンテナ（:8080）とPaddleX OCR（:8005）が稼働していること

### 1. 環境変数

`.env`（リポジトリルート）/ `backend/.env` / `frontend/.env` は **gitignore されている**ため、clone後に用意する。
`backend/example.env` を雛形に作成し、最低限このオンプレ構成のキーを設定する:

```bash
# 抽出LLM（ローカル gemma4 vLLM, OpenAI互換）
LLM_MODEL_CONFIG_GEMMA4=google/gemma-4-26B-A4B-it,http://host.docker.internal:8000/v1,dummy
# EDC抽出エンジン（抽出専用）: <base_url>[,<doctype>[,<chunk_method>[,<concurrency>]]]
LLM_MODEL_CONFIG_EDC=http://host.docker.internal:8080,auto,recursive,2

# 埋め込み（vLLM ruri-v3-310m, 768次元）
EMBEDDING_PROVIDER=vllm
EMBEDDING_MODEL=cl-nagoya/ruri-v3-310m
VLLM_EMBEDDING_ENDPOINT=http://192.168.0.250:8003/v1,dummy
IS_EMBEDDING=true
ENTITY_EMBEDDING=true

# 前処理・共有グラフ
EDC_PDF_CONVERT=true
MARKDOWN_CHUNKING=true
GRAPH_SCHEMA_CONSOLIDATION_ENABLED=false

# フロント（ビルド時に焼き込み。変更時は frontend 再ビルド要）
VITE_BACKEND_API_URL=http://<ホストIP>:8440
VITE_LLM_MODELS=gemma4
VITE_CHAT_MODES=vector,graph_vector,graph,fulltext,graph_vector_fulltext,entity_vector
```

### 2. ビルドと起動

```bash
docker compose build backend frontend
docker compose up -d
```

ブラウザで `http://<ホストIP>:8441` を開き、接続する。

### 3. 初回のみ

1. 接続時にベクトル索引の次元不一致アラートが出たら索引を再作成（旧384 → ruri 768）
2. 文書を取り込み（抽出方式は「EDC（スキーマ統制）」推奨、「標準」も可。LLM は `gemma4`）
3. グラフ強化メニューから**後処理を実行**（フルテキスト索引・SIMILAR・エンティティ埋め込み）

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
