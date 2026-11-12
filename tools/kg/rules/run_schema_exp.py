#!/usr/bin/env python3
"""スキーマの層の比較（A〜E の 5 条件 × 社内規定 11 本・技術基準 10 本）を、専用のコンテナで取り込み、書き出し、採点する。

条件（README.md の「測る条件」と同じ）
    A  標準抽出（本家の LLMGraphTransformer、model=gemma4）
    B  EDC・既存の定義（技術基準: 試運転_検査基準／購入品_品質要求基準、社内規定: 社内規程_就業規則）
    C  EDC・骨格 6 種類だけ（実験C_骨格のみ）
    D  EDC・厚い共通層（実験D_共通）
    E  EDC・共通層＋カテゴリ（実験E_技術基準／実験E_社内規定）
    F  標準抽出＋人手スキーマ（model=gemma4。B と同じ型と関係名を 3 つ組で allowedNodes/allowedRelationship に渡す。schema_F.json。2026-11-04 追加）
    H  B＋切り方の規則 C1〜C5 と架空の手本 1 例（文書の種類 *_v2、~/ai-stack/EDC/schemas_exp/registry.json。2026-11-04 追加、X14）
    R  B の取り直し（設定は B と同じ。1 回ごとの揺れを見るため。2026-11-04 追加、X14）
    G  F＋サンプル 30 チャンクから Graph Builder の「文章からスキーマ」機能（gemma4）が出した 3 つ組を足したもの（schema_G.json、make_schema_G.py。2026-11-04 追加）
    EDC の条件はすべて語彙を開き（enrich）、語彙の外は RELATED_TO に畳み、文の形の名前を落とす（稼働中と同じ）。
    文書の種類は自動判定させず、条件ごとに名前で指定する（判定の誤りを比較から外すため）。

使い方（tools/kg/rules で実行。ホストの python3 と docker と curl だけ）:
    python3 run_schema_exp.py --set rules --cond A,B,C,D,E      # 社内規定 11 本
    python3 run_schema_exp.py --set tech  --cond A,B,C,D,E      # 技術基準 10 本（試運転 6・品質要求基準 4）
    python3 run_schema_exp.py --score                           # 書き出し済みの分を採点して runs/summary.md を作る
    python3 run_schema_exp.py --teardown                        # 実験用の 3 コンテナを止めて消す（Neo4j のボリュームは残す）

使うコンテナ（すべて GPU を使わない。gemma4・ruri・リランカーは常駐のものを HTTP で呼ぶ）
    edc-api-exp        EDC。台帳は ~/ai-stack/EDC/schemas_exp（稼働中の edc-api の schemas とは別）。127.0.0.1:8082
    neo4j-schemaexp    実験用の Neo4j。別名 database-schemaexp。評価用の kgb4〜7 と開発用の gbdev には触らない
    backend-schemaexp  backend。コードは作業ツリー llm-graph-builder-dev/backend（「この規定」の置き換えを含む）。127.0.0.1:8452
                       文書の種類ごとに環境変数を変えて作り直す（数秒）

守ること
    - GPU の予約が他者の名前で「予約中」なら始めない（--force で無視できるが使わない）。gemma4 を数時間占有するため。
    - 稼働中の backend・edc-api・neo4j-kgb* には書き込まない。
    - 一時コンテナはメモリ上限つき（4g）。
所要の目安: 1 条件あたり 技術基準 約 30 分、社内規定 約 60〜90 分（就業規則が 200KB と長い）。5 条件 × 2 組で 8〜10 時間。
"""
import argparse, datetime, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
K = os.path.dirname(HERE)
DEV_BACKEND = "/home/uchida/ai-stack/llm-graph-builder-dev/backend"
LOCAL_MODEL = "/home/uchida/ai-stack/llm-graph-builder/backend/local_model"
SCHEMAS_EXP = "/home/uchida/ai-stack/EDC/schemas_exp"
ENV_BASE = os.path.join(HERE, "runs", "backend-base.env")  # 11/1 の A〜E と同じ設定の控え（元の一時フォルダの設定は消えた）
NET = "llm-graph-builder_net"
NEO, NEO_ALIAS, NEO_PW = "neo4j-schemaexp", "database-schemaexp", "schemaexp-test-only"
BE, BE_PORT = "backend-schemaexp", 8452
EDC, EDC_PORT = "edc-api-exp", 8082
B = f"http://localhost:{BE_PORT}"
EMB = ["--form-string", "embedding_provider=vllm", "--form-string", "embedding_model=cl-nagoya/ruri-v3-310m"]
CRED = ["--form-string", f"uri=neo4j://{NEO_ALIAS}:7687", "--form-string", "userName=neo4j", "--form-string", f"password={NEO_PW}",
        "--form-string", "database=neo4j", "--form-string", "email=test@example.com"]

