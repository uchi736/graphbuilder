"""事実ごとの仮説（どの経路で届くはずか）を、検索の実測と突き合わせる。

使い方: python3 verify_routes.py <仮説の JSON> <出力の JSON>
前提: graph_dump.json（dump_graph.py の出力）、バックエンド（:8440、/retrieve_context あり）、埋め込みとリランカーが稼働中。
LLM は呼ばない。回答の当否は、既に取ってある 16 問の記録から読む。

測るもの（事実ごと）:
  - チャンク検索: 候補 20 件の中での順位と、リランク後の順位。上位 6 件に入るか
  - つなぎの記述（参照の一文など）のチャンクが上位 6 件に入るか
  - 参照リンク: つなぎのチャンクや上位 6 件から、答えのチャンクへ REFERS_TO があるか。たどる設定で実際に足されるか
  - グラフ: 答えの語を持つエンティティがあるか。検索の展開で取り出されるか。文脈の行に入るか
  - 回答: 4 通りの構成で、答えの語が回答に入ったか
"""
import json
import pathlib
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

HERE = pathlib.Path(__file__).parent
KG = HERE.parent
BACKEND = "http://localhost:8440"
CRED = {"uri": "neo4j://database2:7687", "userName": "neo4j", "password": "kgb-please-change", "database": "neo4j", "email": "test@example.com"}
norm = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")))
# 回答の採点に使ってきた、事実ごとの短い語（グラフの中の値は「6点」のように短く入っているので、照合にはこちらを使う）
SHORT_KEYS = {"M01": [["50kPa", "50 kPa"]], "M02": [["10分"]], "M03": [["0.8"]], "M04": [["±3%", "3%以内"]], "M05": [["02313"]], "M06": [["00862"]],
              "M07": [["-10%", "−10%", "0~-10", "0〜-10", "0~−10"]], "M08": [["6点"], ["02313"]], "M09": [["2点"], ["4台"]], "M10": [["検査部門長"]],
              "M11": [["027"], ["00862"], ["02313"]], "M12": [["+45"]], "M13": [["回転機事業部", "過給機部検査課"]],
              "M14": [["取付ない", "取り付けない", "取付けない"], ["6点"]], "M15": [["±2%", "2%以内"], ["C側"]], "M16": [["T側"]]}


def doc_code(name: str) -> str:
    name = unicodedata.normalize("NFKC", name or "")
    m = re.search(r"T00\d", name)
    return m.group(0) if m else ("目録" if "目録" in name else name)


def retrieve(question: str, settings: dict) -> dict:
    data = urllib.parse.urlencode({**CRED, "question": question, "document_names": "[]", "mode": "graph_vector_fulltext", "model": "gemma4",
                                   "embedding_provider": "vllm", "embedding_model": "cl-nagoya/ruri-v3-310m",
                                   "retrieval_settings": json.dumps(settings)}).encode()
    d = json.load(urllib.request.urlopen(urllib.request.Request(BACKEND + "/retrieve_context", data=data), timeout=300))
    if d.get("status") != "Success":
        raise RuntimeError(str(d)[:300])
    return d["data"]


def graph_lines(context: str) -> str:
    """文脈のうち、エンティティとリレーションの行だけ（チャンクの本文を除く）"""
    return "\n".join(re.findall(r"(?:Entities:|Relationships:)(.*?)(?=Document start|Text Content:|\Z)", context or "", flags=re.S))


