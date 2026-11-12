#!/usr/bin/env python3
"""文書どうしの REFERS_TO（段 3 B2）を、手作業の正解 gold_refs.json の文書をまたぐ参照（16 件・15 組）と突き合わせる。
読むだけ。backend コンテナの中で実行する:
    docker exec -i -w /code backend python3 - < tools/kg/refs/score_doclayer.py
正解の文書名（VTR-T003D.pdf など）は、そのファイルの IS_VERSION_OF の先（HS-VTR-T003）に読み替えて照合する。
"""
import collections
import json
import os
import pathlib
import sys

GOLD = pathlib.Path("/code/../tools/kg/refs/gold_refs.json")
if not GOLD.exists():
    GOLD = pathlib.Path(os.environ.get("GOLD_REFS", "/tmp/gold_refs.json"))
from langchain_neo4j import Neo4jGraph  # noqa: E402

g = Neo4jGraph(url=os.environ.get("NEO4J_URI", "neo4j://database:7687"), username="neo4j",
               password=os.environ["NEO4J_PASSWORD"], database="neo4j", refresh_schema=False)
gold = json.load(open(GOLD, encoding="utf-8"))["references"]
number_of = {r["f"]: r["no"] for r in g.query("MATCH (d:Document)-[:IS_VERSION_OF]->(s) RETURN d.fileName AS f, s.id AS no")}
pairs_gold = collections.Counter((r["src"], number_of.get(r["target"], r["target"])) for r in gold if r["src"] != r["target"])
got = {(r["f"], r["no"]): r for r in g.query(
    "MATCH (d:Document)-[r:REFERS_TO]->(s:Standard) WHERE r.method='rule' AND r.scope<>'external' RETURN d.fileName AS f, s.id AS no, r.chunks AS chunks, r.quotes AS quotes")}
hit = [k for k in pairs_gold if k in got]
miss = [k for k in pairs_gold if k not in got]
extra = [k for k in got if k not in pairs_gold]
print(f"正解の組 {len(pairs_gold)}（件数 {sum(pairs_gold.values())}）: 一致 {len(hit)}、取れていない {len(miss)}、正解に無い {len(extra)}")
for k in miss:
    keys = [r["key"] for r in gold if (r["src"], number_of.get(r["target"], r["target"])) == k]
    print("  取れていない:", k, "| 正解の表記:", keys)
for k in extra:
    print("  正解に無い:", k, "| 根拠", got[k]["chunks"], (got[k]["quotes"] or [""])[0][:60])
ok = len(hit) >= 13 and len(extra) <= 3
print("合否:", "合格" if ok else "不合格", "（線: 一致 13 以上かつ余分 3 以下）")
sys.exit(0 if ok else 1)
