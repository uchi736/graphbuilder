# CPU だけの簡易検証用 LLM・埋め込み（tools/cpu-fallback）

| 作業 | いつ | 守るもの | 手順 |
|---|---|---|---|
| 立てる | 学習などで常駐の gemma4・ruri が止まっていて、Graph Builder の取り込みの動作確認だけしたいとき | GPU は使わない（学習中の同居禁止）。メモリ上限を付ける（llama-cpu 8GB、embed-cpu 4GB） | 1 |
| backend を向ける | 立てた後 | 本番の設定行はコメントで残し、戻せるようにする | 2 |
| 戻す | 常駐が戻ったら（gpu-admit status で gemma4・vllm-embed-ruri が稼働） | 向け先を本番に戻してから CPU 版を止める | 3 |

品質は常駐の gemma4（26B）より低く、速さも段違いに遅い。抽出の中身の評価には使わず、取り込みの流れ・チャンク・参照リンク・画面の確認に限る。

## 1. 立てる

```bash
cd ~/ai-stack/llm-graph-builder
# LLM: gemma-4-E4B-it（Q4_K_M、約 5GB、unsloth 配布の GGUF。初回は自動取得）
docker run -d --name llama-cpu --restart no --memory=14g --memory-swap=14g --cpus=8 \
  --network llm-graph-builder_net -p 127.0.0.1:8030:8080 -v llama-cpu-cache:/root/.cache/huggingface \
  ghcr.io/ggml-org/llama.cpp:server -hf unsloth/gemma-4-E4B-it-GGUF:Q4_K_M --alias gemma4cpu \
  -c 8192 -np 2 -t 8 -ngl 0 --jinja --chat-template-kwargs '{"enable_thinking": false}' --host 0.0.0.0 --port 8080
# 注意: -hf の取得先は /root/.cache/huggingface（/root/.cache/llama.cpp ではない）。ここをボリュームにしないと、コンテナを消すと重みも消える。
#       gemma-4 は既定で思考モードになり本文が空になる。--reasoning-budget 0 では思考文が本文に混ざるだけなので、
#       テンプレートの変数 enable_thinking=false をサーバ側で固定する（backend は要求に変数を付けないため）。メモリ 14GB（10GB では長い入力が続くと OOM で落ちた）
# 埋め込み: ruri-v3-310m を sentence-transformers で（重みは ~/ai-stack/shared/models/hub の手元の写し）
docker run -d --name embed-cpu --restart no --memory=4g --memory-swap=4g --cpus=4 \
  --network llm-graph-builder_net -p 127.0.0.1:8031:8000 -e HF_HOME=/hf -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -v ~/ai-stack/shared/models/hub:/hf/hub:ro -v $PWD/tools/cpu-fallback/embed_server.py:/embed_server.py:ro \
  --entrypoint python embedding-trainer:latest /embed_server.py --model cl-nagoya/ruri-v3-310m --port 8000
# リランカー: ruri-v3-reranker-310m を sentence-transformers の CrossEncoder で（/v1/rerank と同じ形）
docker run -d --name rerank-cpu --restart no --memory=4g --memory-swap=4g --cpus=4 \
  --network llm-graph-builder_net -p 127.0.0.1:8032:8000 -e HF_HOME=/hf -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -v ~/ai-stack/shared/models/hub:/hf/hub:ro -v $PWD/tools/cpu-fallback/rerank_server.py:/rerank_server.py:ro \
  --entrypoint python embedding-trainer:latest /rerank_server.py --model cl-nagoya/ruri-v3-reranker-310m --port 8000
curl -s http://127.0.0.1:8030/health; curl -s http://127.0.0.1:8031/health; curl -s http://127.0.0.1:8032/health
```

## 2. backend を向ける

- ルート `.env`: `LLM_MODEL_CONFIG_GEMMA4CPU=gemma4cpu,http://llama-cpu:8080/v1,dummy`（常設。モデル名 `gemma4cpu` で使える）
- ルート `.env`: `VLLM_EMBEDDING_ENDPOINT` を `http://embed-cpu:8000/v1,dummy` に（本番の行はコメントで残す）
- `backend/.env`: `CHUNK_REFERENCE_MODEL=gemma4cpu`（参照リンクの後処理）、`CHAT_RERANK_URL=http://rerank-cpu:8000/v1/rerank`（本番の行はコメントで残す）
- `docker compose up -d --no-deps --no-build backend`
- 取り込みは `python3 tools/ingest/ingest.py <フォルダ> --model gemma4cpu`

## 3. 戻す

- ルート `.env` の `VLLM_EMBEDDING_ENDPOINT` を本番の行に戻す。`backend/.env` の `CHUNK_REFERENCE_MODEL` を消し、`CHAT_RERANK_URL` を本番の行に戻す
- `docker compose up -d --no-deps --no-build backend`
- `docker rm -f llama-cpu embed-cpu rerank-cpu`（重みのキャッシュ `llama-cpu-cache` は残してよい）

## 症状と対処

| 症状 | 原因 | 対処 |
|---|---|---|
| llama-cpu の /health が返らない | 重みの取得中（初回 約 6GB、回線が遅いと 40 分） | `docker exec llama-cpu du -sh /root/.cache/huggingface` で進み具合を見る |
| 応答の content が空で reasoning_content だけある、または本文に「thinking process」が混ざる | 思考モード | `--chat-template-kwargs '{"enable_thinking": false}'` を付けて起動し直す |
| 抽出が極端に遅い | CPU 推論（数 tok/s） | 文書を 1〜2 本に絞る。E2B（`ggml-org/gemma-4-E2B-it-GGUF:Q4_0`）に替える |
| 埋め込みの次元が合わない | モデル違い | 本番と同じ ruri-v3-310m（768）を使う |
