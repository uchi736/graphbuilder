# チャンク間の参照リンクを作り、抽出した参照と結び先を JSON に書き出す。backend コンテナ内で実行する。
#   docker exec -e NEO4J_URI=neo4j://database2:7687 -i backend python - [write|dry] < run_refs.py   → /tmp/chunk_refs.json
import json, logging, os, sys, time
sys.path.insert(0, "/code")
from langchain_neo4j import Neo4jGraph
from src.chunk_references import link_chunk_references

logging.getLogger().setLevel(logging.WARNING)
write = (sys.argv[1] if len(sys.argv) > 1 else "write") == "write"
reextract = "reextract" in sys.argv[2:]
reresolve = "reresolve" in sys.argv[2:]
graph = Neo4jGraph(url=os.environ.get("NEO4J_URI", "neo4j://database2:7687"), username="neo4j",
                   password=os.environ.get("NEO4J_PASSWORD", "kgb-please-change"), database="neo4j", refresh_schema=False)
t0 = time.time()
result = link_chunk_references(graph, write=write, reextract=reextract, reresolve=reresolve)
texts = {r["id"]: r for r in graph.query(
    "MATCH (c:Chunk)-[:PART_OF]->(d:Document) RETURN c.id AS id, c.text AS text, c.position AS position, d.fileName AS doc")}
for ref in result["references"]:
    ref["source_text"] = texts[ref["source"]]["text"]
    for t in ref["targets"]:
        t["text"] = texts[t["id"]]["text"]
result["seconds"] = round(time.time() - t0)
result["links"] = graph.query("MATCH (:Chunk)-[r:REFERS_TO]->(t) WHERE r.method = 'llm' RETURN labels(t)[0] AS target, r.scope AS scope, count(*) AS n ORDER BY target, scope")
json.dump(result, open("/tmp/chunk_refs.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(result["summary"], ensure_ascii=False), f"seconds={result['seconds']} links={result['links']}")
