#!/bin/sh
# EDC に「購入品_品質要求基準」の定義を足す（schemas は edc-api に読み取り専用でマウントされ、依頼のたびに読み直される）。
# 元に戻すときは registry.json.bak.20261016 を registry.json に戻し、追加した 2 つの CSV と few_shot_quality を消す。
set -eu
E=/home/uchida/ai-stack/EDC/schemas; Q=/home/uchida/ai-stack/llm-graph-builder/tools/kg/hs/edc_quality
[ -f $E/registry.json.bak.20261016 ] || cp $E/registry.json $E/registry.json.bak.20261016
cp $Q/domain_quality_schema.csv $Q/domain_quality_types.csv $E/
mkdir -p $E/few_shot_quality && cp $Q/few_shot_quality/*.txt $E/few_shot_quality/
python3 - <<'PY'
import json
E = "/home/uchida/ai-stack/EDC/schemas"; Q = "/home/uchida/ai-stack/llm-graph-builder/tools/kg/hs/edc_quality"
reg = json.load(open(f"{E}/registry.json", encoding="utf-8"))
entry = json.load(open(f"{Q}/registry_entry.json", encoding="utf-8"))
reg["doctypes"] = [d for d in reg["doctypes"] if d["name"] != entry["name"]] + [entry]
json.dump(reg, open(f"{E}/registry.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("doctypes:", [d["name"] for d in reg["doctypes"]])
PY