TECH_DOCS = [("HS-VTR-T001E.pdf", "/home/uchida/docs/試運転要領/HS-VTR-T001E.pdf", "testspec"),
             ("HS-VTR-T002F.pdf", "/home/uchida/docs/試運転要領/HS-VTR-T002F.pdf", "testspec"),
             ("VTR-T003D.pdf", "/home/uchida/docs/試運転要領/VTR-T003 D.pdf", "testspec"),
             ("VTR-T004C.pdf", "/home/uchida/docs/試運転要領/VTR-T004C.pdf", "testspec"),
             ("VTR-T005B.pdf", "/home/uchida/docs/試運転要領/VTR-T005B.pdf", "testspec"),
             ("VTR-T_発行目録.pdf", "/home/uchida/docs/試運転要領/試運転検査基準(HS-VTR-T)発行目録xdw.pdf", "testspec"),
             ("HS-RU-002F.pdf", "/home/uchida/docs/RHAT/HS-RU-002F.pdf", "quality"),
             ("HS-RU-015.pdf", "/home/uchida/docs/RHAT/HS-RU-015.pdf", "quality"),
             ("HS-TP-007A.pdf", "/home/uchida/docs/TPS/HS-TP-007A本文.pdf", "quality"),
             ("HS-TP-008C.pdf", "/home/uchida/docs/TPS/HS-TP-008C本文.pdf", "quality")]
RULE_DOCS = [(f, os.path.join(HERE, "docs", f), "rule") for f in sorted(os.listdir(os.path.join(HERE, "docs"))) if f.endswith(".md")]

DOCTYPE = {  # (条件, 文書の群) → EDC の文書の種類。A は EDC を使わない
    ("B", "testspec"): "試運転_検査基準", ("B", "quality"): "購入品_品質要求基準", ("B", "rule"): "社内規程_就業規則",
    ("C", "testspec"): "実験C_骨格のみ", ("C", "quality"): "実験C_骨格のみ", ("C", "rule"): "実験C_骨格のみ",
    ("D", "testspec"): "実験D_共通", ("D", "quality"): "実験D_共通", ("D", "rule"): "実験D_共通",
    ("H", "testspec"): "試運転_検査基準_v2", ("H", "quality"): "購入品_品質要求基準_v2",
    ("R", "testspec"): "試運転_検査基準", ("R", "quality"): "購入品_品質要求基準",
    ("E", "testspec"): "実験E_技術基準", ("E", "quality"): "実験E_技術基準", ("E", "rule"): "実験E_社内規定",
}


def sh(cmd, timeout=None, check=False, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} failed: {p.stderr[-500:]}")
    return p


def log(msg):
    print(f"== {msg} {datetime.datetime.now():%F %T}", flush=True)


def preflight(force):
    st = sh(["gpu-admit", "status"]).stdout
    if "● 予約中" in st and not force:
        sys.exit("GPU の予約が他者の名前で予約中。gemma4 を数時間占有するので始めない:\n" + "\n".join(l for l in st.splitlines() if "予約中" in l))
    body = json.dumps({"model": "google/gemma-4-26B-A4B-it", "messages": [{"role": "user", "content": "1+1="}], "max_tokens": 4})
    p = sh(["curl", "-s", "--max-time", "90", "-H", "Content-Type: application/json", "-d", body, "http://localhost:8000/v1/chat/completions"])
    if '"choices"' not in p.stdout:
        sys.exit("gemma4 が応答しない。常駐の gemma4 は再起動しない")


def running(name):
    return sh(["docker", "inspect", "-f", "{{.State.Running}}", name]).stdout.strip() == "true"


