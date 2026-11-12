#!/usr/bin/env bash
# 段 3（文書層 B2・B3、章・節の層 B4）の合格条件を、現在のグラフで集計する。書き込みはしない。
#   いつ : build_document_layer / build_section_layer を実行した後（再取り込みの後）
#   守る : 参照のみ。本番の Neo4j（neo4j-kgb）に問い合わせるのみで、何も変更しない
#   手順 : tools/ingest/verify_stage3.sh            （文書どうしの REFERS_TO の採点は tools/kg/refs/score_doclayer.py）
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PW=$(grep -E "^NEO4J_PASSWORD=" "$ROOT/backend/.env" | cut -d= -f2- | tr -d '"'"'")
q(){ docker exec neo4j-kgb cypher-shell -u neo4j -p "$PW" --format plain "$1" 2>/dev/null | tail -n +2; }

echo "== 6.1 文書層"
echo -n "  文書番号ノード（取り込み済み文書、規則により生成）: "; q "MATCH (s:Standard) WHERE s.method='rule' AND NOT coalesce(s.external,false) RETURN collect(s.id)"
echo -n "  文書番号ノード（未取り込み文書）: ";               q "MATCH (s:Standard) WHERE s.method='rule' AND coalesce(s.external,false) RETURN count(s)"
echo -n "  正規形が同一の文書番号の重複（0 件で合格）: ";               q "MATCH (s:Standard) WHERE s.method='rule' WITH toUpper(s.id) AS k, count(*) AS n WHERE n>1 RETURN count(k)"
echo -n "  IS_VERSION_OF（ファイル, 文書番号, 版）: ";         q "MATCH (d:Document)-[r:IS_VERSION_OF]->(s) RETURN collect([d.fileName, s.id, r.rev])"
echo -n "  名寄せ後に残存する別表記（0 件で合格）: ";           q "MATCH (e:__Entity__) WHERE (e.method IS NULL OR e.method<>'rule') AND toUpper(replace(e.id,' ','')) =~ '(HS-?)?VTR-?T[0-9]{3}[A-Z]?' RETURN count(e)"
echo -n "  Standard への HAS_ENTITY: ";                    q "MATCH (:Chunk)-[:HAS_ENTITY]->(s:Standard) WHERE s.method='rule' RETURN count(*)"
echo -n "  文書間の REFERS_TO（取り込み済み / 未取り込み）: ";       q "MATCH (:Document)-[r:REFERS_TO]->(:Standard) WHERE r.method='rule' RETURN [count(CASE WHEN r.scope<>'external' THEN 1 END), count(CASE WHEN r.scope='external' THEN 1 END)]"
echo -n "  LISTS（文書番号, 最新の改訂記号, 日付）: ";             q "MATCH (:Document)-[r:LISTS]->(s) RETURN collect([s.id, r.rev, r.date])"
echo -n "  revisions を持つ Standard: ";                   q "MATCH (s:Standard) WHERE s.revisions IS NOT NULL RETURN count(s)"
echo -n "  REPLACES / ABOLISHES: ";                        q "RETURN [size([()-[r:REPLACES]->() WHERE r.method='rule' | r]), size([()-[r:ABOLISHES]->() WHERE r.method='rule' | r])]"
echo -n "  APPLIES_TO（文書ごと）: ";                      q "MATCH (d:Document)-[r:APPLIES_TO]->() WHERE r.method='rule' WITH d, count(r) AS n RETURN collect([d.fileName, n])"
echo "== 6.2 章・節の層"
echo -n "  Section（文書ごと）: ";                         q "MATCH (s:Section) WHERE s.method='rule' WITH s.fileName AS f, count(s) AS n RETURN collect([f,n])"
echo -n "  IN_SECTION を持つチャンク: ";                   q "MATCH (c:Chunk)-[:IN_SECTION]->(:Section) RETURN count(DISTINCT c)"
echo -n "  章・節を持つ文書のチャンク（上と同数で合格。規則の目録が見出しを検出した文書）: "; q "MATCH (c:Chunk)-[:PART_OF]->(d:Document) WHERE EXISTS { MATCH (s:Section {fileName: d.fileName}) WHERE s.method='rule' } RETURN count(c)"
echo -n "  章・節の生成元（catalogue=規則の目録 / markdown=見出しの経路）: "; q "MATCH (s:Section) WHERE s.method='rule' WITH coalesce(s.source,'markdown') AS src, count(*) AS n RETURN collect([src,n])"
echo -n "  管理用の見出し（来歴・改訂履歴など）の Section: ";                         q "MATCH (s:Section) WHERE s.method='rule' AND s.control RETURN count(s)"
echo -n "  章・節への REFERS_TO（via 別）: ";                   q "MATCH (:Chunk)-[r:REFERS_TO {level:'section'}]->(:Section) WITH r.via AS v, count(*) AS n RETURN collect([v,n])"
echo "== 副作用"
echo -n "  A1 複数の文書に属するチャンク（0 件で合格）: ";          q "MATCH (c:Chunk)-[:PART_OF]->(d:Document) WITH c, count(DISTINCT d) AS n WHERE n>1 RETURN count(c)"
echo -n "  A2 ファイル名のない Document（0 件で合格）: ";          q "MATCH (d:Document) WHERE d.fileName IS NULL RETURN count(d)"
echo -n "  抽出でラベル Section が付与されたエンティティ（0 件で合格）: "; q "MATCH (e:__Entity__:Section) RETURN count(e)"
echo -n "  埋め込みのない、規則により生成したエンティティ（0 件で合格）: ";       q "MATCH (e:__Entity__) WHERE e.method='rule' AND e.embedding IS NULL RETURN count(e)"
echo -n "  抽出失敗チャンク（文書ごと、空で合格）: "; q "MATCH (d:Document) WHERE coalesce(d.skipped_chunks,0) > 0 RETURN collect([d.fileName, d.skipped_chunks])"
