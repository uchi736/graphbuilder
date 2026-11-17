# 作成役・検算役が開いてはいけないもの（抽出結果・変換後の本文・評価の記録）を開いていないかを、エージェントの記録から点検する（X31）。
import json, glob, re, sys, os
BAD = re.compile(r"graph_dump|eval_full56|score_out|chunks[^/\"]*\.json|/qa2?/|neo4j|cypher|/ingest|localhost:84|docker")
for wd in sys.argv[1:]:
    n = 0; viol = []
    for f in glob.glob(os.path.join(wd, "agent-*.jsonl")):
        n += 1
        meta = json.load(open(f.replace(".jsonl", ".meta.json")))
        for line in open(f, encoding="utf-8"):
            try: o = json.loads(line)
            except Exception: continue
            cont = (o.get("message") or {}).get("content")
            if not isinstance(cont, list): continue
            for c in cont:
                if c.get("type") == "tool_use":
                    s = json.dumps(c.get("input"), ensure_ascii=False)
                    if BAD.search(s): viol.append((meta.get("label") or meta.get("description", "")[:40], s[:200]))
    print(os.path.basename(wd), "エージェント", n, "違反", len(viol))
    for v in viol[:20]: print("  ", v)
