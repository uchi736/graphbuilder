# 参照リンクを張る。backend コンテナの中で実行する（再ビルド前でも、リポジトリの最新の 2 ファイルを読み込んで動かせる）。
#   docker cp backend/src/chunk_reference_rules.py backend:/tmp/refrules/ ; docker cp backend/src/chunk_references.py backend:/tmp/refrules/
#   docker exec -e NEO4J_URI=neo4j://database3:7687 -e CHUNK_REFERENCE_MODE=rule -i backend python - < link_refs.py
# mode は環境変数 CHUNK_REFERENCE_MODE（rule = 規則だけで LLM を呼ばない、hybrid = 規則が先で残りだけ LLM）。
import importlib.util
import json
import os
import sys
import time

sys.path.insert(0, "/code")
SRC = os.environ.get("REFRULES_DIR", "/tmp/refrules")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if os.path.exists(f"{SRC}/chunk_reference_rules.py"):
    load("src.chunk_reference_rules", f"{SRC}/chunk_reference_rules.py")
    references = load("src.chunk_references", f"{SRC}/chunk_references.py")
else:
    from src import chunk_references as references

from langchain_neo4j import Neo4jGraph

graph = Neo4jGraph(url=os.environ["NEO4J_URI"], database="neo4j", username="neo4j", password=os.environ.get("NEO4J_PASSWORD", "kgb-please-change"),
                   refresh_schema=False, sanitize=True)
started = time.time()
result = references.link_chunk_references(graph)
summary = result["summary"]
print(json.dumps(summary, ensure_ascii=False, indent=1)[:4000])
print(f"所要 {time.time() - started:.1f} 秒")
rows = graph.query("MATCH (:Chunk)-[r:REFERS_TO]->(t) RETURN coalesce(r.method, '(無し)') AS method, labels(t)[0] AS target, coalesce(r.source_section, '') AS section, count(*) AS n ORDER BY method, target, section")
for row in rows:
    print(row)
