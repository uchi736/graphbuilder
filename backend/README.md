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
| EDC抽出エンジン統合（モデル名 `edc`） | [src/edc_extraction.py](src/edc_extraction.py)（チャンク毎に EDC API `/extract` を呼びトリプルを GraphDocument へ変換） |
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
| `EMBEDDING_PROVIDER=vllm` / `EMBEDDING_MODEL` / `VLLM_EMBEDDING_ENDPOINT` | 埋め込み（ruri-v3-310m） |
| `ENTITY_EMBEDDING=true` | 名寄せ（重複エンティティ検出）用のエンティティ埋め込み |
| `EDC_PDF_CONVERT` / `MARKDOWN_CHUNKING` / `GRAPH_SCHEMA_CONSOLIDATION_ENABLED` | 前処理・共有グラフ運用 |

各キーの詳細と全一覧は `example.env` を参照。
