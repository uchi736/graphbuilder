#!/usr/bin/env bash
# 標準検証の指標 (TS3-5 を除外)。使い方: metrics.sh <ラベル>
CY() { docker exec neo4j-kgb cypher-shell -u neo4j -p kgb-please-change --format plain "$1" 2>/dev/null | tail -n +2; }
X="d.fileName IS NOT NULL AND d.fileName <> 'TS3-5.pdf'"
echo "### $1"
echo "--- 文書ごと (チャンク / エンティティ) ---"; CY "MATCH (d:Document) WHERE $X OPTIONAL MATCH (d)<-[:PART_OF]-(c:Chunk) OPTIONAL MATCH (c)-[:HAS_ENTITY]->(e:__Entity__) RETURN d.fileName, count(DISTINCT c), count(DISTINCT e), d.processingTime ORDER BY d.fileName"
echo "--- 全体 ---"
CY "MATCH (d:Document) WHERE $X MATCH (d)<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e:__Entity__) WITH collect(DISTINCT e) AS es UNWIND es AS e OPTIONAL MATCH (e)-[r]->(e2:__Entity__) WHERE e2 IN es RETURN size(es) AS ents, count(DISTINCT r) AS rels, count(DISTINCT type(r)) AS rel_types" | sed 's/^/  ents,rels,rel_types: /'
CY "MATCH (d:Document) WHERE $X MATCH (d)<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e:__Entity__) WITH DISTINCT e RETURN sum(CASE WHEN NOT (e)-[]-(:__Entity__) THEN 1 ELSE 0 END) AS orphans, sum(CASE WHEN size(e.id)<=2 THEN 1 ELSE 0 END) AS short_ids, count(DISTINCT [l IN labels(e) WHERE l<>'__Entity__'][0]) AS label_types" | sed 's/^/  orphans,short_ids,label_types: /'
echo "--- ラベル上位 ---"; CY "MATCH (d:Document) WHERE $X MATCH (d)<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e:__Entity__) WITH DISTINCT e UNWIND [l IN labels(e) WHERE l<>'__Entity__'] AS l WITH l, count(*) AS c ORDER BY c DESC LIMIT 10 RETURN l+':'+toString(c)" | tr -d '"' | tr '\n' ' '; echo
echo "--- 述語上位 ---"; CY "MATCH (d:Document) WHERE $X MATCH (d)<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(a:__Entity__) WITH collect(DISTINCT a) AS es UNWIND es AS a MATCH (a)-[r]->(b:__Entity__) WHERE b IN es WITH type(r) AS t, count(DISTINCT r) AS c ORDER BY c DESC LIMIT 10 RETURN t+':'+toString(c)" | tr -d '"' | tr '\n' ' '; echo
echo "--- 様式語ハブ (接続本数) ---"; CY "MATCH (e:__Entity__) WHERE e.id IN ['作成','審査','承認','改訂','来歴','改正番号','配布先','備考'] WITH e, size([(e)-[]-() | 1]) AS n RETURN e.id+':'+toString(n)" | tr -d '"' | tr '\n' ' '; echo
echo "--- ゴミIDの例 ---"; CY "MATCH (d:Document) WHERE $X MATCH (d)<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e:__Entity__) WITH DISTINCT e WHERE size(e.id)<=2 RETURN collect(e.id)[..20]"
echo "--- 文書間: 共有エンティティ (文書数別) ---"; CY "MATCH (e:__Entity__)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d:Document) WHERE $X WITH e, count(DISTINCT d) AS n WHERE n>1 RETURN n, count(e) ORDER BY n"
echo "--- 文書間: 全6本で共有 ---"; CY "MATCH (e:__Entity__)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d:Document) WHERE $X WITH e, count(DISTINCT d) AS n WHERE n>=5 RETURN collect(e.id)[..15]"
echo "--- 文書間: 両端が別文書の関係 ---"; CY "MATCH (da:Document)<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(a:__Entity__)-[r]->(b:__Entity__)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(db:Document) WHERE da.fileName<>'TS3-5.pdf' AND db.fileName<>'TS3-5.pdf' AND da<>db AND NOT (a)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(db) RETURN count(DISTINCT r)"
echo "--- 参照: 規格番号エンティティと Document の接続 ---"; CY "MATCH (e:__Entity__) WHERE toUpper(e.id) CONTAINS 'VTR-T00' OR toUpper(e.id) CONTAINS 'QC-KAI' OR e.id CONTAINS 'V0' WITH e OPTIONAL MATCH (e)-[r]-(d:Document) WHERE d.fileName IS NOT NULL RETURN e.id, labels(e)[1], count(r) AS to_document ORDER BY e.id LIMIT 20"
