# -*- coding: utf-8 -*-
"""保守の一覧（src/settings_overview.py）の単体試験。DB には触らない。
実行（バックエンドのイメージを別の使い捨てコンテナで使う。動いているバックエンドには入らない。メモリ上限付き）:
  cd ~/ai-stack/llm-graph-builder && docker run --rm --memory=1g --cpus=1 --network none -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$PWD/backend:/code:ro" -w /code llm-graph-builder-backend python -m pytest -q -p no:cacheprovider tests/test_settings_overview.py
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from src import settings_overview as so  # noqa: E402

WRITE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH|LOAD\s+CSV)\b|apoc\.(create|merge|refactor|periodic)", re.I)
LITERAL = r'''("[^"]*"|'[^']*'|-?\d+(?:\.\d+)?)'''


def _names():
    return [n for n, *_ in so.OVERVIEW_ENV] + [n for n, *_ in so.OVERVIEW_CONST]


def _code():
    files = glob.glob(os.path.join(ROOT, "src", "**", "*.py"), recursive=True) + [os.path.join(ROOT, "score.py")]
    texts = []
    for path in files:
        if path.endswith("settings_overview.py"):
            continue
        with open(path, encoding="utf-8") as f:
            texts.append(f.read())
    return "\n".join(texts)


def test_no_secret_names():
    assert not [n for n in _names() if so.SECRET_NAME.search(n)]
    for name in ("OPENAI_API_KEY", "NEO4J_PASSWORD", "NEO4J_URI", "LLM_MODEL_CONFIG_GEMMA4", "HF_TOKEN", "AWS_SECRET_ACCESS_KEY"):
        assert so.SECRET_NAME.search(name), name


def test_names_exist_in_code():
    code = _code()
    assert not [n for n in _names() if n not in code]
    assert not [n for n in so.OVERVIEW_CONST if not hasattr(so.constants, n[0])]


def test_defaults_match_code():
    """コードに字面で書かれた既定値が、一覧の既定値に含まれていること（コード側の既定を変えたら一覧も直す）。"""
    code = _code()
    wrong = []
    for name, _group, _meaning, default in so.OVERVIEW_ENV:
        for pattern in (rf'get_value_from_env\(\s*"{name}"\s*,\s*{LITERAL}',
                        rf'os\.environ\.get\(\s*"{name}"\s*,\s*{LITERAL}',
                        rf'os\.environ\.get\(\s*"{name}"\s*\)\s*or\s*{LITERAL}'):
            for literal in re.findall(pattern, code):
                if literal.strip("\"'") not in default:
                    wrong.append((name, literal, default))
    assert not wrong
    with open(os.path.join(ROOT, "src", "chunk_references.py"), encoding="utf-8") as f:
        mode = re.search(r'^DEFAULT_MODE\s*=\s*"(\w+)"', f.read(), re.M).group(1)
    assert dict((n, d) for n, _g, _m, d in so.OVERVIEW_ENV)["CHUNK_REFERENCE_MODE"] == mode


def test_settings_hide_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-should-not-leak")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw-should-not-leak")
    monkeypatch.setenv("LLM_MODEL_CONFIG_GEMMA4", "gemma4,http://x/v1,key-should-not-leak")
    monkeypatch.setenv("CHAT_RERANK_URL", "http://user:pw-in-url@rerank:8000/v1/rerank")
    monkeypatch.setenv("MAX_TOKEN_CHUNK_SIZE", "")
    monkeypatch.delenv("CHUNK_REFERENCE_MODEL", raising=False)
    monkeypatch.setenv("GRAPH_CLEANUP_MODEL", "gemma4cpu")
    rows = {r["name"]: r for r in so.collect_settings(env_file=os.devnull)}
    text = json.dumps(rows, ensure_ascii=False)
    assert "should-not-leak" not in text and "pw-in-url" not in text
    assert rows["CHAT_RERANK_URL"]["value"] == "http://***@rerank:8000/v1/rerank"
    assert so._safe("http://h/v1/rerank?api_key=abc&model=m") == "http://h/v1/rerank?api_key=***&model=m"
    assert (rows["MAX_TOKEN_CHUNK_SIZE"]["value"], rows["MAX_TOKEN_CHUNK_SIZE"]["source"]) == ("10000", "既定値（空の値）")
    assert rows["CHUNK_REFERENCE_MODEL"]["value"] == "gemma4cpu"
    assert rows["CHAT_RERANK_URL"]["source"] == "環境変数（docker-compose.yml・ルートの .env）"
    assert rows["VECTOR_SEARCH_TOP_K"]["value"] == str(so.constants.VECTOR_SEARCH_TOP_K)
    assert len(rows) == len(so.OVERVIEW_ENV) + len(so.OVERVIEW_CONST) + 1


def test_pending_restart(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("CHAT_RERANK_URL=http://rerank-cpu:8000/v1/rerank   # 一時\nOPENAI_API_KEY=sk-file-should-not-leak\n"
                   "KNN_MIN_SCORE=0.94\n", encoding="utf-8")
    monkeypatch.setenv("CHAT_RERANK_URL", "http://host.docker.internal:8006/v1/rerank")
    monkeypatch.setenv("KNN_MIN_SCORE", "0.94")
    rows = {r["name"]: r for r in so.collect_settings(env_file=str(env))}
    assert rows["CHAT_RERANK_URL"]["pending"] is True
    assert rows["CHAT_RERANK_URL"]["file_value"] == "http://rerank-cpu:8000/v1/rerank"
    assert rows["KNN_MIN_SCORE"]["pending"] is False and rows["KNN_MIN_SCORE"]["file_value"] is None
    assert rows["KNN_MIN_SCORE"]["source"] == "環境変数（backend/.env）"
    assert "should-not-leak" not in json.dumps(rows, ensure_ascii=False)


def test_notes(monkeypatch):
    monkeypatch.setenv("CHUNK_TOKENIZER", "/nonexistent/tokenizer.json")
    monkeypatch.setenv("PDF_CHUNK_STRATEGY", "markdown")
    monkeypatch.setenv("MARKDOWN_CHUNKING", "false")
    monkeypatch.setenv("CHAT_RERANK_ENABLED", "true")
    rows = {r["name"]: r for r in so.collect_settings(env_file=os.devnull)}
    assert rows["CHUNK_TOKENIZER"]["note"] and rows["PDF_CHUNK_STRATEGY"]["note"]
    assert "CHAT_CANDIDATE_K" in rows["VECTOR_SEARCH_TOP_K"]["note"]


def test_gunicorn_timeout():
    argv = ["/usr/local/bin/python3.12", "/usr/local/bin/gunicorn", "score:app", "--workers", "8", "--timeout", "300"]
    assert so.gunicorn_timeout(argv, "") == (300, "起動コマンド")
    assert so.gunicorn_timeout(argv[:5], "--timeout=120") == (120, "GUNICORN_CMD_ARGS")
    assert so.gunicorn_timeout(argv[:5] + ["-t60"], "") == (60, "起動コマンド")
    assert so.gunicorn_timeout(argv[:5], "") == (30, "gunicorn の既定値")
    assert so.gunicorn_timeout(["python3", "-c", "x"], "")[0] is None


def test_count_queries_read_only():
    assert not [k for k, _g, _l, q, _p in so.COUNTS if WRITE.search(q) or " AS value" not in q]
    keys = [k for k, *_ in so.COUNTS]
    assert len(keys) == len(set(keys))
    assert not [p for *_, p in so.COUNTS if p not in (None, 0) and p not in keys]


def test_collect_counts():
    def run(query):
        if "AS sub" in query:
            return [{"sub": "a.pdf", "value": 2}, {"sub": "b.pdf", "value": 3}]
        if "IN_SECTION" in query:
            return [{"value": 7}]
        if "NEXT_CHUNK" in query:
            raise RuntimeError("boom")
        return [{"value": 0}]

    rows = {r["key"]: r for r in so.collect_counts(run)}
    assert (rows["sections"]["value"], rows["sections"]["detail"]) == (5, "a.pdf 2、b.pdf 3")
    assert rows["chunks_multi_doc"]["ok"] is True and rows["chunks_multi_doc"]["pass_line"] == "0"
    assert rows["sections_without_document"]["ok"] is False
    assert rows["chunks_in_section"]["ok"] is False  # 7 と 0（節のある文書のチャンク）
    assert rows["next_chunk_cross_doc"]["value"] is None and rows["next_chunk_cross_doc"]["ok"] is None
    assert "boom" in rows["next_chunk_cross_doc"]["error"]
    assert rows["documents"]["ok"] is None and rows["documents"]["pass_line"] == "—"


def test_collect_overview_fills_missing_credentials_from_env(monkeypatch):
    """画面が接続を backend の設定に任せたとき（ユーザー名・パスワードを送らない）も取得できる。送られた値があればそれを使う"""
    import neo4j
    from src.entities.user_credential import Neo4jCredentials

    seen = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute_read(self, fn, text):
            return [{"value": 0}]

    class FakeDriver:
        def verify_connectivity(self):
            pass

        def session(self, database=None, default_access_mode=None):
            seen.append(("database", database))
            return FakeSession()

        def close(self):
            pass

    def fake_driver(uri, auth=None):
        seen.append(("uri", uri, auth))
        return FakeDriver()

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", fake_driver)
    monkeypatch.setenv("NEO4J_URI", "neo4j://env-host:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "env-user")
    monkeypatch.setenv("NEO4J_PASSWORD", "env-pass")
    monkeypatch.setenv("NEO4J_DATABASE", "envdb")
    so.collect_overview(Neo4jCredentials(uri="", userName="", password="", database=""))
    assert seen[0] == ("uri", "neo4j://env-host:7687", ("env-user", "env-pass")) and seen[1] == ("database", "envdb")
    seen.clear()
    so.collect_overview(Neo4jCredentials(uri="neo4j://given:7687", userName="u", password="p", database="d"))
    assert seen[0] == ("uri", "neo4j://given:7687", ("u", "p")) and seen[1] == ("database", "d")
    seen.clear()
    data = so.collect_overview(Neo4jCredentials(uri="neo4j://given:7687", userName="u", password="p", database="d"), include_counts=False)
    assert data["counts"] == [] and data["settings"] and seen[0] == ("uri", "neo4j://given:7687", ("u", "p"))   # 件数なしでも資格情報は確かめる