def ensure_edc():
    if running(EDC):
        return
    sh(["docker", "rm", "-f", EDC])
    env = json.loads(sh(["docker", "inspect", "-f", "{{json .Config.Env}}", "edc-api"], check=True).stdout)
    env = [e for e in env if not e.startswith(("PATH=", "LANG=", "GPG", "PYTHON"))]
    cmd = ["docker", "run", "-d", "--name", EDC, "--network", NET, "--network-alias", EDC, "--memory=4g", "--cpus=4", "--add-host", "host.docker.internal:host-gateway",
           "-p", f"127.0.0.1:{EDC_PORT}:8080", "-v", f"{SCHEMAS_EXP}:/app/schemas:ro"]
    for e in env:
        cmd += ["-e", e]
    sh(cmd + ["edc-api:latest", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"], check=True)
    for _ in range(60):
        if '"name"' in sh(["curl", "-s", f"http://localhost:{EDC_PORT}/doctypes"]).stdout:
            return
        time.sleep(2)
    sys.exit("edc-api-exp が起きない")


def ensure_neo4j():
    if not running(NEO):
        if sh(["docker", "start", NEO]).returncode != 0:
            sh(["docker", "run", "-d", "--name", NEO, "--network", NET, "--network-alias", NEO_ALIAS, "--memory=4g",
                "-e", f"NEO4J_AUTH=neo4j/{NEO_PW}", "-e", 'NEO4J_PLUGINS=["apoc"]', "-e", "NEO4J_dbms_security_procedures_unrestricted=apoc.*",
                "-e", "NEO4J_server_memory_pagecache_size=512M", "-e", "NEO4J_server_memory_heap_max__size=1G",
                "-v", "neo4j-schemaexp-data:/data", "neo4j:5.26-community"], check=True)
    for _ in range(60):
        if cypher("RETURN 1").returncode == 0:
            return
        time.sleep(3)
    sys.exit("neo4j-schemaexp が起きない")


def cypher(q):
    return sh(["docker", "exec", NEO, "cypher-shell", "-u", "neo4j", "-p", NEO_PW, q], timeout=900)


def start_backend(doctype):
    """文書の種類（EDC に渡す名前）を環境変数に入れて backend-schemaexp を作り直す。"""
    sh(["docker", "rm", "-f", BE])
    env = []
    for line in open(ENV_BASE, encoding="utf-8").read().splitlines():
        if not line.strip() or line.startswith("#") or "=" not in line:
            continue
        k = line.split("=", 1)[0]
        if k in ("LLM_MODEL_CONFIG_EDC", "EDC_SCHEMAS_DIR", "NEO4J_URI", "NEO4J_PASSWORD"):
            continue
        env.append(line)
    env += [f"LLM_MODEL_CONFIG_EDC=http://{EDC}:8080,{doctype or 'auto'},recursive,2",  # 同じネットワークの名前で呼ぶ（127.0.0.1 公開はコンテナから届かない） "EDC_SCHEMAS_DIR=/edc_schemas",
            f"NEO4J_URI=neo4j://{NEO_ALIAS}:7687", f"NEO4J_PASSWORD={NEO_PW}", "EDC_ENRICH_SCHEMA=true", "EDC_FOLD_UNKNOWN_RELATIONS=true",
            "EDC_DROP_SENTENCE_NAMES=true", "EDC_RESOLVE_SELF_REFERENCE=true"]
    envfile = os.path.join(HERE, "runs", ".backend-schemaexp.env")
    os.makedirs(os.path.dirname(envfile), exist_ok=True)
    open(envfile, "w", encoding="utf-8").write("\n".join(env) + "\n")
    sh(["docker", "run", "-d", "--name", BE, "--network", NET, "--memory=4g", "--add-host", "host.docker.internal:host-gateway",
        "-p", f"127.0.0.1:{BE_PORT}:8000", "--env-file", envfile, "-v", f"{DEV_BACKEND}:/code", "-v", f"{SCHEMAS_EXP}:/edc_schemas:ro",
        "-v", f"{LOCAL_MODEL}:/code/local_model", "-v", f"{os.path.join(HERE, 'runs', 'tiktoken-cache')}:/tmp/data-gym-cache:ro",
        "llm-graph-builder-backend:latest",
        "gunicorn", "score:app", "--workers", "2", "--threads", "4", "--worker-class", "uvicorn.workers.UvicornWorker",
        "--bind", "0.0.0.0:8000", "--timeout", "300"], check=True)
    for _ in range(90):
        if sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"{B}/health"]).stdout in ("200", "404"):
            return
        time.sleep(2)
    sys.exit("backend-schemaexp が起きない")


