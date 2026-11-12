# 含意判定（LLM 判定の 3 層目）に渡す材料を作る。鍵の照合で決まらないもの（向き・否定・条件・「食い違い」の指摘・列挙の漏れ）を人または Claude が判定する。
#   python3 make_judge_items.py out/run1/results.json ../out2/kg_eval_v1.json out/run1/judge_items.json
# 出力: [{qid, cond, run, aspect, question, reference_answer, answer_keys, model_answer, mechanical: {...}}]
import json, sys

R = json.load(open(sys.argv[1], encoding="utf-8"))
S = {x["qid"]: x for x in json.load(open(sys.argv[2], encoding="utf-8"))}
items = []
for r in R:
    it = S[r["qid"]]
    items.append({"qid": r["qid"], "cond": r["cond"], "run": r["run"], "aspect": r["aspect"], "question": it["question"],
                  "reference_answer": it["answer"], "answer_keys": it.get("answer_keys_primary") or it.get("answer_keys"),
                  "model_answer": r["answer"], "mechanical": r["score"]})
json.dump(items, open(sys.argv[3], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(items), "items")
