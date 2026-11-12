#!/usr/bin/env bash
# 文書の取り込み直し・削除（C8）で消える物と残る物を数える。書き込みはしない（--access-mode read）。
#   いつ : 取り込み直し・削除の前（消える見込み = /extract の応答の replaced と同じ数）と後（共有の物が残ったか）
#   守る : 本番の Neo4j（neo4j-kgb）を読むだけ。名前に ' を含む文書は扱わない
#   手順 : tools/ingest/verify_replace.sh VTR-T003D.pdf
set -u
ROOT="${GB_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
F="${1:?文書名を渡す（例: VTR-T003D.pdf）}"
PW=$(grep -E "^NEO4J_PASSWORD=" "$ROOT/backend/.env" | cut -d= -f2- | tr -d '"'"'")
q(){ docker exec neo4j-kgb cypher-shell -u neo4j -p "$PW" --access-mode read --format plain -P "f => '$F'" "$1" 2>/dev/null | tail -n +2; }
EDGES="['IS_VERSION_OF','REFERS_TO','LISTS','APPLIES_TO','ABOLISHES','REPLACES']"
echo "== $F"
echo -n "  状態・チャンク: ";   q 'MATCH (d:Document {fileName: $f}) RETURN [d.status, COUNT { (d)<-[:PART_OF]-(:Chunk) }]'
echo "== 取り込み直しで消える見込み"
echo -n "  chunks: ";     q 'MATCH (:Document {fileName: $f})<-[:PART_OF]-(c:Chunk) WHERE NOT EXISTS { (c)-[:PART_OF]->(o:Document) WHERE o.fileName <> $f } RETURN count(c)'
echo -n "  entities: ";   q 'MATCH (:Document {fileName: $f})<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e) WHERE NOT EXISTS { MATCH (e)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d2:Document) WHERE d2.fileName <> $f } AND coalesce(e.method, "") <> "rule" AND NOT coalesce(e._curated, false) AND NOT EXISTS { MATCH (d3:Document)-[r]->(e) WHERE r.method = "rule" AND d3.fileName <> $f } WITH DISTINCT e RETURN count(e)'
echo -n "  sections: ";   q 'MATCH (s:Section) WHERE s.fileName = $f AND s.method = "rule" AND NOT s:__Entity__ RETURN count(s)'
echo -n "  rule_edges: "; q "CALL () { MATCH (d:Document {fileName: \$f})-[r]->() WHERE type(r) IN $EDGES AND r.method = 'rule' RETURN count(r) AS a } CALL () { MATCH (:Document)-[r:REPLACES]->(:Document {fileName: \$f}) WHERE r.method = 'rule' RETURN count(r) AS b } RETURN a + b"
echo "== 残る物"
echo -n "  共有のエンティティ: "; q 'MATCH (:Document {fileName: $f})<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e) WITH DISTINCT e WHERE e.method = "rule" OR EXISTS { MATCH (e)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d2:Document) WHERE d2.fileName <> $f } RETURN count(e)'
echo -n "  Standard・Model（規則 / 抽出）: "; q 'MATCH (e:__Entity__) WHERE e:Standard OR e:Model WITH CASE WHEN e:Standard THEN "Standard" ELSE "Model" END AS l, coalesce(e.method, "-") AS m, count(*) AS n ORDER BY l, m RETURN collect(l + "/" + m + "=" + toString(n))'
echo "== 全体の取り残し（0 が合格）"
echo -n "  文書ノードの無い規則の節: "; q 'MATCH (s:Section) WHERE s.method = "rule" AND NOT EXISTS { MATCH (d:Document) WHERE d.fileName = s.fileName } RETURN count(s)'
echo -n "  PART_OF の無いチャンク: ";   q 'MATCH (c:Chunk) WHERE NOT EXISTS { (c)-[:PART_OF]->(:Document) } RETURN count(c)'
echo -n "  抽出された型 Section のエンティティ: "; q 'MATCH (e:__Entity__:Section) RETURN count(e)'
