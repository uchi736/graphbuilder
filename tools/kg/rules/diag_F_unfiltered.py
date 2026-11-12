"""条件 F の取りこぼしの内訳: 同じ人手スキーマ・同じ LLM で、絞り込み（strict_mode）を外した出力を一部のチャンクで取り、
捨てられる線を「関係名が一覧外」「関係名は一覧内だが型の組が 3 つ組に無い」「端点の型が一覧外」に分けて数える。
backend-schemaexp の中で実行する（stdin に入力 JSON、stdout に結果 JSON）。2026-11-04"""
import sys, json, asyncio
sys.path.insert(0, "/code")
from langchain_core.documents import Document
from langchain_experimental.graph_transformers import LLMGraphTransformer
from src.llm import get_llm
from src.shared.constants import ADDITIONAL_INSTRUCTIONS

inp = json.load(sys.stdin)
llm, _, _ = get_llm("gemma4")

async def run_group(group, chunks):
    s = inp["schema"][group]
    nodes = s["nodes"]
    trip = [(a, r, b) for r, (srcs, tgts) in s["rels"].items() for a in srcs for b in tgts]
    tf = LLMGraphTransformer(llm=llm, node_properties=["description"], relationship_properties=["description"],
                             allowed_nodes=nodes, allowed_relationships=trip, strict_mode=False,
                             additional_instructions=ADDITIONAL_INSTRUCTIONS)
    sem = asyncio.Semaphore(4)
    async def one(c):
        async with sem:
            try:
                g = (await tf.aconvert_to_graph_documents([Document(page_content=c["text"])]))[0]
                return [{"s": r.source.id, "st": r.source.type, "p": r.type, "o": r.target.id, "ot": r.target.type} for r in g.relationships]
            except Exception as e:
                return {"error": str(e)[:200]}
    res = await asyncio.gather(*[one(c) for c in chunks])
    lower_nodes = {n.lower() for n in nodes}; lower_rels = {r.lower() for r in s["rels"]}
    lower_trip = {(a.lower(), r.lower(), b.lower()) for a, r, b in trip}
    cnt = {"chunks": len(chunks), "errors": 0, "raw": 0, "kept": 0, "node_type_outside": 0, "rel_name_outside": 0, "type_pair_not_in_triples": 0}
    examples = {"rel_name_outside": [], "type_pair_not_in_triples": [], "node_type_outside": []}
    for r in res:
        if isinstance(r, dict): cnt["errors"] += 1; continue
        for x in r:
            cnt["raw"] += 1
            st, p, ot = x["st"].lower(), x["p"].lower(), x["ot"].lower()
            if st not in lower_nodes or ot not in lower_nodes: k = "node_type_outside"
            elif p not in lower_rels: k = "rel_name_outside"
            elif (st, p, ot) not in lower_trip: k = "type_pair_not_in_triples"
            else: cnt["kept"] += 1; continue
            cnt[k] += 1
            if len(examples[k]) < 12: examples[k].append(x)
    return {"counts": cnt, "examples": examples, "raw": res}

async def main():
    out = {}
    for group, chunks in inp["chunks"].items():
        out[group] = await run_group(group, chunks)
    json.dump(out, sys.stdout, ensure_ascii=False)
asyncio.run(main())
