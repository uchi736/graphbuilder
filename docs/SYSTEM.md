# システム構成（3リポジトリ + 外部サービス）

このリポジトリ単体では完結しない。全体は3つのリポジトリと稼働サービス群で構成される。

## リポジトリ

| リポジトリ | リモート | 役割 |
|---|---|---|
| llm-graph-builder（本リポジトリ） | ※要フォーク（下記注意） | 取り込み・保存基盤・運用UI・チャット |
| EDC | https://github.com/uchi736/EDC | 統制スキーマでのトリプル抽出（HTTPサービス）+ PDF OCR変換 |
| graphrag | https://github.com/uchi736/graphrag | Graph-First検索（共有グラフ運用のバッチ / ステージ2でbackendに内蔵予定） |

> **注意**: 本リポジトリの origin は現在 neo4j-labs（本家）を向いている。
> カスタマイズ（日本語化・EDC統合・共有グラフ対応）を保全するには自分のリポジトリを作成して付け替えること:
> `git remote rename origin upstream && git remote add origin <自分のリポジトリURL>`

## 稼働サービス（192.168.0.250）

| サービス | ポート | 提供元 |
|---|---|---|
| GB frontend / backend / Neo4j | 8441 / 8440 / 7475・7688 | 本リポジトリの docker-compose |
| edc-api | 8080 | EDCリポジトリの docker-compose |
| PostgreSQL (PGVector) | 5432 | graphrag側で稼働（共有グラフ運用で使用） |
| vLLM chat (gemma4) | 8000 | ホスト側プロセス |
| vLLM embedding (ruri-v3-310m) | 8003 | ホスト側プロセス（GB / EDC / graphrag 共用） |
| vLLM reranker (bge-reranker-v2-m3) | 8006 | ホスト側プロセス（graphrag用） |
| PaddleX OCR | 8005 | ホスト側プロセス（EDC /convert 用） |

## リポジトリ間の接点（ファイル依存はHTTP・設定のみ）

- 本リポジトリ → EDC: HTTP のみ（`LLM_MODEL_CONFIG_EDC` のURL、`/extract` と `/convert`）。ファイルコピー不要
- graphrag → 本リポジトリのNeo4j: `scripts/enrich_external_graph.py` / `scripts/mirror_gb_chunks.py`（graphrag側にコミット済み）
- 検証ゲート手順: `graphrag/docs/gb_shared_graph_eval.md`

## ステージ2（一本化）での依存方針

graphrag_core を backend に取り込む際は**生コピー（vendoring）せず**、graphrag の `pyproject.toml` を使って
コミット固定の pip 参照にする（再現性とコード一元管理の両立）:

```
# backend/requirements.txt に追加（<sha> は検証済みコミット）
graphrag-core @ git+https://github.com/uchi736/graphrag.git@<sha>
```

ローカル開発では `pip install -e C:\work\RAG\graphrag` でも可。
