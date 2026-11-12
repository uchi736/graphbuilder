"""章・節と文書層の参照を、正しくたどれているかを確かめる（X18 の動作確認）。読み取りのみ。

段 1（照会）: backend コンテナの中で、検索部品の照会文（SECTION_REFERENCE_QUERY・DOCUMENT_LAYER_REFERENCE_QUERY）に参照元のチャンクを
       1 つずつ渡し、返る参照先がグラフ（refs_dump.json）と一致するかを見る。  python check_traversal.py query
段 2（検索）: ホストから本番の /retrieve_context に、参照元の一文を質問として送り、新しい追跡で足されたチャンクが、その参照の参照先
       （章・節の参照先のチャンク、または参照先の文書のチャンク）の中にあるかを見る。2 つの選び方（target・link）で確かめる。
       python3 check_traversal.py search
"""
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
dump = json.load(open(os.path.join(HERE, "refs_dump.json"), encoding="utf-8"))
review = json.load(open(os.path.join(HERE, "refs_review.json"), encoding="utf-8"))
file_of = {c["id"]: c["fileName"] for c in dump["chunks"]}


def query_stage():
    from neo4j import GraphDatabase
    sys.path.insert(0, "/code")
    from src import rerank_retriever as rr
    drv = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
    run = lambda q, **p: [r.data() for r in drv.session(database=os.environ.get("NEO4J_DATABASE") or None, default_access_mode="READ").run(q, **p)]
    ok = ng = 0
    for i, ref in enumerate(dump["section_refs"]):
        rows = run(rr.SECTION_REFERENCE_QUERY, ids=[ref["source"]], query_vector=None, per_section=100)
        got = {r["id"] for r in rows if r["section_id"] == ref["section_id"] and (r["quote"] or "") == (ref["quote"] or "")}
        want = set(ref["targets"]) - {ref["source"]}
        if got == want:
            ok += 1
        else:
            ng += 1
            print(f"  NG 節 #{i}: 期待 {len(want)} 件・照会 {len(got)} 件")
    print(f"段 1 章・節: 参照先がグラフと一致 {ok}/{ok + ng}")
    ok = ng = 0
    for i, ref in enumerate(dump["document_refs"]):
        hit = False
        for src in ref["source_chunks"]:
            rows = run(rr.DOCUMENT_LAYER_REFERENCE_QUERY, ids=[src])
            hit = hit or any(r["file_name"] == ref["target_file"] and r["standard"] == ref["standard"] for r in rows)
        ok += hit
        ng += not hit
        if not hit:
            print(f"  NG 文書 #{i}: {ref['source_file']} → {ref['standard']} → {ref['target_file']}")
    print(f"段 1 文書層: 参照元のチャンクから参照先の文書に届く {ok}/{ok + ng}")


def retrieve(question, settings):
    data = urllib.parse.urlencode({"uri": "", "userName": "", "password": "", "database": "", "question": question, "document_names": "[]",
                                   "mode": "graph_vector_fulltext", "model": "gemma4", "embedding_provider": "vllm",
                                   "embedding_model": "cl-nagoya/ruri-v3-310m", "retrieval_settings": json.dumps(settings)}).encode()
    return json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:8440/retrieve_context", data=data), timeout=300))["data"]


def search_stage():
    out = []
    items = [("section", i, r) for i, r in enumerate(dump["section_refs"])] + [("document", i, r) for i, r in enumerate(dump["document_refs"])]
    for kind, i, ref in items:
        verdict = review["section_refs" if kind == "section" else "document_refs"][i]["verdict"]
        if kind == "section":
            question, source_ids = ref["quote"], {ref["source"]}
            targets = set(ref["targets"]) - source_ids
            flag = {"reference_sections": True}
        else:
            question, source_ids = ref["quotes"][0], set(ref["source_chunks"])
            targets = {c["id"] for c in dump["chunks"] if c["fileName"] == ref["target_file"]}
            flag = {"reference_document_layer": True}
        rec = {"kind": kind, "index": i, "verdict": verdict, "question": question}
        for select in ("target", "link"):
            r = retrieve(question, {"reference_max": 6, "reference_select": select, **flag})
            seeds = [c["id"] for c in r["chunks"] if not c.get("via_reference")]
            added = [x for x in (r.get("references") or []) if x.get("target_kind") in ("section", "document_layer")]
            in_target = [x["chunk_id"] for x in added if x["chunk_id"] in targets]
            outside = [x["chunk_id"] for x in added if x["chunk_id"] not in targets]
            already = sorted(targets & set(seeds))
            if not (source_ids & set(seeds)):
                state = "起点が上位 6 件に無い"
            elif in_target:
                state = "参照先に到達"
            elif already and targets <= set(seeds) | {x["chunk_id"] for x in (r.get("references") or [])}:
                state = "参照先は既に文脈にある"
            elif already:
                state = "参照先の一部が既に文脈にあり、足していない"
            else:
                state = "足していない"
            rec[select] = {"state": state, "added_in_target": in_target, "added_outside": outside,
                           "added_from_other_refs": [x["chunk_id"] for x in added if x["chunk_id"] not in targets]}
        out.append(rec)
        print(f"{kind:8} #{i:2} {verdict:4} target: {rec['target']['state']:22} link: {rec['link']['state']:22} 外れ {len(rec['target']['added_outside'])}/{len(rec['link']['added_outside'])}", flush=True)
    json.dump(out, open(os.path.join(HERE, "traversal_check.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    for kind in ("section", "document"):
        for select in ("target", "link"):
            print(kind, select, dict(Counter(r[select]["state"] for r in out if r["kind"] == kind)))


if __name__ == "__main__":
    query_stage() if sys.argv[1:] == ["query"] else search_stage()
