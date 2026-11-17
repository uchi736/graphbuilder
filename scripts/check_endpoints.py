"""backend のコンテナの中から、設定された接続先に届くかを確かめる（scripts/check.sh が標準入力で渡して実行する）。

必須の項目が 1 つでも届かなければ終了コード 1。任意の項目は「注意」とだけ出す。標準ライブラリだけで動く。
"""
import json
import os
import sys
import urllib.request

results = []  # (必須か, 名前, 良否, 説明)


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


def http(url, body=None, key=None, timeout=20):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=timeout) as r:
        return json.loads(r.read().decode() or "null")


def check(required, name, fn):
    try:
        results.append((required, name, True, fn() or ""))
    except Exception as exc:  # 届かない理由をそのまま見せる
        results.append((required, name, False, f"{type(exc).__name__}: {str(exc)[:150]}"))


def neo4j():
    from neo4j import GraphDatabase
    with GraphDatabase.driver(env("NEO4J_URI"), auth=(env("NEO4J_USERNAME"), env("NEO4J_PASSWORD"))) as d:
        d.verify_connectivity()
    return env("NEO4J_URI")


def llm():
    model, base, key = (env("LLM_MODEL_CONFIG_GEMMA4").split(",") + ["", ""])[:3]
    served = [m["id"] for m in http(base.rstrip("/") + "/models", key=key)["data"]]
    if model not in served:
        raise RuntimeError(f"{base} は {served} を配信しており、LLM_MODEL={model} が無い")
    return f"{base}  {model}"


def embedding():
    base, key = (env("VLLM_EMBEDDING_ENDPOINT").split(",") + [""])[:2]
    out = http(base.rstrip("/") + "/embeddings", {"model": env("EMBEDDING_MODEL"), "input": ["接続の確認"]}, key=key)
    dim = len(out["data"][0]["embedding"])
    return f"{base}  {env('EMBEDDING_MODEL')}  {dim} 次元"


def rerank():
    if env("CHAT_RERANK_ENABLED", "false").lower() != "true":
        return "CHAT_RERANK_ENABLED=false（使わない）"
    out = http(env("CHAT_RERANK_URL"), {"model": env("CHAT_RERANK_MODEL"), "query": "接続の確認",
                                         "documents": ["接続を確認する文", "関係のない文"]})
    if len(out.get("results", [])) != 2:
        raise RuntimeError(f"応答の形が違う: {str(out)[:100]}")
    return f"{env('CHAT_RERANK_URL')}  {env('CHAT_RERANK_MODEL')}"


def parse():
    base = env("PDF_REGIONS_PARSE_URL")
    served = [m["id"] for m in http(base.rstrip("/") + "/models")["data"]]
    if env("PDF_REGIONS_PARSE_MODEL") not in served:
        raise RuntimeError(f"{base} は {served} を配信しており、PARSE_MODEL={env('PDF_REGIONS_PARSE_MODEL')} が無い")
    return f"{base}  {env('PDF_REGIONS_PARSE_MODEL')}"


def edc_base():
    return env("LLM_MODEL_CONFIG_EDC").split(",")[0].rstrip("/")


def edc():
    http(edc_base() + "/health")  # EDC から生成と埋め込みに届かなければ 503 で例外になる
    n = len(http(edc_base() + "/doctypes"))
    if n == 0:
        raise RuntimeError("文書タイプが 0 件（edc-api/schemas が空）")
    return f"{edc_base()}  文書タイプ {n} 件、EDC から生成と埋め込みに届く"


def edc_schemas():
    d = env("EDC_SCHEMAS_DIR")
    if not os.path.isfile(os.path.join(d, "registry.json")):
        raise RuntimeError(f"{d}/registry.json が無い")
    return f"{d}  {len([f for f in os.listdir(d) if f.endswith('.csv')])} ファイル"


def chunk_tokenizer():
    p = env("CHUNK_TOKENIZER", "gpt2")
    if "/" in p and not os.path.isfile(p):
        raise RuntimeError(f"{p} が無い（scripts/fetch_tokenizers.sh を実行する。無いままだと gpt2 で数える）")
    return p


def tiktoken_cache():
    d = env("TIKTOKEN_CACHE_DIR")
    need = ["6d1cbeee0f20b3d9449abfede4726ed8212e3aee", "6c7ea1a7e38e3a7f062df639a5b80947f075ffe6"]
    missing = [f for f in need if not os.path.isfile(os.path.join(d, f))]
    if missing:
        raise RuntimeError(f"{d} に {len(missing)} ファイル足りない（scripts/fetch_tokenizers.sh を実行する。無いままだと実行時に社外へ取りに行く）")
    return d


check(True, "Neo4j", neo4j)
check(True, "生成", llm)
check(True, "埋め込み", embedding)
check(True, "EDC", edc)
check(True, "EDC のスキーマ（backend 側）", edc_schemas)
check(True, "リランク", rerank)
check(True, "チャンクのトークナイザー", chunk_tokenizer)
check(True, "tiktoken のキャッシュ", tiktoken_cache)
check(False, "PDF の構造の読み取り（任意）", parse)

bad = 0
for required, name, ok, note in results:
    mark = "OK  " if ok else ("NG  " if required else "注意")
    bad += (not ok) and required
    print(f"[{mark}] {name}: {note}")
print("\n" + ("すべて届く" if not bad else f"{bad} 件が届かない。.env の接続先を直して docker compose ... up -d をやり直す"))
sys.exit(1 if bad else 0)
