# 参照リンク（B1）を、Luna（luna-meter 経由）で 6 本に流し、結果を /tmp/chunk_refs_luna.json に書く。backend コンテナで実行する。
#   docker exec -d -w /code backend sh -c 'python3 - < /code/../tools/kg/refs/run_refs_luna.py > /tmp/run_refs_luna.log 2>&1'
import json, logging, os, sys, time, urllib.request
sys.path.insert(0, "/code")
from langchain_neo4j import Neo4jGraph
from src.chunk_references import link_chunk_references
logging.getLogger().setLevel(logging.WARNING)
FILES = ["HS-VTR-T001E.pdf", "HS-VTR-T002F.pdf", "VTR-T003D.pdf", "VTR-T004C.pdf", "VTR-T005B.pdf", "VTR-T_発行目録.pdf"]

def ask(prompt: str) -> str:
    body = {"model": "luna", "messages": [{"role": "user", "content": prompt}], "max_tokens": 4000}
    req = urllib.request.Request("http://luna-meter:8000/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-Client": "b1"})
    d = json.loads(urllib.request.urlopen(req, timeout=300).read())
    return d["choices"][0]["message"].get("content") or ""

graph = Neo4jGraph(url="neo4j://database:7687", username="neo4j", password=os.environ["NEO4J_PASSWORD"], database="neo4j", refresh_schema=False)
t0 = time.time()
result = link_chunk_references(graph, file_names=FILES, ask=ask, write=True, reextract=True, reresolve=True, mode="llm")
texts = {r["id"]: r for r in graph.query("MATCH (c:Chunk)-[:PART_OF]->(d:Document) RETURN c.id AS id, c.text AS text, c.position AS position, d.fileName AS doc")}
for ref in result["references"]:
    ref["source_text"] = texts.get(ref["source"], {}).get("text", "")
    for t in ref["targets"]:
        t["text"] = texts.get(t["id"], {}).get("text", "")
result["seconds"] = round(time.time() - t0)
json.dump(result, open(os.environ.get("REFS_OUT", "/tmp/chunk_refs_luna.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(result["summary"], ensure_ascii=False), "seconds", result["seconds"], flush=True)
