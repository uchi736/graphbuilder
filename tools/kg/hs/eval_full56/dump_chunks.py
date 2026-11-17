"""本番グラフの全チャンクの本文を JSON に書き出す（読むだけ）。backend コンテナの中で動かす:
  docker exec -i backend python - < dump_chunks.py > chunks.json
"""
import json, os
from neo4j import GraphDatabase

drv = GraphDatabase.driver(os.environ.get("NEO4J_URI") or "neo4j://database:7687",
                           auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
with drv.session(database=os.environ.get("NEO4J_DATABASE") or "neo4j") as s:
    rows = s.run("MATCH (c:Chunk)-[:PART_OF]->(d:Document) RETURN c.id AS id, d.fileName AS file, "
                 "c.position AS position, c.text AS text ORDER BY file, position").data()
drv.close()
print(json.dumps(rows, ensure_ascii=False))
