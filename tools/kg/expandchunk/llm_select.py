"""LLM（gemma4）にチャンクを選ばせ、答えのチャンクを拾えるかをリランカーと比べる。呼び出しは 1 件ずつ順に行う。"""
import json, re, time, urllib.request
exec(open("run.py", encoding="utf-8").read().split("out = []")[0])
def chat(prompt):
    body = {"model": "google/gemma-4-26B-A4B-it", "messages": [{"role": "user", "content": prompt}], "temperature": 0, "max_tokens": 300}
    return post("http://localhost:8000/v1/chat/completions", body)["choices"][0]["message"]["content"] or ""
INSTR = ("次の質問に答えるために必要な情報を含む文書片の番号を、すべて挙げてください。"
         "質問が複数のことを聞いている場合は、どれか 1 つにでも必要な文書片を含めてください。"
         "他の文書を参照している記述や、図番・文書番号が書かれた文書片も、答えに必要なら含めてください。"
         "出力は番号だけをカンマ区切りで 1 行にしてください。該当がなければ「なし」と出力してください。")
def ask(question, items):
    body = "\n\n".join(f"[{i + 1}]（{CH[c]['doc'].replace('.pdf', '')}）{CH[c]['n'][:600]}" for i, c in enumerate(items))
    out = chat(f"{INSTR}\n\n# 質問\n{question}\n\n# 文書片\n{body}")
    nums = {int(x) for x in re.findall(r"\d+", out.split("\n")[0]) if 1 <= int(x) <= len(items)}
    return [items[n - 1] for n in sorted(nums)], out
six = [c for c in CH if c in SIX]
res = []; t0 = time.time()
for qid, qq in QS.items():
    text = qq["q"]; s = rerank(text, [CH[c]["text"] for c in six]); order = [c for c, _ in sorted(zip(six, s), key=lambda x: -x[1])]
    rec = {"id": qid}
    for name, groups in (("rerank_top6", None), ("llm_top20", [order[:20]]), ("llm_all84", [order[0:28], order[28:56], order[56:84]])):
        if groups is None: sel = order[:6]; raw = ""
        else:
            sel = []; raw = []
            for g in groups:
                picked, o = ask(text, g); sel += picked; raw.append(o[:120])
        facts = [any(any(k in CH[c]["n"] for k in keys) for c in sel) for keys in FACTS[qid]]
        rec[name] = {"n": len(sel), "chars": sum(len(CH[c]["n"]) for c in sel), "facts": facts, "sel": [(CH[c]["doc"].replace(".pdf", ""), CH[c]["pos"]) for c in sel], "raw": raw}
    res.append(rec)
    print(qid, {k: (v["n"], v["facts"]) for k, v in rec.items() if k != "id"}, f"{time.time() - t0:.0f}s", flush=True)
json.dump(res, open("llm_select.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for name in ("rerank_top6", "llm_top20", "llm_all84"):
    hit = sum(sum(r[name]["facts"]) for r in res); tot = sum(len(r[name]["facts"]) for r in res)
    print(name, f"事実 {hit}/{tot}", "平均件数", round(sum(r[name]["n"] for r in res) / len(res), 1), "平均字数", round(sum(r[name]["chars"] for r in res) / len(res)))
