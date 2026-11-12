# バックエンド（FastAPI）

Knowledge Graph Builder のバックエンド。FastAPI 製で、文書の取り込み・チャンク化・エンティティ抽出・Neo4j保存・チャット検索のAPIを提供する。
エントリポイントは [score.py](score.py)（`score:app`）。

> 全体構成・起動手順・オンプレ構成の要点はリポジトリルートの [../README.md](../README.md) と [../docs/SYSTEM.md](../docs/SYSTEM.md) を参照。
> 本ドキュメントはバックエンド単体の開発者向け。

## 前提

- Python 3.12 以上
- Neo4j 5.23 以降（本構成は compose 同梱の 5.26 Community + APOC）
- 抽出・チャットに使うLLM/埋め込みサービス（オンプレ構成では vLLM chat :8000 / embedding :8003、EDC :8080）

## このフォークの主な追加

| 機能 | 実装 |
|---|---|
| EDC抽出エンジン統合（抽出方式 `edc`。/extract の `extraction_method`、旧来の `model=edc` も可） | [src/edc_extraction.py](src/edc_extraction.py)（チャンク毎に EDC API `/extract` を呼びトリプルを GraphDocument へ変換）。方式の判定・EDC の LLM の名前・`GET /extraction_methods`（EDC の項目は、backend の現在の EDC 設定 `settings`、共通の語彙の件数 `vocabulary`、文書種別ごとのノードラベル数・リレーションタイプ数を含む。URL と資格情報は返さない）は [src/extraction_methods.py](src/extraction_methods.py) |
| グラフスキーマ設定欄の選択肢の件数（`POST /schema_options`、参照のみ） | [src/schema_options.py](src/schema_options.py)（ラベルごとのノード数とリレーションタイプごとのリレーション数。対象は `POST /graph_edit/vocabulary` と同じ規則） |
| ローカルLLM `gemma4`（vLLM・OpenAI互換） | [src/llm.py](src/llm.py) |
| vLLM埋め込み `ruri-v3-310m`（768次元） | [src/shared/common_fn.py](src/shared/common_fn.py) の `_get_vllm_embeddings` |
| 共有グラフ共存ハードニング（保護ラベル除外） | [src/shared/constants.py](src/shared/constants.py) の `PROTECTED_MANAGEMENT_LABELS` ほか |
| EDC `/convert` によるスキャンPDF前処理 | [src/document_sources/local_file.py](src/document_sources/local_file.py) |
| 見出しパンくずMarkdownチャンキング | [src/create_chunks.py](src/create_chunks.py) |

## ローカル起動（compose を使わない場合）

```bash
cd backend
python3.12 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 開発（自動リロード）
uvicorn score:app --reload
# 本番相当（compose の CMD と同じ）
# gunicorn score:app --workers 8 --threads 8 \
#   --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 300
```

API仕様は起動後 `http://localhost:8000/docs`（Swagger UI）で確認できる。

## docker-compose 起動（推奨）

ルートの docker-compose 経由で backend / frontend / neo4j を一括起動する（ルート [../README.md](../README.md) 参照）。
backend はコンテナ内 :8000、ホスト側は **:8440** に公開される。

## 環境変数（`.env`）

`.env` は **gitignore 済み**。`example.env` を雛形にコンテナ実行時の値を用意する。オンプレ構成の主要キー:

| 変数 | 用途 |
|---|---|
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` | Neo4j接続 |
| `LLM_MODEL_CONFIG_GEMMA4` | ローカルLLM `<model>,<base_url>,<api_key>` |
| `LLM_MODEL_CONFIG_EDC` | EDC API `<base_url>[,<doctype>[,<chunk_method>[,<concurrency>]]]` |
| `EDC_LLM_MODEL` | EDC が使う LLM の名前（EDC の .env の VLLM_MODEL と同じ値、backend/.env に書く）。EDC で抽出した文書の Document.model に残す |
| `EMBEDDING_PROVIDER=vllm` / `EMBEDDING_MODEL` / `VLLM_EMBEDDING_ENDPOINT` | 埋め込み（ruri-v3-310m） |
| `ENTITY_EMBEDDING=true` | 名寄せ（重複エンティティ検出）用のエンティティ埋め込み |
| `EDC_PDF_CONVERT` / `MARKDOWN_CHUNKING` / `GRAPH_SCHEMA_CONSOLIDATION_ENABLED` | 前処理・共有グラフ運用 |

各キーの詳細と全一覧は `example.env` を参照。

## 社外へ通信しない設定

実行時に社外へ通信しないよう、`.env` に次の 3 行を設定する（`example.env` の末尾と同じ）。いずれも backend の再起動後に反映される。

| 変数 | 停止する通信 |
|---|---|
| `RAGAS_DO_NOT_TRACK=true` | ragas（評価 API）の利用統計の送信（t.explodinggradients.com） |
| `ORT_DISABLE_TELEMETRY=1` | onnxruntime（PDF の Markdown 変換で読み込む）の利用統計の送信（mobile.events.data.microsoft.com） |
| `TIKTOKEN_CACHE_DIR=/code/tokenizers/tiktoken-cache` | tiktoken による gpt2 の符号化ファイルの実行時の取得（openaipublic.blob.core.windows.net） |

tiktoken の格納場所 `backend/tokenizers/tiktoken-cache/`（git の追跡外）には 2 ファイルを配置する。ファイル名は取得元の URL の sha1 である:
`6d1cbeee0f20b3d9449abfede4726ed8212e3aee`（`.../gpt-2/encodings/main/vocab.bpe`）と `6c7ea1a7e38e3a7f062df639a5b80947f075ffe6`（`.../gpt-2/encodings/main/encoder.json`）。再作成する場合は、社外に接続できる環境で `TIKTOKEN_CACHE_DIR` をこの格納場所に設定し、`python -c "import tiktoken; tiktoken.get_encoding('gpt2')"` を一度実行する（導入時のみの通信）。
なお unstructured は、UTF-8 の .txt や docx を要素に分割する際に spaCy のモデル（en_core_web_sm）を github.com から取得する。この通信を停止する設定はないため、使用する場合はイメージの作成段階で導入する（現在のイメージには含まれていない）。
