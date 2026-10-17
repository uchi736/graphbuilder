#!/usr/bin/env python3
"""HS 基準 56 本（文字層のある分）を、指定の抽出方式で評価用の Neo4j に取り込み、グラフを書き出す。

使い方（tools/kg/hs で実行。ホストの python3 で動く。標準ライブラリと curl だけ）:
    python3 run_hs_method.py <方式> <コンテナ> <別名> <出力名> [--only 名前,名前]
      方式    std    = 標準の抽出（スキーマの指定なし、model=gemma4）
              schema = 人手スキーマ（文書の種類ごとに型を allowedNodes に、種類の名前を追加の指示に書く。model=gemma4）
              edc    = EDC（文書の種類は EDC が自動で判定する。backend の環境変数の設定がそのまま効く）
      コンテナ neo4j-kgb4 / neo4j-kgb5 / neo4j-kgb6 のどれか（評価用の元のグラフと他の検証用は対象にしない）
      別名     database4 など（backend から見た Neo4j のホスト名）
      出力名   runs/<出力名>/ に、取り込みの記録（run.json）と書き出し（graph_dump.json）を置く

手順: コンテナを起こす → グラフを空にする → 1 本ずつ upload と extract → 後処理（エンティティの埋め込みと索引）
      → 書き出し → コンテナを止める（グラフはボリュームに残る）。
抽出が失敗したときは gemma4 の応答を確かめ、応答が無ければそこで止める。常駐の gemma4 は再起動しない。
"""
import json, os, subprocess, sys, time, datetime, argparse, re

HERE = os.path.dirname(os.path.abspath(__file__))
K = os.path.dirname(HERE)
B = "http://localhost:8440"
PASSWORD = "kgb-please-change"   # 評価用 Neo4j の開発用パスワード（リポジトリ内スクリプトに既出）
ALLOWED = {"neo4j-kgb4", "neo4j-kgb5", "neo4j-kgb6"}
EMB = ["--form-string", "embedding_provider=vllm", "--form-string", "embedding_model=cl-nagoya/ruri-v3-310m"]


def sh(cmd, timeout=None, check=False):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"{cmd[:3]} failed: {p.stderr[-400:]}")
    return p


def log(msg):
    print(f"== {msg} {datetime.datetime.now():%F %T}", flush=True)


def cypher(container, query):
    return sh(["docker", "exec", container, "cypher-shell", "-u", "neo4j", "-p", PASSWORD, query], timeout=600)


def wait_neo4j(container):
    for _ in range(60):
        if cypher(container, "RETURN 1").returncode == 0:
            return True
        time.sleep(3)
    return False


def gemma_alive():
    body = json.dumps({"model": "google/gemma-4-26B-A4B-it", "messages": [{"role": "user", "content": "1+1="}], "max_tokens": 4})
    p = sh(["curl", "-s", "--max-time", "90", "-H", "Content-Type: application/json", "-d", body, "http://localhost:8000/v1/chat/completions"])
    return '"choices"' in p.stdout


def schema_settings():
    """人手スキーマの定義（文書の種類ごと）。hs/schemas.json を読む。"""
    return json.load(open(os.path.join(HERE, "schemas.json"), encoding="utf-8"))


