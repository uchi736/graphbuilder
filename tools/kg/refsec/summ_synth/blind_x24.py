"""X24 の内容判定の材料。参照先の集合が違う・到達が違う・道具の正答が違う問いについて、2 条件の回答を X／Y に伏せて並べる。
対応表は blind_key_x24.json に書く（判定が済むまで見ない）。

使い方: python3 blind_x24.py results_x24.json blind_x24.md blind_key_x24.json
"""
import glob
import json
import pathlib
import random
import sys

A, B = "参照追跡", "参照追跡・一覧"
Q = {}
for p in sorted(glob.glob(str(pathlib.Path(__file__).resolve().parents[2] / "synth" / "questions" / "*.json"))):
    d = json.load(open(p, encoding="utf-8"))
    for q in (d["questions"] if isinstance(d, dict) else d):
        Q[q["qid"]] = q

out = json.load(open(sys.argv[1], encoding="utf-8"))["questions"]
rng = random.Random(20261110)
key, lines = {}, []
for rec in out:
    ra, rb = rec["runs"].get(A), rec["runs"].get(B)
    if not (ra and rb):
        continue
    diff = ({e["chunk_id"] for e in ra.get("references") or []} != {e["chunk_id"] for e in rb.get("references") or []}
            or ra["answer_in"] != rb["answer_in"] or bool(ra.get("correct")) != bool(rb.get("correct")))
    if not diff:
        continue
    pair = [(A, ra), (B, rb)]
    rng.shuffle(pair)
    key[rec["qid"]] = {"X": pair[0][0], "Y": pair[1][0]}
    q = Q[rec["qid"]]
    lines += [f"## {rec['qid']}（{rec['type']}）", f"問い: {q['question']}", f"正解: {q.get('answer')}", f"要の語: {q.get('answer_keys')}",
              f"取り違えの値: {q.get('wrong_values')}", "", f"### X", (pair[0][1].get("answer") or "").strip(), "", f"### Y", (pair[1][1].get("answer") or "").strip(), ""]
open(sys.argv[2], "w", encoding="utf-8").write("\n".join(lines))
json.dump(key, open(sys.argv[3], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"{len(key)} 問")
