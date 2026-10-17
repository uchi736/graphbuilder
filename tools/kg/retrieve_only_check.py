"""検索だけの口（POST /retrieve_context）の確認。

使い方（稼働中のバックエンドのコンテナの中で、新しい関数を直接呼ぶ。バックエンドの再起動は要らない）:
  docker exec -e NEO4J_URI=neo4j://database2:7687 -i backend python - < tools/kg/retrieve_only_check.py
再起動のあと、ホストから稼働中の口を呼ぶ（標準ライブラリだけで動く）:
  CHECK_VIA=http CHECK_BASE=http://localhost:8440 CHECK_QUESTION='…' NEO4J_URI=neo4j://database2:7687 \
    NEO4J_USERNAME=… NEO4J_PASSWORD=… python3 tools/kg/retrieve_only_check.py

環境変数（docker exec では -e で渡す）:
  CHECK_VIA      direct（既定。retrieve_context を直接呼ぶ）/ app（score.app を同じプロセスに読み込み、口を通す）/ http（稼働中の口を呼ぶ）
  CHECK_BASE     http のときと /chat_bot との比較に使う URL（既定 http://localhost:8000。ホストからは http://localhost:8440）
  CHECK_QUESTION 質問。無ければグラフの先頭のチャンクの見出しか本文の先頭を使う（direct / app のみ）
  CHECK_MODE     既定 graph_vector_fulltext
  CHECK_SETTINGS 検索設定の JSON（既定 {}）
  CHECK_DOCS     document_names の JSON（既定 []）
  CHECK_CHAT_BOT 1 にすると同じ質問で /chat_bot を 1 回呼び（LLM を使い、Session を 1 件書く）、チャンクの ID・点数・ID の集合・文脈を比べる
  CHECK_MODES    1 にすると、ほかのモードと拒否される入力を LLM なしで流す（direct / app のみ）
  CHECK_MODEL    /chat_bot に渡すモデル（既定 gemma4）
接続先は NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD / NEO4J_DATABASE、埋め込みは EMBEDDING_PROVIDER / EMBEDDING_MODEL から読む
（コンテナには設定済み。秘密はこのファイルに書かない）。最後の行に "RESULT: OK" か "RESULT: NG" を出す。
"""
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# score を読み込むと .env が環境変数を上書きするので、設定は最初に読み切る
ENV = os.environ.get
VIA = ENV("CHECK_VIA", "direct")
BASE = ENV("CHECK_BASE", "http://localhost:8000").rstrip("/")
MODE = ENV("CHECK_MODE", "graph_vector_fulltext")
SETTINGS = ENV("CHECK_SETTINGS", "{}")
DOCS = ENV("CHECK_DOCS", "[]")
QUESTION = ENV("CHECK_QUESTION", "")
WITH_CHAT_BOT = ENV("CHECK_CHAT_BOT", "0") == "1"
WITH_MODES = ENV("CHECK_MODES", "0") == "1"
MODEL = ENV("CHECK_MODEL", "gemma4")
CRED = {"uri": ENV("NEO4J_URI", ""), "userName": ENV("NEO4J_USERNAME", ""), "password": ENV("NEO4J_PASSWORD", ""),
        "database": ENV("NEO4J_DATABASE", "neo4j"), "email": "retrieve-only-check@example.com"}
EMBED = {"embedding_provider": ENV("EMBEDDING_PROVIDER", ""), "embedding_model": ENV("EMBEDDING_MODEL", "")}
RERANK = ENV("CHAT_RERANK_ENABLED", "").strip().lower() in ("true", "1", "yes")   # バックエンドと同じ環境で動くときだけ分かる

WRITE_WORDS = re.compile(r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP)\b|setNodeVectorProperty", re.IGNORECASE)
CHUNK_KEYS = {"id": str, "text": str, "fileName": str, "position": int, "page_number": (int, type(None)),
              "section": (str, type(None)), "score": (int, float), "via_reference": bool}
problems = []


def note(ok, label, detail=""):
    print(("  ok  " if ok else "  NG  ") + label + (f" | {detail}" if detail else ""), flush=True)
    if not ok:
        problems.append(label)


