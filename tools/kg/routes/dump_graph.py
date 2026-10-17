# 検証用グラフのチャンク、参照リンク、エンティティを JSON で書き出す。backend コンテナの中で実行する（読み取りだけ）。
#   docker exec -e NEO4J_URI=neo4j://database2:7687 -i backend python - < dump_graph.py > graph_dump.json
import json, os, sys
from neo4j import GraphDatabase

driver = GraphDatabase.driver(os.environ.get("NEO4J_URI", "neo4j://database2:7687"), auth=("neo4j", os.environ.get("NEO4J_PASSWORD", "kgb-please-change")))
with driver.session(database="neo4j", default_access_mode="READ") as s:
    chunks = s.run("MATCH (c:Chunk)-[:PART_OF]->(d:Document) RETURN c.id AS id, d.fileName AS fileName, c.position AS position, c.page_number AS page, c.section AS section, c.text AS text ORDER BY fileName, position").data()
    refs = s.run("MATCH (a:Chunk)-[r:REFERS_TO]->(b:Chunk) RETURN a.id AS source, b.id AS target, r.quote AS quote, r.locator AS locator, r.scope AS scope").data()
    has = s.run("MATCH (c:Chunk)-[:HAS_ENTITY]->(e:__Entity__) RETURN c.id AS chunk, elementId(e) AS entity").data()
    entities = s.run("MATCH (e:__Entity__) RETURN elementId(e) AS id, [l IN labels(e) WHERE l <> '__Entity__'] AS labels, "
                     "apoc.map.removeKeys(properties(e), ['embedding']) AS props").data()
    rels = s.run("MATCH (a:__Entity__)-[r]->(b:__Entity__) RETURN elementId(r) AS id, elementId(a) AS source, elementId(b) AS target, type(r) AS type, "
                 "apoc.map.removeKeys(properties(r), ['embedding']) AS props").data()
json.dump({"chunks": chunks, "refers_to": refs, "has_entity": has, "entities": entities, "relationships": rels}, sys.stdout, ensure_ascii=False, default=str)