def extract_args(method, doc, schemas):
    model = {"std": "gemma4", "schema": "gemma4", "edc": "edc"}[method]
    nodes = rels = instr = ""
    if method == "schema":
        s = schemas.get(doc["schema"])
        if s:
            nodes = ",".join(s["types"])
            instr = s["instructions"] + " (5) Use ONLY these relationship types and no others: " + ", ".join(s["relations"]) + "."
    return model, nodes, rels, instr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("method", choices=["std", "schema", "edc"])
    ap.add_argument("container"); ap.add_argument("alias"); ap.add_argument("out")
    ap.add_argument("--only", default="")
    ap.add_argument("--keep-running", action="store_true")
    a = ap.parse_args()
    if a.container not in ALLOWED:
        sys.exit(f"このグラフは対象にできない: {a.container}")
    out = os.path.join(HERE, "runs", a.out); os.makedirs(os.path.join(out, "pdf"), exist_ok=True)
    docs = json.load(open(os.path.join(HERE, "docs.json"), encoding="utf-8"))
    if a.only:
        keep = set(a.only.split(","))
        docs = [d for d in docs if d["name"] in keep]
    schemas = schema_settings() if a.method == "schema" else {}
    cred = ["--form-string", f"uri=neo4j://{a.alias}:7687", "--form-string", "userName=neo4j", "--form-string", f"password={PASSWORD}",
            "--form-string", "database=neo4j", "--form-string", "email=test@example.com"]
    record = {"method": a.method, "container": a.container, "started": f"{datetime.datetime.now():%F %T}", "docs": []}

    log(f"0) {a.container} を起こして空にする（方式 {a.method}、{len(docs)} 本）")
    sh(["docker", "start", a.container], check=True)
    if not wait_neo4j(a.container):
        sys.exit("Neo4j が起きない")
    print(cypher(a.container, "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 500 ROWS").stdout[-200:], flush=True)

    log("1) 取り込み")
    aborted = None
    for i, doc in enumerate(docs, 1):
        dst = os.path.join(out, "pdf", doc["name"])
        if not os.path.exists(dst):
            subprocess.run(["cp", doc["src"], dst], check=True)
        model, nodes, rels, instr = extract_args(a.method, doc, schemas)
        since = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        sh(["curl", "-s", "--max-time", "120", "-X", "POST", f"{B}/upload", "-F", f"file=@{dst}", "--form-string", "chunkNumber=1",
            "--form-string", "totalChunks=1", "--form-string", f"originalname={doc['name']}", "--form-string", f"model={model}"] + cred)
        t0 = time.time()
        p = sh(["curl", "-s", "--max-time", "3000", "-X", "POST", f"{B}/extract"] + cred + [
            "--form-string", f"model={model}", "--form-string", "source_type=local file", "--form-string", f"file_name={doc['name']}",
            "--form-string", "source_url=", "--form-string", f"allowedNodes={nodes}", "--form-string", f"allowedRelationship={rels}",
            "--form-string", "token_chunk_size=512", "--form-string", "chunk_overlap=50", "--form-string", "chunks_to_combine=1",
            "--form-string", "language=", "--form-string", "retry_condition=", "--form-string", f"additional_instructions={instr}"] + EMB)
        sec = round(time.time() - t0)
        try:
            r = json.loads(p.stdout); x = r.get("data") or {}
            row = {"name": doc["name"], "category": doc["category"], "status": r.get("status"), "chunks": x.get("total_chunks"),
                   "nodes": x.get("nodeCount"), "rels": x.get("relationshipCount"), "state": x.get("status"), "sec": sec,
                   "message": (r.get("message") or "")[:200]}
        except Exception as e:
            row = {"name": doc["name"], "category": doc["category"], "status": "unreadable", "sec": sec, "message": (p.stdout or p.stderr)[:200]}
        if a.method == "edc":
            lg = sh(["docker", "logs", "backend", "--since", since], timeout=60)
            m = re.findall(r"EDC classified document as doctype '([^']*)'", lg.stdout + lg.stderr)
            row["doctype"] = m[-1] if m else None
        record["docs"].append(row)
        print(f"[{i}/{len(docs)}] {doc['name']} {row.get('status')} | chunks {row.get('chunks')} | nodes {row.get('nodes')} | rels {row.get('rels')}"
              f" | {row.get('state')} | {row.get('doctype', '')} ({sec}s) {row.get('message', '')[:80]}", flush=True)
        json.dump(record, open(os.path.join(out, "run.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        if row.get("status") != "Success":
            if not gemma_alive():
                aborted = f"{doc['name']} の抽出が失敗し、gemma4 が 90 秒以内に応答しない。ここで止める（gemma4 は再起動しない）"
                log("中止: " + aborted)
                break

    if not aborted:
        log("2) エンティティの埋め込みと索引")
        p = sh(["curl", "-s", "--max-time", "1800", "-X", "POST", f"{B}/post_processing"] + cred + EMB +
               ["--form-string", 'tasks=["materialize_entity_similarities","enable_hybrid_search_and_fulltext_search_in_bloom"]'])
        print(p.stdout[:160], flush=True)

    log("3) 書き出し")
    dump = os.path.join(out, "graph_dump.json")
    with open(dump, "w", encoding="utf-8") as f:
        subprocess.run(["docker", "exec", "-e", f"NEO4J_URI=neo4j://{a.alias}:7687", "-i", "backend", "python", "-"],
                       stdin=open(os.path.join(K, "routes", "dump_graph.py"), encoding="utf-8"), stdout=f, stderr=subprocess.DEVNULL)
    try:
        d = json.load(open(dump, encoding="utf-8")); print({k: len(v) for k, v in d.items()}, flush=True)
        record["dump"] = {k: len(v) for k, v in d.items()}
    except Exception as e:
        print("書き出しを読めない:", e, flush=True)
    record["finished"] = f"{datetime.datetime.now():%F %T}"; record["aborted"] = aborted
    json.dump(record, open(os.path.join(out, "run.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if not a.keep_running:
        sh(["docker", "stop", a.container])
        log(f"{a.container} を止めた（グラフはボリュームに残る）")
    log("完了" if not aborted else "中止して終了")
    sys.exit(1 if aborted else 0)


if __name__ == "__main__":
    main()
