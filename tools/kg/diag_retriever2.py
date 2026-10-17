# GB 本体のリトリーバ連鎖をそのまま呼び、STEP1(候補4件) → 文書行 → 3000tok断片 → 類似度上位4片 を記録する
#   docker exec backend python /tmp/diag_retriever2.py <tag>   → /tmp/diag_retriever_<tag>.json
import os, sys, json, logging
logging.disable(logging.CRITICAL)
sys.path.insert(0, "/code"); os.chdir("/code")
from langchain_core.messages import HumanMessage
from src.QA_integration import get_neo4j_retriever, create_document_retriever_chain, retrieve_documents, format_documents
from src.shared.constants import CHAT_MODE_CONFIG_MAP
from src.llm import get_llm
from langchain_neo4j import Neo4jGraph
tag = sys.argv[1] if len(sys.argv) > 1 else "x"
graph = Neo4jGraph(url=os.environ["NEO4J_URI"], username=os.environ["NEO4J_USERNAME"], password=os.environ["NEO4J_PASSWORD"], database="neo4j", sanitize=True, refresh_schema=False)
cfg = dict(CHAT_MODE_CONFIG_MAP["graph_vector_fulltext"], mode="graph_vector_fulltext")
retriever = get_neo4j_retriever(graph=graph, document_names=[], chat_mode_settings=cfg, embedding_provider="vllm", embedding_model="cl-nagoya/ruri-v3-310m")
llm = get_llm("gemma4")[0]
chain = create_document_retriever_chain(llm, retriever, "vllm", "cl-nagoya/ruri-v3-310m")
QS = {"M06": ("発行目録に載っている RU・RH型初号機の基準では、性能計測はどの試験方案（図番）に基づきますか？", ["V00862"]),
      "M12": ("TPL.TPS形初号機の性能計測時、外部給油方式の過給機の潤滑油温度は何℃以下であることが求められますか？", ["+45", "潤滑油温度 JUDGED_BY", "REQ-VTR-T001-3-6-2"]),
      "M04": ("コンプレッサ性能管理曲線管理基準が関連基準として挙げる VTR4シリーズ初号機の要領で、サージラインの判定基準は何%以内ですか？", ["±3%", "3%以内"]),
      "M08": ("TPL.TPS形過給機の吸込温度の計測点数と、その型式の初号機で量産機の性能計測時に適用する nMmax の出典を答えてください。", ["6点", "V02313"]),
      "M07": ("発行目録で『コンプレッサ性能管理曲線管理基準』とされる文書が関連基準の1番目に挙げる基準では、タービン入口ガス温度をどの範囲で管理しますか？", ["-10%", "0~-10", "タービン入口ガス温度 JUDGED_BY", "REQ-VTR-T001-2-9-7"])}
def pos_of(cid):
    r = graph.query("MATCH (c:Chunk {id:$id})-[:PART_OF]->(d:Document) RETURN d.fileName AS doc, c.position AS pos", {"id": cid})
    return (r[0]["doc"], r[0]["pos"]) if r else (None, None)
out = {"tag": tag, "retriever_kwargs": dict(retriever.search_kwargs), "search_type": retriever.search_type}
for qid, (q, keys) in QS.items():
    rec = {"q": q, "keys": keys}
    base = retriever.invoke(q)
    rows = []
    for d in base:
        md = d.metadata; chs = []
        for c in md.get("chunkdetails", []):
            doc, pos = pos_of(c["id"]); chs.append({"id": c["id"], "score": round(c["score"], 4), "doc": doc, "pos": pos})
        txt = d.page_content
        rows.append({"source": md.get("source"), "chunks": chs, "len_chars": len(txt),
                     "n_entity_lines": txt.count("\n", txt.find("Entities:"), txt.find("Relationships:")) if "Entities:" in txt and "Relationships:" in txt else None,
                     "n_rel_lines": txt[txt.find("Relationships:"):].count("\n") if "Relationships:" in txt else None,
                     "ents_meta": len((md.get("entities") or {}).get("entityids", [])), "rels_meta": len((md.get("entities") or {}).get("relationshipids", [])),
                     "keys_in_row": [k for k in keys if k in txt]})
    rec["rows"] = rows
    docs, _ = retrieve_documents(chain, [HumanMessage(content=q)])
    # format_documents と同じ並べ替え（上位4片）
    ranked = sorted(docs, key=lambda d: d.state.get("query_similarity_score", 0), reverse=True)
    pieces = []
    for i, d in enumerate(ranked):
        t = d.page_content
        pieces.append({"rank": i + 1, "source": d.metadata.get("source"), "sim": round(d.state.get("query_similarity_score", 0), 4), "len_chars": len(t),
                       "has_text_content": t.lstrip().startswith("Text Content:"), "head": " ".join(t.split())[:70],
                       "keys_found": [k for k in keys if k in t], "kept": i < 4})
    rec["pieces"] = pieces
    ctx, sources, ents, _ = format_documents(docs, "gemma4", cfg)
    rec["final_context_chars"] = len(ctx); rec["final_sources"] = sorted(sources)
    rec["final_keys_found"] = [k for k in keys if k in ctx]
    rec["final_n_rel_lines"] = ctx.count(" REFERENCES ") + ctx.count(" DEFINED_IN ") + ctx.count(" JUDGED_BY ") + ctx.count(" MEASURED_AT ") + ctx.count(" MEASURED_BY ") + ctx.count(" APPLIES_TO ") + ctx.count(" PERFORMED_BY ") + ctx.count(" ON ") + ctx.count(" APPLIES_WHEN ") + ctx.count(" HAS_CRITERION ") + ctx.count(" HAS_REQUIREMENT ") + ctx.count(" SOURCE ")
    rec["final_context_head"] = ctx[:600]
    out[qid] = rec
    print(f"{qid}: rows={len(rows)} pieces={len(pieces)} kept_sources={rec['final_sources']} final_keys={rec['final_keys_found']} ctx_chars={len(ctx)}", flush=True)
    for r in rows: print(f"   row {r['source']} chunks={[(c['doc'][:10] if c['doc'] else None, c['pos'], c['score']) for c in r['chunks']]} chars={r['len_chars']} keys={r['keys_in_row']}")
    for p in pieces: print(f"   {'KEEP' if p['kept'] else 'drop'} #{p['rank']} sim={p['sim']} {p['source'][:14]} {'TEXT' if p['has_text_content'] else '    '} chars={p['len_chars']} keys={p['keys_found']} | {p['head'][:48]}")
json.dump(out, open(f"/tmp/diag_retriever_{tag}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved", f"/tmp/diag_retriever_{tag}.json")
