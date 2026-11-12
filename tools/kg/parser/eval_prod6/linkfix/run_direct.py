"""評価用 backend の中で、参照リンク（判定と特定のやり直し）→ 文書層 → 章・節の層 を直接流す（API を通さない。LLM と結び先の選び方を環境変数で選ぶ）。
使い方: docker exec -e CHUNK_REFERENCE_MODEL=gemma4 [-e CHUNK_REFERENCE_LINK_STYLE=legacy] -e PYTHONPATH=/code -w /code backend-parser python run_direct.py
接続先は評価用の kgb5（database5）に固定する。"""
import json, logging, os, sys
os.environ["NEO4J_URI"] = "neo4j://database5:7687"
from langchain_neo4j import Neo4jGraph
from src.chunk_references import link_chunk_references
from src.document_layer import build_document_layer
from src.section_layer import build_section_layer
logging.basicConfig(level=logging.WARNING)
g = Neo4jGraph(url="neo4j://database5:7687", username="neo4j", password="kgb-please-change", refresh_schema=False)
out = {"model": os.environ.get("CHUNK_REFERENCE_MODEL"), "style": os.environ.get("CHUNK_REFERENCE_LINK_STYLE") or "outline"}
out["chunk_references"] = link_chunk_references(g, reresolve=True)["summary"]
out["document_layer"] = build_document_layer(g)["summary"]
out["section_layer"] = build_section_layer(g)["summary"]
print(json.dumps(out, ensure_ascii=False, default=str)[:3000])