SCHEMA_F = json.load(open(os.path.join(HERE, "schema_F.json"), encoding="utf-8"))
SCHEMA_G = json.load(open(os.path.join(HERE, "schema_G.json"), encoding="utf-8")) if os.path.exists(os.path.join(HERE, "schema_G.json")) else None


def allowed_for(cond, group):
    """条件 F のときだけ、文書の群に応じた allowedNodes と allowedRelationship（3 つ組をカンマでつないだもの）を返す。"""
    if cond not in ("F", "G"):
        return "", ""
    s = (SCHEMA_F if cond == "F" else SCHEMA_G)[group]
    trip = [f"{a},{r},{b}" for r, (srcs, tgts) in s["rels"].items() for a in srcs for b in tgts]
    return ",".join(s["nodes"]), ",".join(trip)


def ingest(doc, model, out, record, cond="A"):
    name, src, group = doc
    an, ar = allowed_for(cond, group)
    dst = os.path.join(out, "src", name)
    if not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        subprocess.run(["cp", src, dst], check=True)
    sh(["curl", "-s", "--max-time", "120", "-X", "POST", f"{B}/upload", "-F", f"file=@{dst}", "--form-string", "chunkNumber=1",
        "--form-string", "totalChunks=1", "--form-string", f"originalname={name}", "--form-string", f"model={model}"] + CRED)
    t0 = time.time()
    p = sh(["curl", "-s", "--max-time", "6000", "-X", "POST", f"{B}/extract"] + CRED + [
        "--form-string", f"model={model}", "--form-string", "source_type=local file", "--form-string", f"file_name={name}",
        "--form-string", "source_url=", "--form-string", f"allowedNodes={an}", "--form-string", f"allowedRelationship={ar}",
        "--form-string", "token_chunk_size=512", "--form-string", "chunk_overlap=50", "--form-string", "chunks_to_combine=1",
        "--form-string", "language=", "--form-string", "retry_condition=", "--form-string", "additional_instructions="] + EMB)
    sec = round(time.time() - t0)
    try:
        r = json.loads(p.stdout); x = r.get("data") or {}
        row = {"name": name, "group": group, "status": r.get("status"), "chunks": x.get("total_chunks"), "nodes": x.get("nodeCount"),
               "rels": x.get("relationshipCount"), "sec": sec, "message": (r.get("message") or "")[:200]}
    except Exception:
        row = {"name": name, "group": group, "status": "unreadable", "sec": sec, "message": (p.stdout or p.stderr)[:200]}
    record["docs"].append(row)
    print(f"  {name} {row['status']} | chunks {row.get('chunks')} | nodes {row.get('nodes')} | rels {row.get('rels')} ({sec}s) {row['message'][:80]}", flush=True)
    return row["status"] == "Success"


DEADLINE = None  # --deadline で設定。過ぎたら文書の区切りで止める（予約 r2 が gemma4 を止める前に終えるため）


def past_deadline():
    return DEADLINE is not None and datetime.datetime.now() >= DEADLINE


