"""評価用グラフ（kgb5）のチャンクに保存した抽出の結果を、控え（kgb5_before_linkfix.json）に戻す。評価の各回を同じ抽出から始めるため。
LLM の参照リンク（REFERS_TO method = 'llm'）は、次の参照リンクの後処理（判定と特定のやり直し）で書き直される。評価用 backend の中で実行する。"""
import json, os
from neo4j import GraphDatabase
snap = json.load(open("/tmp/kgb5_before_linkfix.json"))
uri = os.environ["NEO4J_URI"]
assert "database5" in uri, uri   # 評価用のグラフだけ
drv = GraphDatabase.driver(uri, auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
with drv.session() as s:
    n = s.run("UNWIND $rows AS row MATCH (c:Chunk {id: row.id}) SET c.reference_extraction = row.stored, c.reference_unresolved = row.unresolved, "
              "c.external_references = row.external RETURN count(c) AS n", rows=snap["stored"]).single()["n"]
print("restored", n, "/", len(snap["stored"]))
