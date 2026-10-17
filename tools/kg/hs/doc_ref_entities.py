# 規則で見つけた文書間の参照を、エンティティのリレーションとしてグラフに足す。backend コンテナの中で実行する（読み書き）。
#   docker exec -e NEO4J_URI=neo4j://database4:7687 -i backend python - < doc_ref_entities.py
#
# チャンク間の参照リンク（REFERS_TO、チャンク → チャンク）は検索のたどりに使う。こちらは、文書を表すエンティティどうしの線
# （HS-RU-015 -[REFERS_TO]-> HS-RU-005、HS-RU-007 -[REFERS_TO]-> JIS Z 2201）で、グラフの経路と集約の問いに使う。
#   - 取り込み対象の中の文書への参照: 参照元と参照先の文書番号のエンティティを MERGE し、REFERS_TO で結ぶ
#   - 取り込み対象の外の規格（JIS、ASTM、QC-KAI、HZTL など）への参照: 規格の名前のエンティティを MERGE し、REFERS_TO で結ぶ
#   - どちらも、参照の一文のあるチャンクから HAS_ENTITY を張る（どのチャンクから出た線かが分かるように）
# LLM は使わない。参照の検出は backend の chunk_reference_rules（規則の 8 段）をそのまま使う。
import json, os, re, sys, collections, unicodedata
sys.path.insert(0, "/code")
from neo4j import GraphDatabase
from src import chunk_reference_rules as R

DOC_NUMBER = re.compile(r"^(?:HS[-\s]?)?([A-Z]{2,4})-([A-Z]?\d{3})([A-Z])?")
JIS = re.compile(r"JIS[\s\-]*([A-Z])[\s\-]*(\d{4})")
EXTERNAL = re.compile(r"(JIS[\s\-]*[A-Z][\s\-]*\d{4}|ASTM[\s\-]*[A-Z]?[\s\-]*\d{2,4}|QC-KAI-\d{3}|HZTL\d{6}[A-Z]?|HS-VTR-\d{3})")


def doc_number(file_name):
    stem = unicodedata.normalize("NFKC", os.path.splitext(file_name)[0]).replace(" ", "")
    m = DOC_NUMBER.match(stem)
    return f"HS-{m.group(1)}-{m.group(2)}" if m else stem


def external_name(text):
    t = unicodedata.normalize("NFKC", text or "")
    m = JIS.search(t)
    if m:
        return f"JIS {m.group(1)} {m.group(2)}"
    m = EXTERNAL.search(t)
    return re.sub(r"\s+", "", m.group(1)) if m else None


driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=("neo4j", os.environ.get("NEO4J_PASSWORD", "kgb-please-change")))
with driver.session(database="neo4j") as s:
    rows = s.run("MATCH (c:Chunk)-[:PART_OF]->(d:Document) RETURN c.id AS id, d.fileName AS fileName, c.position AS position, "
                 "c.text AS text, c.section AS section").data()
result = R.find_references(rows)
edges = collections.Counter()
batch = []
for r in result["references"]:
    src_doc = r.get("source_doc")
    if not src_doc:
        continue
    a = doc_number(src_doc)
    if r.get("scope") == "other_document" and r.get("target_doc"):
        b = doc_number(r["target_doc"]); kind = "in_corpus"
    elif r.get("scope") == "external":
        b = external_name(r.get("target_document") or r.get("quote")); kind = "external"
    else:
        continue
    if not b or a == b:
        continue
    batch.append({"chunk": r["source"], "a": a, "b": b, "kind": kind, "quote": (r.get("quote") or "")[:200]})
    edges[(a, b, kind)] += 1

with driver.session(database="neo4j") as s:
    s.run("""
        UNWIND $rows AS row
        MATCH (c:Chunk {id: row.chunk})
        MERGE (a:__Entity__ {id: row.a}) ON CREATE SET a:Standard
        MERGE (b:__Entity__ {id: row.b}) ON CREATE SET b:Standard
        MERGE (a)-[r:REFERS_TO]->(b)
        SET r.method = 'rule', r.kind = row.kind, r.quote = coalesce(r.quote, row.quote)
        MERGE (c)-[:HAS_ENTITY]->(a)
        MERGE (c)-[:HAS_ENTITY]->(b)
    """, rows=batch)
driver.close()
kinds = collections.Counter(k for (_, _, k) in edges)
print(json.dumps({"references": len(result["references"]), "edges": len(edges), "by_kind": kinds,
                  "examples": [f"{a} -> {b} ({k})" for (a, b, k) in list(edges)[:25]]}, ensure_ascii=False, indent=1))