def short(value, limit=160):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"…（全 {len(text)} 字）"


# ---------- 呼び出し ----------
def post(path, fields, multipart=False, timeout=300):
    if multipart:
        boundary = "----check" + uuid.uuid4().hex
        body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode() + str(v).encode("utf-8") + b"\r\n"
                        for k, v in fields.items()) + f"--{boundary}--\r\n".encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    else:
        body, headers = urllib.parse.urlencode(fields).encode(), {}
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + path, data=body, headers=headers), timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        return {"status": "HTTP " + str(exc.code), "error": exc.read().decode("utf-8", "replace")[:300]}


def form(question, mode=MODE, docs=DOCS, settings=SETTINGS):
    return {**CRED, **EMBED, "question": question, "document_names": docs, "mode": mode, "retrieval_settings": settings}


class Backend:
    """direct / app のときだけ使う。バックエンドのコードを読み込み、LLM と履歴に触れたら例外にし、流れた Cypher を控える。"""

    def __init__(self):
        from langchain_neo4j import Neo4jGraph, Neo4jVector
        import src.QA_integration as qa
        import src.llm as llm_module
        from src.entities.user_credential import Neo4jCredentials
        from src.shared.common_fn import create_graph_database_connection

        def forbidden(name):
            def raiser(*args, **kwargs):
                raise AssertionError(f"{name} was called in the retrieval-only path")
            return raiser

        qa.get_llm = llm_module.get_llm = forbidden("get_llm")
        qa.Neo4jChatMessageHistory = forbidden("Neo4jChatMessageHistory")
        self.statements = []
        for cls in (Neo4jGraph, Neo4jVector):
            original = cls.query

            def recording(obj, query, *args, _original=original, **kwargs):
                self.statements.append(str(query))
                return _original(obj, query, *args, **kwargs)
            cls.query = recording
        self.logs, self.warnings = [], []

        def emit(record):
            self.logs.append(record.getMessage())
            if record.levelno >= logging.WARNING:
                self.warnings.append(f"{record.name}: {record.getMessage()}")
        handler = logging.Handler(level=logging.INFO)
        handler.emit = emit
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        self.graph = create_graph_database_connection(Neo4jCredentials(**CRED))
        self.client = None

    def read(self, cypher, **params):
        return self.graph.query(cypher, params=params)

    def call(self, fields):
        if VIA == "app":
            if self.client is None:
                from starlette.testclient import TestClient
                import score
                self.client = TestClient(score.app)
            return self.client.post("/retrieve_context", files={k: (None, str(v)) for k, v in fields.items()}).json()
        from src.retrieve_only import retrieve_context
        keys = ("question", "document_names", "mode", "embedding_provider", "embedding_model", "retrieval_settings", "model")
        try:
            return {"status": "Success", "data": retrieve_context(self.graph, **{k: fields[k] for k in keys if k in fields})}
        except Exception as exc:
            return {"status": "Failed", "error": f"{type(exc).__name__}: {exc}"}

    def index_ready(self, label, index_name):
        """索引があり、ノードがあり、埋め込みの無いノードが無いときだけ真。
        索引が無いか埋め込みの無いノードがある接続先で検索を流すと、/chat_bot と同じく索引の作成や埋め込みの書き込みが起こる。"""
        names = {row["name"] for row in self.read("SHOW INDEXES YIELD name RETURN name")}
        total = self.read(f"MATCH (n:`{label}`) RETURN count(n) AS n")[0]["n"]
        missing = self.read(f"MATCH (n:`{label}`) WHERE n.embedding IS NULL RETURN count(n) AS n")[0]["n"]
        return (index_name in names and total > 0 and missing == 0,
                f"index {index_name}: {'あり' if index_name in names else '無し'}、{label} {total} 件、うち embedding 無し {missing} 件")


