#!/usr/bin/env bash
# 段 1（A1 チャンク ID・A4 取り込み直し）と B1（参照リンク）の合格条件を、2 本の文書で確かめる。
#   いつ : 取り込みの直し（チャンク ID・前の版の削除・参照リンク）を変えた後
#   守る : 本番の Neo4j（neo4j-kgb）に対して同じ名前の文書を取り込み直すだけで、他の文書は触らない
#   手順 : tools/ingest/verify_stage1.sh <PDF のフォルダ> [モデル名]   例: verify_stage1.sh /path/to/two_pdfs gemma4cpu
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DIR="${1:?PDF のフォルダ}"; MODEL="${2:-gemma4}"
PW=$(grep -E "^NEO4J_PASSWORD=" "$ROOT/backend/.env" | cut -d= -f2- | tr -d '"'"'")
q(){ docker exec neo4j-kgb cypher-shell -u neo4j -p "$PW" --format plain "$1" 2>&1 | tail -n +2; }
names=$(ls "$DIR"/*.pdf | xargs -n1 basename | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()], ensure_ascii=False))")

echo "== 前: 対象の文書 $names"
echo -n "  複数の文書に属するチャンク: "; q "MATCH (c:Chunk)-[:PART_OF]->(d:Document) WITH c, count(DISTINCT d) AS n WHERE n>1 RETURN count(c)"
echo -n "  文書をまたぐ NEXT_CHUNK: ";   q "MATCH (a:Chunk)-[:NEXT_CHUNK]->(b:Chunk) WHERE a.fileName<>b.fileName RETURN count(*)"
echo -n "  対象文書のチャンク数: ";       q "MATCH (d:Document)<-[:PART_OF]-(c:Chunk) WHERE d.fileName IN $names RETURN d.fileName, count(c)" | tr '\n' ' '; echo

echo "== 取り込み直し（--force、後処理なし）"
python3 "$ROOT/tools/ingest/ingest.py" "$DIR" --model "$MODEL" --force --tasks "" || true

echo "== 参照リンクの後処理（link_chunk_references）"
curl -s --max-time 3600 -X POST http://localhost:8440/post_processing \
  --form-string "uri=neo4j://database:7687" --form-string "userName=neo4j" --form-string "password=$PW" \
  --form-string "database=neo4j" --form-string "email=ingest@local" --form-string 'tasks=["link_chunk_references"]' \
  --form-string "embedding_provider=vllm" --form-string "embedding_model=cl-nagoya/ruri-v3-310m" | cut -c1-400; echo

echo "== 後: 合格条件"
echo -n "  A1 複数の文書に属するチャンク(0 が合格): "; q "MATCH (c:Chunk)-[:PART_OF]->(d:Document) WITH c, count(DISTINCT d) AS n WHERE n>1 RETURN count(c)"
echo -n "  A1 文書をまたぐ NEXT_CHUNK(0 が合格): ";   q "MATCH (a:Chunk)-[:NEXT_CHUNK]->(b:Chunk) WHERE a.fileName<>b.fileName RETURN count(*)"
echo -n "  A4 対象文書のチャンク数(前と同じ程度・重複なし): "; q "MATCH (d:Document)<-[:PART_OF]-(c:Chunk) WHERE d.fileName IN $names RETURN d.fileName, count(c)" | tr '\n' ' '; echo
echo -n "  A1 ID の形(文書名を含むハッシュ=文書ごとに別): "; q "MATCH (d:Document)<-[:PART_OF]-(c:Chunk) WHERE d.fileName IN $names RETURN count(DISTINCT c.id) AS ids, count(c) AS chunks"
echo -n "  B1 チャンク間の REFERS_TO(method 付き): "; q "MATCH (a:Chunk)-[r:REFERS_TO]->(b) WHERE r.method IS NOT NULL RETURN r.method, labels(b)[0], count(*)" | tr '\n' ' '; echo
echo -n "  A2 ファイル名の無い Document(0 が合格): "; q "MATCH (d:Document) WHERE d.fileName IS NULL RETURN count(d)"
