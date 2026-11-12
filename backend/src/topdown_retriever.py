# -*- coding: utf-8 -*-
"""上からたどる検索（案 C）の 1 段目: 文書を選ぶ。

  (1) いつもの混成検索（ベクトル索引 vector ＋ 全文索引 keyword）でチャンクの候補を取る
  (2) リランカーで上位だけ残す
  (3) そのチャンクの文書 ＋ その文書が参照している文書（文書どうしの REFERS_TO → Standard → IS_VERSION_OF の逆 → 現行版）を候補にする
  (4) 文書カード（文書番号・名称・適用先・「1. 適用」・見出し）と質問の近さをリランカーで測り、
      文書ごとの点数 = max(チャンクの点数, カードの点数, 参照の一文の点数 × 参照元の点数) で絞る
各段の数を全部返すので、どの信号で入ったかが後から分かる。2 段目（節を選ぶ）はこの結果を受け取る。
使い方:
    from src.topdown_retriever import select_documents
    out = select_documents(graph, embeddings, query)         # embeddings: embed_query を持つ埋め込み（load_embedding_model の戻り）
    out["kept"]  -> [{"fileName", "score", "via", ...}]        # 絞った後の文書
    out["candidates"] -> 候補の全文書と各信号の点数
"""
import logging
from typing import Callable, Dict, List, Optional

from src.shared.common_fn import execute_graph_query, get_value_from_env

DEFAULTS = {
    "candidate_k": 40,        # 混成検索で取るチャンクの数（ベクトル・全文それぞれ）
    "rerank_top": 10,         # リランカーで残すチャンクの数
    "doc_keep": 2,            # 絞った後に残す文書の数（R2）
    "keep_ratio": 0.6,        # 2 本目以降は 1 本目の点数 × この比率以上のときだけ残す
    "min_score": 0.05,        # 候補に入れる最低の点数（文書）
    "follow_references": True,
    "card_weight": 1.0,
    "ref_weight": 1.0,
}


def _settings(overrides: Optional[dict]) -> dict:
    s = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k in s and v is not None:
            s[k] = type(s[k])(v)
    return s


def _rerank(query: str, texts: List[str]) -> List[float]:
    from src.rerank_retriever import _rerank as rr
    return rr(query, texts) if texts else []


def _chunk_candidates(graph, vector: List[float], query: str, k: int, document_names: Optional[List[str]] = None) -> Dict[str, dict]:
    rows = execute_graph_query(graph, """
        CALL db.index.vector.queryNodes('vector', $k, $vec) YIELD node AS c, score
        MATCH (c)-[:PART_OF]->(d:Document)
        WHERE $names IS NULL OR d.fileName IN $names
        RETURN c.id AS id, c.text AS text, d.fileName AS fileName, c.position AS position, score AS vscore""",
        params={"k": int(k), "vec": vector, "names": document_names})
    out = {r["id"]: dict(r, fscore=0.0) for r in rows}
    try:
        ft = execute_graph_query(graph, """
            CALL db.index.fulltext.queryNodes('keyword', $q, {limit: $k}) YIELD node AS c, score
            MATCH (c)-[:PART_OF]->(d:Document)
            WHERE $names IS NULL OR d.fileName IN $names
            RETURN c.id AS id, c.text AS text, d.fileName AS fileName, c.position AS position, score AS fscore""",
            params={"k": int(k), "q": _fulltext_query(query), "names": document_names})
        for r in ft:
            if r["id"] in out:
                out[r["id"]]["fscore"] = r["fscore"]
            else:
                out[r["id"]] = dict(r, vscore=0.0)
    except Exception as e:  # 全文索引が無い場合はベクトルだけ
        logging.info("fulltext index unavailable: %s", e)
    return out


def _fulltext_query(query: str) -> str:
    # Lucene の記号を落とし、語の OR にする
    import re
    words = [w for w in re.split(r"[\s、。，．,.？?！!（）()「」『』:：;；/／]+", query) if len(w) >= 2]
    return " OR ".join(re.sub(r'[+\-&|!(){}\[\]^"~*?:\\/]', " ", w).strip() for w in words[:20]) or query


