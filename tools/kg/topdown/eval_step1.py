#!/usr/bin/env python3
"""上からたどる検索の 1 段目（文書を選ぶ）を、実文書の問いで測る。backend コンテナの中で実行する。
    docker cp tools/kg/eval_multihop.json backend:/tmp/eval_multihop.json
    docker cp tools/kg/eval_questions.json backend:/tmp/eval_questions.json
    docker exec -i -w /code backend python3 - < tools/kg/topdown/eval_step1.py
測る数:
  多段の 16 問（docs 列に正解の文書の鎖がある）: 起点の文書・答えの文書が、候補（絞る前）と絞った後に入る率
  単段の 35 問（gold は答えの語）: 答えの語を含むチャンクの文書を正解の文書とみなし、同じ率を出す
"""
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, "/code")
from langchain_neo4j import Neo4jGraph  # noqa: E402
from src.shared.common_fn import load_embedding_model, get_value_from_env  # noqa: E402
from src.topdown_retriever import select_documents  # noqa: E402

g = Neo4jGraph(url=os.environ.get("NEO4J_URI", "neo4j://database:7687"), username="neo4j",
               password=os.environ["NEO4J_PASSWORD"], database="neo4j", refresh_schema=False)
emb, _dim = load_embedding_model(get_value_from_env("EMBEDDING_PROVIDER", "vllm"), get_value_from_env("EMBEDDING_MODEL", "cl-nagoya/ruri-v3-310m"))
FILES = {"T001": "HS-VTR-T001E.pdf", "T002": "HS-VTR-T002F.pdf", "T003": "VTR-T003D.pdf", "T004": "VTR-T004C.pdf", "T005": "VTR-T005B.pdf", "目録": "VTR-T_発行目録.pdf"}
sq = lambda t: re.sub(r"\s+", "", unicodedata.normalize("NFKC", t or ""))
chunks = g.query("MATCH (c:Chunk)-[:PART_OF]->(d:Document) RETURN c.text AS text, d.fileName AS f")
settings = json.loads(os.environ.get("TOPDOWN_SETTINGS", "{}") or "{}")


def gold_docs_of_answer(gold: str):
    key = sq(gold)
    docs = sorted({c["f"] for c in chunks if key and key[:12] in sq(c["text"])})
    return docs


def run(name, items, gold_fn):
    print(f"== {name}（{len(items)} 問）")
    tot = {"start_cand": 0, "start_kept": 0, "all_cand": 0, "all_kept": 0, "n": 0, "cand_size": 0, "kept_size": 0}
    rows = []
    for q in items:
        gold = gold_fn(q)
        if not gold:
            continue
        out = select_documents(g, emb, q["q"], settings)
        cand = [d["fileName"] for d in out["candidates"]]
        kept = [d["fileName"] for d in out["kept"]]
        start, answer = gold[0], gold[-1]
        tot["n"] += 1
        tot["start_cand"] += start in cand
        tot["start_kept"] += start in kept
        tot["answer_cand"] = tot.get("answer_cand", 0) + (answer in cand)
        tot["answer_kept"] = tot.get("answer_kept", 0) + (answer in kept)
        tot["all_cand"] += all(x in cand for x in gold)
        tot["all_kept"] += all(x in kept for x in gold)
        tot["cand_size"] += len(cand)
        tot["kept_size"] += len(kept)
        mark = "○" if all(x in kept for x in gold) else ("◎" if answer in kept else ("△" if answer in cand else "×"))
        rows.append((q["id"], mark, "→".join(x.replace(".pdf", "") for x in gold), [(d["fileName"].replace(".pdf", ""), round(d["score"], 2), "/".join(d["via"])) for d in out["kept"]], q["q"][:40]))
    n = max(tot["n"], 1)
    print(f"  答えの文書（鎖の最後）: 候補に {tot.get('answer_cand',0)}/{n}、絞った後に {tot.get('answer_kept',0)}/{n}")
    print(f"  起点の文書（鎖の最初）: 候補に {tot['start_cand']}/{n}、絞った後に {tot['start_kept']}/{n}")
    print(f"  正解の文書すべて: 候補に {tot['all_cand']}/{n}、絞った後に {tot['all_kept']}/{n}")
    print(f"  候補の文書数の平均 {tot['cand_size']/n:.1f}、絞った後 {tot['kept_size']/n:.1f}")
    for r in rows:
        print("  ", r[0], r[1], r[2], "| 残した:", r[3], "|", r[4])
    return tot


multi = json.load(open("/tmp/eval_multihop.json", encoding="utf-8"))
single = json.load(open("/tmp/eval_questions.json", encoding="utf-8"))


def gold_multi(q):
    chain = re.split(r"→|×", q["docs"])
    return [FILES[x.strip()] for x in chain if x.strip() in FILES]


t1 = run("多段の問い（起点 → 答えの文書）", multi, gold_multi)
t2 = run("単段の問い（答えの語を含む文書）", single, lambda q: gold_docs_of_answer(q["gold"]))
print(json.dumps({"multi": t1, "single": t2}, ensure_ascii=False))