# ---------- 確認 ----------
def check_shape(data, question):
    note(set(data) == {"question", "mode", "retrieval_settings", "chunks", "entityids", "relationshipids", "references", "context"},
         "data のキーが取り決め 3 と一致", ", ".join(sorted(data)))
    note(data.get("question") == question and isinstance(data.get("mode"), str) and isinstance(data.get("retrieval_settings"), dict)
         and isinstance(data.get("context"), str), "question / mode / retrieval_settings / context の型")
    bad = [f"{c.get('id', '?')[:8]}.{k}={c.get(k)!r}" for c in data.get("chunks", []) for k, kind in CHUNK_KEYS.items()
           if k not in c or not isinstance(c[k], kind) or isinstance(c[k], bool) != (kind is bool)]
    note(not bad and all(set(c) == set(CHUNK_KEYS) for c in data.get("chunks", [])), "chunks の各項目のキーと型", "; ".join(bad[:5]))
    ids = [c["id"] for c in data.get("chunks", [])]
    note(len(ids) == len(set(ids)), "chunks の id に重複が無い", f"{len(ids)} 件")
    note(all(isinstance(x, str) for x in data.get("entityids", []) + data.get("relationshipids", [])),
         "entityids / relationshipids が文字列の配列", f"{len(data.get('entityids', []))} / {len(data.get('relationshipids', []))} 件")
    missing = [c["id"][:8] for c in data.get("chunks", []) if c["text"] and c["text"] not in data.get("context", "")]
    if RERANK:
        note(not missing, "chunks の本文がすべて文脈の文字列に入っている", ", ".join(missing))
    else:
        # リランクを使わない経路（本家の既定）は、文書行を断片に切って上位だけを文脈に入れるので、本文が全部は入らない
        print(f"  --  chunks {len(data.get('chunks', []))} 件のうち、本文が文脈にそのまま入っていないもの {len(missing)} 件（リランク無効か不明のため参考値）", flush=True)


def check_reference(backend, data, logs):
    """via_reference を、retriever のログ（reference followed）とグラフの REFERS_TO の 2 通りで確かめる。"""
    followed = set()
    for line in logs:
        if line.startswith("reference followed: "):
            for item in line[len("reference followed: "):].split(", "):
                m = re.match(r"(.*)#(-?\d+):[-0-9.e]+$", item)
                if m:
                    followed.add((m.group(1), int(m.group(2))))
    chunks = data["chunks"]
    via = [c for c in chunks if c["via_reference"]]
    wrong = [f"{c['fileName']}#{c['position']}" for c in chunks if c["via_reference"] != ((c["fileName"], c["position"]) in followed)]
    note(not wrong, "via_reference が retriever のログと一致",
         f"ログで足されたチャンク {len(followed)} 件、応答の via_reference {len(via)} 件" + (f"、不一致 {wrong}" if wrong else ""))
    if via:
        direct = [c["id"] for c in chunks if not c["via_reference"]]
        rows = backend.read("MATCH (c:Chunk)-[:REFERS_TO]->(t:Chunk) WHERE t.id IN $via AND c.id IN $direct RETURN t.id AS id, count(c) AS n",
                            via=[c["id"] for c in via], direct=direct)
        linked = {row["id"] for row in rows if row["n"] > 0}
        note(linked == {c["id"] for c in via}, "via_reference のチャンクは、直接選ばれたチャンクから REFERS_TO で届く", f"{len(linked)}/{len(via)} 件")
    return len(via)


