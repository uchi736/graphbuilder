# ワークフローの journal.jsonl から作成役・検算役の返り値を集め、assemble.py の入力（results.json）にする（X31）。
import json, sys
out = {}
for jp in sys.argv[2:]:
    label = {}
    for line in open(jp, encoding="utf-8"):
        o = json.loads(line)
        if o["type"] == "started": label[o["agentId"]] = o["label"]
        elif o["type"] == "result":
            kind, doc = label[o["agentId"]].split(":", 1)
            out.setdefault(doc, {"doc": doc, "created": None, "verified": None})
            out[doc]["created" if kind == "create" else "verified"] = o["result"]
json.dump(list(out.values()), open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
print(len(out), "文書", "検算なし:", [d for d, v in out.items() if not v["verified"]])
