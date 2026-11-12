#!/usr/bin/env python3
"""条件 G のスキーマを作る: 人手スキーマ（schema_F.json）に、サンプル 30 チャンク（条件 F の診断と同じ、各群 15）から
Graph Builder の「文章からスキーマ」機能（/populate_graph_schema、model=gemma4、説明つきでない既定のプロンプト）が出した 3 つ組を足す。
群ごとに 5 チャンクずつ 3 回呼び、出た 3 つ組を和集合にする。人手の名前への寄せや取捨はしない（実務で「LLM に足させる」のと同じ）。
出力: schema_G.json（F と同じ形）、runs/tech_G/schema_proposals.json（呼び出しごとの生の出力）。2026-11-04。"""
import json, os, re, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_schema_exp as R

R.preflight(False); R.ensure_neo4j(); R.start_backend(None)
F = json.load(open(os.path.join(HERE, "schema_F.json"), encoding="utf-8"))
chunks = json.load(open(os.path.join(HERE, "runs", "tech_F", "diag_input.json"), encoding="utf-8"))["chunks"]
out = os.path.join(HERE, "runs", "tech_G"); os.makedirs(out, exist_ok=True)
raw = {}; G = {"_note": "条件 G＝人手スキーマ（schema_F.json）＋サンプル 30 チャンクから Graph Builder の『文章からスキーマ』機能（gemma4）が出した 3 つ組の和集合。make_schema_G.py で作成（2026-11-04）。"}
pat = re.compile(r"^\s*([^-\s>]+)\s*-\s*\[?:?([A-Za-z0-9_]+)\]?\s*->\s*([^-\s>]+)\s*$")
for g, cs in chunks.items():
    raw[g] = []; nodes = list(F[g]["nodes"]); rels = {r: [list(s), list(t)] for r, (s, t) in F[g]["rels"].items()}
    added_t = 0; bad = []
    for i in range(0, len(cs), 5):
        text = "\n\n".join(c["text"] for c in cs[i:i + 5])
        p = R.sh(["curl", "-s", "--max-time", "600", "-X", "POST", f"{R.B}/populate_graph_schema", "--form-string", f"input_text={text}",
                  "--form-string", "model=gemma4", "--form-string", "is_schema_description_checked=false", "--form-string", "is_local_storage=false"])
        try:
            r = json.loads(p.stdout); trip = (r.get("data") or {}).get("triplets") or []
        except Exception:
            r = {"unreadable": p.stdout[:500]}; trip = []
        raw[g].append({"batch": i // 5, "status": r.get("status"), "triplets": trip, "error": r.get("error")})
        print(g, i // 5, r.get("status"), len(trip), flush=True)
        for t in trip:
            m = pat.match(t.replace("（", "(").replace("）", ")"))
            if not m:
                bad.append(t); continue
            a, rel, b = m.group(1), m.group(2).upper(), m.group(3)
            for n in (a, b):
                if n not in nodes: nodes.append(n)
            s, tt = rels.setdefault(rel, [[], []])
            new = False
            if a not in s: s.append(a); new = True
            if b not in tt: tt.append(b); new = True
            added_t += new
    G[g] = {"nodes": nodes, "rels": rels}
    nF = sum(len(s) * len(t) for s, t in F[g]["rels"].values()); nG = sum(len(s) * len(t) for s, t in rels.values())
    print(f"{g}: 型 {len(F[g]['nodes'])}→{len(nodes)}、関係名 {len(F[g]['rels'])}→{len(rels)}、3 つ組 {nF}→{nG}、読めない {len(bad)} {bad[:5]}")
    raw[g + "_unparsed"] = bad
json.dump(raw, open(os.path.join(out, "schema_proposals.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(G, open(os.path.join(HERE, "schema_G.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