def compare_with_chat_bot(data, question):
    t0 = time.time()
    chat = post("/chat_bot", {**form(question), "model": MODEL, "session_id": f"retrieve-only-check-{uuid.uuid4().hex[:12]}"})
    sec = time.time() - t0
    info = (chat.get("data") or {}).get("info") if isinstance(chat.get("data"), dict) else None
    if chat.get("status") != "Success" or not isinstance(info, dict) or info.get("error"):
        note(False, "/chat_bot の呼び出し", short(chat, 300))
        return None
    details = (info.get("nodedetails") or {}).get("chunkdetails") or []
    mine = {c["id"]: c["score"] for c in data["chunks"]}
    theirs = {c["id"]: c.get("score") for c in details}
    note(set(mine) == set(theirs), "選ばれたチャンクの ID が /chat_bot と一致", f"{len(mine)} 件 / {len(theirs)} 件、差 {sorted(set(mine) ^ set(theirs))[:4]}")
    diffs = [abs(mine[i] - theirs[i]) for i in set(mine) & set(theirs)]
    note(not diffs or max(diffs) <= 1e-3, "チャンクの点数が /chat_bot と一致", f"差の最大 {max(diffs) if diffs else 0:.4f}")
    entities = info.get("entities") or {}
    for key in ("entityids", "relationshipids"):
        note(set(data[key]) == set(entities.get(key) or []), f"{key} が /chat_bot と一致", f"{len(data[key])} 件 / {len(entities.get(key) or [])} 件")
    context = (info.get("metric_details") or {}).get("contexts") or ""
    detail = f"{len(data['context'])} 字 / {len(context)} 字"
    if data["context"] != context:
        a, b = data["context"].split("\n"), context.split("\n")
        at = next((i for i, (x, y) in enumerate(zip(data["context"], context)) if x != y), min(len(data["context"]), len(context)))
        only_a, only_b = [l for l in a if l not in set(b)], [l for l in b if l not in set(a)]
        detail += (f"、最初の違い {at} 字目、こちらだけの行 {len(only_a)}、/chat_bot だけの行 {len(only_b)}、行の集合は{'同じ' if not only_a and not only_b else '違う'}"
                   f"、例 {short(only_a[:2], 200)} / {short(only_b[:2], 200)}")
    note(data["context"] == context, "文脈の文字列が /chat_bot と完全に一致", detail)
    note(data["retrieval_settings"] == info.get("retrieval_settings") and data["mode"] == info.get("mode"), "適用された検索設定とモードが /chat_bot と一致")
    print(f"  /chat_bot {sec:.1f} 秒 | 回答: {short((chat['data'].get('message') or '').strip(), 120)}", flush=True)
    return round(sec, 1)


def other_modes(backend, question, main):
    """ほかのモードと、拒否されるべき入力。LLM は使わない。索引の作成や埋め込みの書き込みが起こるモードは流さない。"""
    file_name = main["chunks"][0]["fileName"]
    one_doc = json.dumps([file_name], ensure_ascii=False)
    cases = [("vector", "[]", None), ("graph_vector", one_doc, None), ("fulltext", "[]", None),
             ("entity_vector", "[]", ("__Entity__", "entity_vector")), ("global_vector", "[]", ("__Community__", "community_vector"))]
    for mode, docs, index in cases:
        if index:
            ready, why = backend.index_ready(*index)
            if not ready:
                print(f"  --  mode={mode} は流さない（{why}）", flush=True)
                continue
        t0 = time.time()
        res = backend.call(form(question, mode=mode, docs=docs))
        data = res.get("data") or {}
        ok = res.get("status") == "Success" and data.get("mode") == mode and bool(data.get("context"))
        if ok and docs != "[]":
            ok = bool(data["chunks"]) and all(c["fileName"] == file_name for c in data["chunks"])
        if ok and mode in ("entity_vector", "global_vector"):
            ok = data["chunks"] == []
        note(ok, f"mode={mode} docs={short(docs, 60)}", f"{time.time() - t0:.1f} 秒、chunks {len(data.get('chunks', []))}、entityids {len(data.get('entityids', []))}、"
             f"relationshipids {len(data.get('relationshipids', []))}、文脈 {len(data.get('context', ''))} 字" + (f"、{short(res.get('error', ''), 200)}" if not ok else ""))

    res = backend.call(form(question, mode="vector", docs='["__no_such_document__"]'))
    data = res.get("data") or {}
    note(res.get("status") == "Success" and data.get("chunks") == [] and data.get("context") == "" and data.get("entityids") == [],
         "該当なし（存在しない文書に絞る）は Success で空の結果", short(res.get("error") or "", 160))
    res = backend.call({**form(question), "model": MODEL})
    same = (res.get("data") or {}).get("context") == main["context"]
    note(res.get("status") == "Success", f"任意の項目 model={MODEL} を渡しても通る", "文脈は model 無しのときと" + ("同じ" if same else "違う（文書行の上限がモデルで変わる設定）"))
    for label, expect, fields in (("mode=graph は失敗で返る", "Failed", form(question, mode="graph")),
                                  ("文書の絞り込みができないモードに document_names を渡すと失敗で返る", "Failed", form(question, mode="graph_vector_fulltext", docs=one_doc)),
                                  ("question が空だと失敗で返る", "Failed", form("")),
                                  ("document_names が JSON の配列でないと失敗で返る", "Failed", form(question, docs="not json")),
                                  ("retrieval_settings が JSON でなくても既定値で通る", "Success", form(question, settings="not json"))):
        res = backend.call(fields)
        note(res.get("status") == expect, label, short(res.get("error") or "", 160))


