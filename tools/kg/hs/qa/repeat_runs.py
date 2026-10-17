"""同じ問いを同じ条件で繰り返し流し、回答の揺れを数える。eval_hs.py の呼び出しと採点をそのまま使う。
使い方: python3 qa/repeat_runs.py <出力> --db database4 --times 5
"""
import json, sys, time, argparse, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import eval_hs as E
ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("--db", default="database4"); ap.add_argument("--times", type=int, default=5)
a = ap.parse_args()
E.CRED["uri"] = f"neo4j://{a.db}:7687"
qs = {q["qid"]: q for q in json.load(open(pathlib.Path(__file__).parent / "questions_hs.json", encoding="utf-8"))["questions"]}
plan = [("HS-11", "本文だけ"), ("HS-19", "本文だけ"), ("HS-15", "本文だけ"), ("HS-16", "本文だけ"), ("HS-17", "本文だけ"),
        ("HS-13", "本文だけ"), ("HS-12", "グラフあり"), ("HS-02", "本文だけ"), ("HS-08", "本文だけ")]
relaxed = {"HS-13": [["835"]], "HS-12": [["735"]], "HS-02": [["Aマーク", "「A」マーク"], ["記録票"]], "HS-08": [["630"]]}
out = []
for qid, name in plan:
    q = qs[qid]; items = [[E.norm(x) for x in alts] for alts in q.get("answer_items") or []]
    for i in range(a.times):
        t0 = time.time()
        try:
            r = E.call(q["question"], E.SETTINGS[name], True, f"rep-{qid}-{i}-{int(t0)}")
        except Exception as e:
            print(qid, name, i, "失敗", str(e)[:100], flush=True); continue
        text = E.norm(r["answer"])
        rec = {"qid": qid, "setting": name, "i": i, "answer": r["answer"], "chunk_ids": r["chunk_ids"], "context_chars": len(r["context"]), "sec": round(time.time() - t0, 1)}
        if items:
            found = [any(E.has_key(text, x) for x in alts) for alts in items]
            rec["items"] = [bool(f) for f in found]; rec["correct"] = all(found)
        else:
            rec["correct"] = all(any(E.has_key(text, E.norm(x)) for x in g) for g in relaxed[qid])
        out.append(rec)
        print(qid, name, i, "○" if rec["correct"] else "×", rec.get("items", ""), rec["sec"], "s", flush=True)
        json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