def main():
    hyp = json.load(open(sys.argv[1], encoding="utf-8"))
    facts = hyp["facts"] if "facts" in hyp else hyp["merged"]["facts"]
    dump = json.load(open(HERE / "graph_dump.json", encoding="utf-8"))
    questions = {x["id"]: x["q"] for x in json.load(open(KG / "eval_multihop.json", encoding="utf-8"))}
    chunks = {c["id"]: dict(c, code=doc_code(c["fileName"]), n=norm(c["text"])) for c in dump["chunks"]}
    refs = {}
    for r in dump["refers_to"]:
        refs.setdefault(r["source"], set()).add(r["target"])
    entity_text = {e["id"]: norm(json.dumps(e["props"], ensure_ascii=False)) for e in dump["entities"]}
    entity_name = {e["id"]: norm(e["props"].get("id") or "") for e in dump["entities"]}
    chunk_entities = {}
    for h in dump["has_entity"]:
        chunk_entities.setdefault(h["chunk"], set()).add(h["entity"])

    traces = {}
    for name, path in (("GB・参照なし", "trace_v4_md512.json"), ("GB・参照 2 件", "trace_v4_md512_ref2.json")):
        traces[name] = {r["id"]: unicodedata.normalize("NFKC", " ".join(r["answer"].split())) for r in json.load(open(KG / path, encoding="utf-8"))}
    dify = json.load(open(KG / "trace_dify_16q.json", encoding="utf-8"))
    for label, key in (("Dify・ツール", "ツール連携"), ("Dify・外部ナレッジ", "外部ナレッジ連携")):
        traces[label] = {r["id"]: unicodedata.normalize("NFKC", " ".join(re.sub(r"<button.*", "", r["answer"], flags=re.S).split())) for r in dify[key]["rows"]}

    cache = {}

    def runs(qid):
        if qid not in cache:
            q = questions[qid]
            cache[qid] = {
                "all": retrieve(q, {"rerank_top_n": 20, "candidate_k": 20, "reference_max": 0}),   # 候補 20 件すべてをリランクの順に
                "top6": retrieve(q, {"reference_max": 0}),                                          # 既定（上位 6 件）
                "ref2": retrieve(q, {"reference_max": 2}),                                          # 参照先を 2 件足す（この配備の既定）
                "ref10": retrieve(q, {"reference_max": 10}),                                        # 参照先を 10 件まで足す
            }
        return cache[qid]

    out = []
    for f in facts:
        qid, keys = f["qid"], [norm(k) for k in f["answer_keys"] if norm(k)]
        code = doc_code(f["answer_doc"])
        answer_ids = {i for i, c in chunks.items() if c["code"] == code and any(k in c["n"] for k in keys)}
        bridge_ids = set()
        if f.get("bridge_needed") and f.get("bridge_quote"):
            bq = norm(f["bridge_quote"])
            bcode = doc_code(f.get("bridge_doc") or f.get("entry_doc") or "")
            for i, c in chunks.items():
                if c["code"] != bcode or not bq:
                    continue
                m = SequenceMatcher(None, bq, c["n"], autojunk=False).find_longest_match(0, len(bq), 0, len(c["n"]))
                if m.size >= max(8, int(len(bq) * 0.6)):
                    bridge_ids.add(i)
        r = runs(qid)
        order = [c["id"] for c in r["all"]["chunks"]]
        rank = lambda ids, seq: min((seq.index(i) + 1 for i in ids if i in seq), default=None)
        top6 = [c["id"] for c in r["top6"]["chunks"]]
        ref2 = {c["id"]: c for c in r["ref2"]["chunks"]}
        ref10 = {c["id"]: c for c in r["ref10"]["chunks"]}
        link_from_bridge = any(answer_ids & refs.get(b, set()) for b in bridge_ids)
        link_from_top6 = any(answer_ids & refs.get(s, set()) for s in top6)
        lines = norm(graph_lines(r["ref2"]["context"]))
        expanded = set(r["ref2"]["entityids"])
        short = [norm(k) for k in SHORT_KEYS[qid][f["fact_no"] - 1]]
        # 答えのチャンクから抽出されたエンティティのうち、答えの語を値に持つもの（グラフの上での、この事実）
        from_answer_chunks = set().union(*[chunk_entities.get(i, set()) for i in answer_ids]) if answer_ids else set()
        fact_entities = {i for i in from_answer_chunks if any(k in entity_text[i] for k in short)}
        rec = {
            "qid": qid, "fact_no": f["fact_no"], "fact": f["fact"], "answer_doc": code, "answer_where": f["answer_where"],
            "predicted_route": f["predicted_route"], "chunk_search_will_find": f["chunk_search_will_find"], "bridge_needed": f.get("bridge_needed"),
            "answer_chunks": sorted(chunks[i]["position"] for i in answer_ids),
            "bridge_chunks": sorted(chunks[i]["position"] for i in bridge_ids),
            "rank_in_candidates": rank(answer_ids, order),           # 候補 20 件をリランクした順位。None は候補に入らず
            "in_top6": bool(answer_ids & set(top6)),
            "bridge_rank": rank(bridge_ids, order) if bridge_ids else None,
            "bridge_in_top6": bool(bridge_ids & set(top6)) if bridge_ids else None,
            "link_bridge_to_answer": link_from_bridge if bridge_ids else None,
            "link_top6_to_answer": link_from_top6,
            "added_by_reference_2": any(i in ref2 and ref2[i].get("via_reference") for i in answer_ids),
            "added_by_reference_10": any(i in ref10 and ref10[i].get("via_reference") for i in answer_ids),
            "answer_chunk_in_context_ref2": bool(answer_ids & set(ref2)),
            "bridge_in_context_ref2": bool(bridge_ids & set(ref2)) if bridge_ids else None,
            "graph_has_fact": bool(fact_entities),
            "graph_expanded": bool(fact_entities & expanded),
            "graph_in_context_lines": any(entity_name[i] and entity_name[i] in lines for i in fact_entities),
            "answers": {name: any(k in norm(t.get(qid, "")) for k in short) for name, t in traces.items()},
        }
        out.append(rec)
        print(f"{qid}.{f['fact_no']} 予想={f['predicted_route']:<20s} 候補内順位={rec['rank_in_candidates']} 上位6={rec['in_top6']} "
              f"つなぎ上位6={rec['bridge_in_top6']} リンク(つなぎ→答え)={rec['link_bridge_to_answer']} 参照で追加={rec['added_by_reference_2']}/{rec['added_by_reference_10']} "
              f"グラフ(有/展開/文脈)={rec['graph_has_fact']}/{rec['graph_expanded']}/{rec['graph_in_context_lines']} 回答={list(rec['answers'].values())}", flush=True)
    json.dump({"facts": out}, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