def run_condition(cond, docset):
    docs = RULE_DOCS if docset == "rules" else TECH_DOCS
    out = os.path.join(HERE, "runs", f"{docset}_{cond}")
    os.makedirs(out, exist_ok=True)
    record = {"cond": cond, "set": docset, "started": f"{datetime.datetime.now():%F %T}", "docs": []}
    log(f"{docset} / 条件 {cond}: グラフを空にする")
    cypher("MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 500 ROWS")
    model = "gemma4" if cond in ("A", "F", "G") else "edc"
    groups = {}
    for d in docs:
        groups.setdefault(None if cond in ("A", "F", "G") else DOCTYPE[(cond, d[2])], []).append(d)
    for doctype, ds in groups.items():
        log(f"  backend を文書の種類 {doctype or '（標準抽出）'} で作り直す（{len(ds)} 本）")
        start_backend(doctype)
        fails = 0
        for d in ds:
            if past_deadline():
                record["aborted"] = f"締め切り {DEADLINE:%F %T} を過ぎたので {d[0]} の前で止めた"
                json.dump(record, open(os.path.join(out, "run.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                log(record["aborted"]); sys.exit(2)
            if not ingest(d, model, out, record, cond):
                preflight(force=True)  # gemma4 が落ちていればここで止まる
                fails += 1
                if fails >= 3:
                    record["aborted"] = f"{doctype} で 3 本続けて失敗したので止めた（最後の理由: {record['docs'][-1]['message'][:120]}）"
                    json.dump(record, open(os.path.join(out, "run.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                    log(record["aborted"]); sys.exit(3)
            else:
                fails = 0
        json.dump(record, open(os.path.join(out, "run.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("  後処理（埋め込みと索引）と書き出し")
    sh(["curl", "-s", "--max-time", "1800", "-X", "POST", f"{B}/post_processing"] + CRED + EMB +
       ["--form-string", 'tasks=["materialize_entity_similarities","enable_hybrid_search_and_fulltext_search_in_bloom"]'])
    with open(os.path.join(out, "graph_dump.json"), "w", encoding="utf-8") as f:
        subprocess.run(["docker", "exec", "-e", f"NEO4J_URI=neo4j://{NEO_ALIAS}:7687", "-e", f"NEO4J_PASSWORD={NEO_PW}", "-i", BE, "python", "-"],
                       stdin=open(os.path.join(K, "routes", "dump_graph.py"), encoding="utf-8"), stdout=f, stderr=subprocess.DEVNULL)
    record["finished"] = f"{datetime.datetime.now():%F %T}"
    json.dump(record, open(os.path.join(out, "run.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def score():
    """recount_hs.py（値の型に Value・Condition を足したもの）で、3 つの正解ごとに採点する。"""
    sc = os.path.join(K, "hs", "score", "recount_hs.py")
    runs = os.path.join(HERE, "runs")
    plans = [("社内規定", os.path.join(HERE, "gold_rules.json"), "rules", None),
             ("試運転要領", os.path.join(K, "gold_graph.json"), "tech", os.path.join(K, "hs", "files_vtrt6.txt")),
             ("品質要求基準", os.path.join(K, "hs", "gold_hs.json"), "tech", os.path.join(K, "hs", "files_hs4.txt"))]
    md = ["# スキーマの層の比較（自動生成）", ""]
    for label, gold, docset, files in plans:
        args = []
        for c in "ABCDEFGHR":
            dump = os.path.join(runs, f"{docset}_{c}", "graph_dump.json")
            if os.path.exists(dump) and os.path.getsize(dump) > 100:
                args += ["--run", f"{c}={dump}" + (f"@{files}" if files else "")]
        if not args:
            continue
        outp = os.path.join(runs, f"recount_{docset}_{label}.json")
        p = sh(["python3", sc, "--gold", gold, "--out", outp] + args, timeout=3600)
        md += [f"## {label}", "", "\n".join(l for l in p.stdout.splitlines() if l.startswith("|")), ""]
    open(os.path.join(runs, "summary.md"), "w", encoding="utf-8").write("\n".join(md))
    print("\n".join(md))


def teardown():
    for n in (BE, EDC):
        sh(["docker", "rm", "-f", n])
    sh(["docker", "stop", NEO])
    log("backend-schemaexp と edc-api-exp を消し、neo4j-schemaexp を止めた（ボリューム neo4j-schemaexp-data は残る）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["rules", "tech"])
    ap.add_argument("--cond", default="A,B,C,D,E")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--teardown", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--deadline", default="", help="例 2026-11-01 18:20。過ぎたら文書の区切りで止める")
    a = ap.parse_args()
    if a.teardown:
        return teardown()
    if a.score:
        return score()
    if not a.set:
        sys.exit("--set rules か --set tech を指定する")
    global DEADLINE
    if a.deadline:
        DEADLINE = datetime.datetime.strptime(a.deadline, "%Y-%m-%d %H:%M")
    preflight(a.force)
    ensure_edc(); ensure_neo4j()
    for c in a.cond.split(","):
        run_condition(c.strip(), a.set)
    log("完了。続けて --score で採点する")


if __name__ == "__main__":
    main()
