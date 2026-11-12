"""本番グラフから、節への参照・文書間の参照の実物を書き出す（読み取りのみ）。backend コンテナの中で実行する。
出力: refs_dump.json（chunks・section_refs・document_refs）"""
import json, os
from neo4j import GraphDatabase
drv = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
q = lambda s, **p: [r.data() for r in drv.session(database=os.environ.get("NEO4J_DATABASE") or None, default_access_mode="READ").run(s, **p)]
chunks = q("""MATCH (c:Chunk)-[:PART_OF]->(d:Document)
OPTIONAL MATCH (c)-[:IN_SECTION]->(s:Section)
RETURN c.id AS id, d.fileName AS fileName, c.position AS position, c.text AS text, c.section AS heading, collect(s.id) AS sections ORDER BY fileName, position""")
section_refs = q("""MATCH (c:Chunk)-[r:REFERS_TO]->(s:Section) MATCH (c)-[:PART_OF]->(d:Document)
OPTIONAL MATCH (s)-[:HAS_SECTION*0..3]->(sub:Section)<-[:IN_SECTION]-(t:Chunk)
WITH c, d, r, s, collect(DISTINCT t.id) AS targets
RETURN c.id AS source, d.fileName AS source_file, r.quote AS quote, r.method AS method, r.via AS via, r.source_method AS source_method,
       s.id AS section_id, s.title AS section_title, s.path AS section_path, s.fileName AS section_file, targets""")
document_refs = q("""MATCH (d:Document)-[r:REFERS_TO]->(st:Standard)<-[:IS_VERSION_OF]-(d2:Document) WHERE d2 <> d
RETURN d.fileName AS source_file, st.id AS standard, d2.fileName AS target_file, r.quotes AS quotes, r.chunks AS n_chunks""")
norm = lambda s: "".join((s or "").split())
for ref in document_refs:
    ref["source_chunks"] = [c["id"] for c in chunks if c["fileName"] == ref["source_file"] and any(norm(x) and norm(x) in norm(c["text"]) for x in ref["quotes"] or [])]
json.dump({"chunks": chunks, "section_refs": section_refs, "document_refs": document_refs}, open("/out/refs_dump.json", "w"), ensure_ascii=False, indent=1)
print(len(chunks), len(section_refs), len(document_refs), "doc refs with source chunk:", sum(1 for r in document_refs if r["source_chunks"]))
