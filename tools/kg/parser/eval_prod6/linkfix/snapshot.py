"""評価用グラフ（kgb5）の LLM の参照リンク・節への参照・チャンクに保存した抽出の結果を控える（読み取りのみ）。評価用 backend の中で実行する。
出力: /out/snapshot.json"""
import json, os, sys
from neo4j import GraphDatabase
drv = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
q = lambda s: [r.data() for r in drv.session(default_access_mode="READ").run(s)]
out = {
    "chunk_refs": q("MATCH (a:Chunk)-[r:REFERS_TO]->(b) WHERE r.method = 'llm' RETURN a.id AS src, coalesce(b.id, b.fileName) AS tgt, labels(b) AS labels, properties(r) AS props"),
    "section_refs": q("MATCH (a:Chunk)-[r:REFERS_TO]->(s:Section) RETURN a.id AS src, s.id AS sid, properties(r) AS props"),
    "stored": q("MATCH (c:Chunk) WHERE c.reference_extraction IS NOT NULL RETURN c.id AS id, c.reference_extraction AS stored, c.reference_unresolved AS unresolved, c.external_references AS external"),
}
name = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
json.dump(out, open(f"/out/{name}.json", "w"), ensure_ascii=False, indent=1)
print({k: len(v) for k, v in out.items()})
