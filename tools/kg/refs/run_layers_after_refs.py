# 参照リンクの後に、文書層 → 節の層 を作り直す（backend と別の一時コンテナで実行する。backend の再起動に巻き込まれないため）
import json, logging, os, sys
sys.path.insert(0, "/code")
logging.getLogger().setLevel(logging.WARNING)
from langchain_neo4j import Neo4jGraph
from src.document_layer import build_document_layer
from src.section_layer import build_section_layer
g = Neo4jGraph(url="neo4j://database:7687", username="neo4j", password=os.environ["NEO4J_PASSWORD"], database="neo4j", refresh_schema=False)
print("document layer:", json.dumps(build_document_layer(g)["summary"], ensure_ascii=False), flush=True)
print("section layer:", json.dumps(build_section_layer(g)["summary"], ensure_ascii=False), flush=True)
