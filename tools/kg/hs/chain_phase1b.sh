#!/bin/sh
# 比較の後半: 人手スキーマ → EDC に定義を足す → backend を再起動 → EDC＋新しい定義。前半（chain_phase1a.sh）の終わりを待ってから始める。
cd /home/uchida/ai-stack/llm-graph-builder/tools/kg/hs
while [ ! -f phase1a.done ]; do sleep 20; done
ONLY=$(cat gold_docs.txt)
python3 run_hs_method.py schema neo4j-kgb6 database6 p1_schema --only "$ONLY" > runs_p1_schema.log 2>&1
./apply_quality_doctype.sh > apply_quality_doctype.log 2>&1
# 種類の判定を確かめる（品質要求基準と試運転の 1 本ずつ）
for f in HS-RU-002F HS-VTR-T001E; do
  python3 - "$f" >> apply_quality_doctype.log 2>&1 <<'PY'
import json, sys, urllib.request
S = "/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad"
d = json.load(open(f"{S}/frame-impl/out/chunk_check.json", encoding="utf-8"))
name = next(k for k in d["chunks"] if k.startswith(sys.argv[1]) and not k.endswith("::meta"))
text = "\n".join(c["text"] for c in d["chunks"][name][:4])[:3000]
req = urllib.request.Request("http://localhost:8081/classify", data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
print(sys.argv[1], "->", json.load(urllib.request.urlopen(req, timeout=300)))
PY
done
docker restart backend > /dev/null
for i in $(seq 1 40); do curl -s --max-time 3 http://localhost:8440/health > /dev/null 2>&1 && break; sleep 3; done
python3 run_hs_method.py edc neo4j-kgb6 database6 p1_edc_quality --only "$ONLY" > runs_p1_edc_quality.log 2>&1
echo PHASE1B_DONE > phase1b.done
