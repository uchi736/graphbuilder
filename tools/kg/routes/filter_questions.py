"""経路の型ごとに作った問いの候補を、条件に照らして機械的に判定する。

使い方: python3 filter_questions.py <候補の JSON> <出力の JSON>
前提: graph_dump.json、バックエンド（:8440、/retrieve_context）、埋め込みとリランカーが稼働中。LLM は呼ばない。

判定する条件:
  1. 起点は引ける … 質問だけの検索（上位 6 件、参照なし）に、起点のチャンクが入る
  2. 答えは直接は引けない … 質問で全チャンクを並べ替えたときの、答えのチャンクの順位。6 位以内は「直接引ける」、
     7〜20 位は「件数を増やせば届く」、21 位以下を「直接は引けない」とする（順位は 50 位まで数える）。上位 6 件に答えの語を含む別のチャンクがある場合も「直接引ける」
  3. 起点から答えへ道がある … 起点のチャンクに、つなぎの記述（またはエンティティの名前）が実際に書かれている。答えのチャンクに答えの語がある
参考（条件ではない）: いまのグラフに、起点から答えへの参照リンクがあるか。共有のエンティティがグラフで両方のチャンクにつながっているか
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
BACKEND = "http://localhost:8440"
CRED = {"uri": "neo4j://database2:7687", "userName": "neo4j", "password": "kgb-please-change", "database": "neo4j", "email": "test@example.com"}
RANK_DEPTH = 50
norm = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")))


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


def entity_names(text: str) -> list:
    """つなぎのエンティティの名前。括弧の注記を外す。2 回たどる場合は「→」で区切ってある"""
    names = []
    for part in re.split(r"→|->", unicodedata.normalize("NFKC", text or "")):
        name = norm(re.sub(r"\(.*?\)", "", part))
        if name:
            names.append(name)
    return names


def graph_lines(context: str) -> str:
    """文脈のうち、エンティティとリレーションの行だけ（チャンクの本文を除く）"""
    return "\n".join(re.findall(r"(?:Entities:|Relationships:)(.*?)(?=Document start|Text Content:|\Z)", context or "", flags=re.S))


def contains(quote: str, text: str, share: float = 0.6) -> bool:
    q = norm(quote)
    if not q:
        return False
    if q in text:
        return True
    m = SequenceMatcher(None, q, text, autojunk=False).find_longest_match(0, len(q), 0, len(text))
    return m.size >= max(8, int(len(q) * share))


def main():
    raw = json.load(open(sys.argv[1], encoding="utf-8"))
    candidates = raw["questions"] if isinstance(raw, dict) else raw
    dump = json.load(open(HERE / "graph_dump.json", encoding="utf-8"))
    chunks = {c["id"]: dict(c, code=doc_code(c["fileName"]), n=norm(c["text"])) for c in dump["chunks"]}
    total = len(chunks)
    refs = {}
    for r in dump["refers_to"]:
        refs.setdefault(r["source"], set()).add(r["target"])
    chunk_entities = {}
    for h in dump["has_entity"]:
        chunk_entities.setdefault(h["chunk"], set()).add(h["entity"])
    entity_text = {e["id"]: norm(json.dumps(e["props"], ensure_ascii=False)) for e in dump["entities"]}

    out = []
    for n, c in enumerate(candidates, start=1):
        keys = [norm(k) for k in c["answer_keys"] if norm(k)]
        acode, ecode = doc_code(c["answer_doc"]), doc_code(c["entry_doc"])
        answer_ids = {i for i, ch in chunks.items() if ch["code"] == acode and (contains(c["answer_quote"], ch["n"]) or any(k in ch["n"] for k in keys))}
        answer_ids_strict = {i for i in answer_ids if any(k in chunks[i]["n"] for k in keys)} or answer_ids
        entry_ids = {i for i, ch in chunks.items() if ch["code"] == ecode and contains(c["entry_quote"], ch["n"])}
        entry_by = "原文"
        if not entry_ids:
            # 原文が表のセルの境で割れていて見つからない場合は、候補に書かれたチャンクの番号を使う
            declared = set(c.get("entry_chunk_positions") or [])
            entry_ids = {i for i, ch in chunks.items() if ch["code"] == ecode and ch["position"] in declared}
            entry_by = "番号"
        names = entity_names(c.get("bridge_entity", ""))
        bridge_ok = None
        if c["bridge_kind"] == "reference":
            bridge_ok = any(contains(c["bridge_quote"], chunks[i]["n"]) for i in entry_ids)
        elif c["bridge_kind"] == "entity":
            # 起点のチャンクに最初の名前があり、答えのチャンクに最後の名前がある
            bridge_ok = bool(names) and any(names[0] in chunks[i]["n"] for i in entry_ids) and any(names[-1] in chunks[i]["n"] for i in answer_ids_strict)

        # 選ぶ件数の上限は 50。50 位までに無ければ、順位は None（51 位以下）として扱う
        full = retrieve(c["question"], {"candidate_k": 200, "rerank_top_n": RANK_DEPTH, "candidate_min_score": 0.0, "reference_max": 0})
        order = [x["id"] for x in full["chunks"]]
        top6 = [x["id"] for x in retrieve(c["question"], {"reference_max": 0})["chunks"]]
        top20 = [x["id"] for x in retrieve(c["question"], {"rerank_top_n": 20, "candidate_k": 20, "reference_max": 0})["chunks"]]   # 既定の候補 20 件をすべて選ぶ
        followed = {}
        for label, limit in (("ref2", 2), ("ref10", 10)):
            r = retrieve(c["question"], {"reference_max": limit})
            followed[label] = {"chunks": [x["id"] for x in r["chunks"]], "via": [x["id"] for x in r["chunks"] if x.get("via_reference")], "lines": norm(graph_lines(r["context"]))}
        rank = lambda ids: min((order.index(i) + 1 for i in ids if i in order), default=None)
        answer_rank, entry_rank = rank(answer_ids_strict), rank(entry_ids)
        leak = [f"{chunks[i]['code']}#{chunks[i]['position']}" for i in top6 if i not in answer_ids_strict and any(k in chunks[i]["n"] for k in keys)]
        if not answer_ids_strict:
            direct = "答えのチャンクが見つからない"
        elif (answer_rank is not None and answer_rank <= 6) or leak:
            direct = "直接引ける"
        elif answer_rank is not None and answer_rank <= 20:
            direct = "件数を増やせば届く"
        else:
            direct = "直接は引けない"
        seed_ok = bool(entry_ids & set(top6))
        # 参考: いまのグラフの上での道
        link = any(answer_ids_strict & refs.get(i, set()) for i in entry_ids)
        of_entry = set().union(*[chunk_entities.get(i, set()) for i in entry_ids]) if entry_ids else set()
        of_answer = set().union(*[chunk_entities.get(i, set()) for i in answer_ids_strict]) if answer_ids_strict else set()
        shared_entities = of_entry & of_answer
        named = lambda ids, name: {i for i in ids if name and name in entity_text.get(i, "")}
        # つなぎのエンティティが、グラフの上で起点と答えの両方のチャンクにつながっているか
        bridge_in_graph = bool(names) and bool(named(of_entry, names[0])) and bool(named(of_answer, names[-1])) and (len(names) > 1 or bool(named(shared_entities, names[0])))
        near = lambda ids: {j for j, ch in chunks.items() for i in ids if ch["fileName"] == chunks[i]["fileName"] and abs(ch["position"] - chunks[i]["position"]) <= 1}
        link_near = any(near(answer_ids_strict) & refs.get(i, set()) for i in entry_ids)
        verdict = "採用" if (c["type"] == "control" and direct == "直接引ける") or (c["type"] != "control" and seed_ok and direct == "直接は引けない" and bridge_ok) else "不採用"
        if c["type"] != "control" and seed_ok and direct == "件数を増やせば届く" and bridge_ok:
            verdict = "境目"
        rec = {
            "no": n, "type": c["type"], "question": c["question"], "answer": c["answer"], "answer_keys": c["answer_keys"],
            "entry": f"{ecode} {c['entry_where']}", "answer_at": f"{acode} {c['answer_where']}", "bridge_kind": c["bridge_kind"], "bridge_entity": c.get("bridge_entity", ""),
            "entry_chunks": sorted(f"{chunks[i]['code']}#{chunks[i]['position']}" for i in entry_ids),
            "answer_chunks": sorted(f"{chunks[i]['code']}#{chunks[i]['position']}" for i in answer_ids_strict),
            "seed_in_top6": seed_ok, "entry_rank": entry_rank, "answer_rank_of_all": answer_rank, "chunks_total": total,
            "answer_leak_in_top6": leak, "direct": direct, "bridge_in_entry_chunk": bridge_ok,
            "graph_reference_link": link, "graph_reference_link_to_neighbor": link_near, "graph_shared_entities": len(shared_entities),
            "graph_bridge_entity": bridge_in_graph if c["bridge_kind"] == "entity" else None, "verdict": verdict,
            "src": c.get("_src", ""), "entry_matched_by": entry_by, "entry_in_candidates20": bool(entry_ids & set(top20)),
            "top6": [f"{chunks[i]['code']}#{chunks[i]['position']}" for i in top6 if i in chunks],
            "answer_in_top6": bool(answer_ids_strict & set(top6)), "answer_in_top20": bool(answer_ids_strict & set(top20)),
            "answer_in_ref2": bool(answer_ids_strict & set(followed["ref2"]["chunks"])), "answer_in_ref10": bool(answer_ids_strict & set(followed["ref10"]["chunks"])),
            "answer_via_ref2": bool(answer_ids_strict & set(followed["ref2"]["via"])), "answer_via_ref10": bool(answer_ids_strict & set(followed["ref10"]["via"])),
            "keys_in_graph_lines": all(k in followed["ref2"]["lines"] for k in keys) if keys else False,
        }
        out.append(rec)
        print(f"{n:2d} [{c['type']:9s}] {verdict:4s} 起点の順位={entry_rank} 答えの順位={answer_rank}/{total} {direct} つなぎ={bridge_ok} "
              f"候補20に起点={rec['entry_in_candidates20']} 答え(上位6/20/参照2/参照10)={rec['answer_in_top6']:d}{rec['answer_in_top20']:d}{rec['answer_in_ref2']:d}{rec['answer_in_ref10']:d} グラフの行に答え={rec['keys_in_graph_lines']:d} "
              f"(参考: リンク={link}/{link_near} つなぎのエンティティ={rec['graph_bridge_entity']}) | {c['question'][:30]}", flush=True)
    json.dump({"questions": out}, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    import collections
    print(collections.Counter((r["type"], r["verdict"]) for r in out))


if __name__ == "__main__":
    main()