def select_documents(graph, embeddings, query: str, settings: Optional[dict] = None, document_names: Optional[List[str]] = None) -> dict:
    s = _settings(settings)
    vector = embeddings.embed_query(query)
    cands = _chunk_candidates(graph, vector, query, s["candidate_k"], document_names)
    ids = list(cands)
    scores = _rerank(query, [cands[i]["text"] for i in ids])
    for i, sc in zip(ids, scores):
        cands[i]["rscore"] = sc
    top_chunks = sorted(cands.values(), key=lambda c: -c.get("rscore", 0.0))[: s["rerank_top"]]

    docs: Dict[str, dict] = {}
    def D(f):
        return docs.setdefault(f, {"fileName": f, "chunk_score": 0.0, "chunks": 0, "card_score": 0.0, "ref_score": 0.0, "ref_from": None, "via": set()})
    for c in top_chunks:
        d = D(c["fileName"])
        d["chunk_score"] = max(d["chunk_score"], c.get("rscore", 0.0))
        d["chunks"] += 1
        d["via"].add("chunk")

    # (3) 参照先の文書（現行版）
    if s["follow_references"] and docs:
        rows = execute_graph_query(graph, """
            UNWIND $names AS f
            MATCH (d:Document {fileName: f})-[r:REFERS_TO]->(st:Standard)<-[:IS_VERSION_OF]-(t:Document)
            WHERE t.fileName <> f AND coalesce(r.scope, '') <> 'external'
              AND (NOT EXISTS { MATCH (st)-[:HAS_CURRENT_VERSION]->(:Document) } OR EXISTS { MATCH (st)-[:HAS_CURRENT_VERSION]->(t) })
            RETURN f AS src, t.fileName AS tgt, st.id AS number, r.quotes AS quotes, r.chunks AS chunks""",
            params={"names": list(docs)})
        quote_texts, quote_keys = [], []
        for r in rows:
            for q in (r["quotes"] or [])[:3]:
                quote_texts.append(q)
                quote_keys.append((r["src"], r["tgt"], r["number"]))
        qscores = _rerank(query, quote_texts)
        for (src, tgt, number), q, sc in zip(quote_keys, quote_texts, qscores):
            link = sc * docs[src]["chunk_score"] * s["ref_weight"]
            d = D(tgt)
            if link > d["ref_score"]:
                d["ref_score"] = link
                d["ref_from"] = {"src": src, "number": number, "quote": q[:80], "quote_score": sc}
            d["via"].add("reference")

    # (4) 文書カード（全文書。カードだけで入る文書もある）
    cards = execute_graph_query(graph, "MATCH (d:Document) WHERE d.card IS NOT NULL AND ($names IS NULL OR d.fileName IN $names) RETURN d.fileName AS f, d.card AS card",
                                params={"names": document_names})
    cscores = _rerank(query, [c["card"] for c in cards])
    for c, sc in zip(cards, cscores):
        d = D(c["f"])
        d["card_score"] = sc * s["card_weight"]
        if sc >= s["min_score"]:
            d["via"].add("card")

    for d in docs.values():
        d["score"] = max(d["chunk_score"], d["card_score"], d["ref_score"])
        d["via"] = sorted(d["via"])
    ranked = sorted(docs.values(), key=lambda d: -d["score"])
    ranked = [d for d in ranked if d["score"] >= s["min_score"]]
    kept = []
    for d in ranked:
        if len(kept) >= s["doc_keep"]:
            break
        if kept and d["score"] < kept[0]["score"] * s["keep_ratio"]:
            break
        kept.append(d)
    return {"query": query, "settings": s, "top_chunks": [{k: v for k, v in c.items() if k != "text"} for c in top_chunks],
            "candidates": ranked, "kept": kept}