def main():
    print(f"via={VIA} uri={CRED['uri']} database={CRED['database']} mode={MODE} settings={SETTINGS} docs={DOCS}", flush=True)
    if not CRED["uri"] or not CRED["userName"] or not CRED["password"]:
        sys.exit("NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD が要る")
    backend = Backend() if VIA in ("direct", "app") else None
    question = QUESTION
    if not question and backend:
        row = backend.read("MATCH (c:Chunk) WHERE c.position = 1 RETURN coalesce(c.section, left(c.text, 60)) AS q ORDER BY c.fileName LIMIT 1")
        question = (row[0]["q"] if row else "") or ""
    if not question:
        sys.exit("CHECK_QUESTION が要る")
    print(f"question={question}", flush=True)

    before = None
    if backend:
        ready, why = backend.index_ready("Chunk", "vector")
        print(f"  {why}", flush=True)
        if not ready:
            sys.exit("チャンクが無いか、流すと索引の作成や埋め込みの書き込みが起こる接続先なので、止める")
        before = backend.read("MATCH (n) WHERE n:Session OR n:Message RETURN count(n) AS n")[0]["n"]
        backend.statements.clear()
        backend.logs.clear()

    t0 = time.time()
    res = backend.call(form(question)) if backend else post("/retrieve_context", form(question), multipart=True)
    sec = time.time() - t0
    if res.get("status") != "Success":
        note(False, "/retrieve_context の呼び出し", short(res, 400))
        print("RESULT: NG")
        sys.exit(1)
    data = res["data"]
    note(True, "検索だけの呼び出しが Success", f"{sec:.2f} 秒")
    check_shape(data, question)
    n_via = None
    if backend:
        statements, logs = list(backend.statements), list(backend.logs)
        writes = [s for s in statements if WRITE_WORDS.search(s)]
        note(not writes, "流れた Cypher に書き込みが無い", f"{len(statements)} 本" + (f"、書き込み: {short(writes[0], 200)}" if writes else ""))
        after = backend.read("MATCH (n) WHERE n:Session OR n:Message RETURN count(n) AS n")[0]["n"]
        note(before == after, "Session / Message の数が呼び出しの前後で同じ（ほかの利用者が同時に /chat_bot を使うと変わりうる）", f"{before} → {after}")
        note(True, "LLM と履歴に触れていない（get_llm と Neo4jChatMessageHistory を例外に差し替えた状態で通った）")
        n_via = check_reference(backend, data, logs)
        for line in logs:
            if line.startswith(("rerank picked", "reference followed", "line rerank")):
                print("  log " + short(line, 260), flush=True)
        for line in backend.warnings[:5]:
            print("  warn " + short(line, 260), flush=True)

    chat_sec = compare_with_chat_bot(data, question) if WITH_CHAT_BOT else None
    if WITH_MODES and backend and data["chunks"]:
        other_modes(backend, question, data)

    example = dict(data, context=short(data["context"], 300), entityids=data["entityids"][:2] + ["…"], relationshipids=data["relationshipids"][:2] + ["…"],
                   chunks=[dict(c, text=short(c["text"], 80)) for c in data["chunks"]])
    print("EXAMPLE " + json.dumps({"status": res["status"], "data": example}, ensure_ascii=False), flush=True)
    print("SUMMARY " + json.dumps({"via": VIA, "question": question, "mode": data["mode"], "seconds": round(sec, 2), "chat_bot_seconds": chat_sec,
                                   "chunks": len(data["chunks"]), "via_reference": n_via, "entityids": len(data["entityids"]),
                                   "relationshipids": len(data["relationshipids"]), "context_chars": len(data["context"]), "problems": problems}, ensure_ascii=False), flush=True)
    print("RESULT: " + ("OK" if not problems else "NG"))
    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    main()
