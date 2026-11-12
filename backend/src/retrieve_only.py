"""検索だけの口（POST /retrieve_context）の本体。

/chat_bot（QA_integration.QA_RAG）と同じ検索を、LLM を呼ばず、履歴（Session / Message）を読み書きせずに通す。

    検索設定の解決 → retriever の組み立て → 文書の取得 → 文脈の整形

各段は QA_integration と rerank_retriever の関数をそのまま呼ぶ。ここで足すのは次の 2 点だけである。
  - 参照リンク（REFERS_TO）をたどって足されたチャンクの控えを読む（retriever が文書行の metadata.references に残したもの。
    1 件 = 足したチャンク、参照元のチャンク、参照の一文、点数、何回目のたどりか）
  - 選ばれたチャンクの fileName / position / page_number / section を Neo4j から読む

質問の言い換え（履歴を使った書き直し）は行わない。質問はそのまま検索に使う。
"""
import json
import logging
import time

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda

from src.QA_integration import (
    create_document_retriever_chain, format_documents, get_chat_mode_settings, get_neo4j_retriever, get_sources_and_chunks,
)
from src.rerank_retriever import collect_reference_trace, resolve_retrieval_settings
from src.shared.constants import (
    CHAT_DEFAULT_MODE, CHAT_ENTITY_VECTOR_MODE, CHAT_GLOBAL_VECTOR_FULLTEXT_MODE, CHAT_GRAPH_MODE,
)

# 選ばれたチャンクの詳細。source は取り出しの照会（metadata.source）と同じ式で、文書行との突き合わせに使う。
CHUNK_DETAIL_QUERY = """
UNWIND $ids AS id
MATCH (c:Chunk {id: id})
OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
WITH c, collect({fileName: d.fileName,
                 source: COALESCE(CASE WHEN d.url CONTAINS "None" THEN d.fileName ELSE d.url END, d.fileName)}) AS docs
RETURN c.id AS id, c.text AS text, c.fileName AS fileName, c.position AS position,
       c.page_number AS page_number, c.section AS section, docs
"""

# format_documents は、モデル名を「文脈に入れる文書行の上限（CHAT_TOKEN_CUT_OFF）」を決めるためだけに使う。
# 省略時は、どの表にも当たらない名前を渡して既定の上限にする（None は表の照合で例外になり、空文字は誤って当たる）。
_NO_MODEL = "(none)"


def _llm_must_not_run(_):
    raise RuntimeError("retrieve_context must not call the LLM")


# create_document_retriever_chain は、履歴があるときに質問を書き直す枝のために LLM を受け取る。
# 検索だけの口は常にメッセージ 1 件で呼ぶのでその枝は通らない。通ったら誤りなので例外にする。
_NO_LLM = RunnableLambda(_llm_must_not_run)


def _parse_retrieval_settings(retrieval_settings):
    """QA_RAG と同じ扱い: JSON の文字列か辞書を受け、読めなければ既定値にする。"""
    overrides = retrieval_settings
    if isinstance(overrides, str):
        try:
            overrides = json.loads(overrides) if overrides.strip() else None
        except json.JSONDecodeError:
            logging.warning(f"retrieval_settings is not valid JSON; using defaults: {overrides[:200]}")
            overrides = None
    return overrides if isinstance(overrides, dict) else None


def _parse_document_names(document_names):
    """JSON の配列の文字列（/chat_bot と同じ）か配列を受ける。未指定は空の配列にする。"""
    if document_names is None:
        return []
    if isinstance(document_names, str):
        document_names = json.loads(document_names) if document_names.strip() else []
    if not isinstance(document_names, list):
        raise ValueError("document_names must be a JSON array of strings")
    return [str(name).strip() for name in document_names]


def _chunk_rows(graph, chunkdetails, docs, references):
    """chunkdetails（id と点数）に、Neo4j のチャンクのプロパティと via_reference を付ける。

    チャンクの項目は従来のままにする（何回目のたどりか、どの一文からたどったかは、応答の references に載せる）。
    """
    scores = {}
    for detail in chunkdetails:
        scores.setdefault(detail.get("id"), detail.get("score", 0))
    if not scores:
        return []
    # チャンクがどの文書行に入っていたか（同じ本文が複数の文書にある場合に fileName を取り違えないため）
    row_source = {}
    for doc in docs:
        for detail in doc.metadata.get("chunkdetails") or []:
            row_source.setdefault(detail.get("id"), doc.metadata.get("source"))
    # 参照元の直後に示した参照先は、参照元の文書行に載っている。fileName は、足したチャンク自身の文書で引く
    for entry in references:
        if entry.get("select") == "link" and entry.get("source"):
            row_source[entry.get("chunk_id")] = entry["source"]
    details = {row["id"]: row for row in graph.query(CHUNK_DETAIL_QUERY, params={"ids": list(scores)})}
    followed = {entry.get("chunk_id") for entry in references}
    chunks = []
    for chunk_id, score in scores.items():
        row = details.get(chunk_id) or {}
        file_name = next((d.get("fileName") for d in row.get("docs") or []
                          if d.get("fileName") and d.get("source") == row_source.get(chunk_id)), None)
        chunks.append({
            "id": chunk_id,
            "text": row.get("text") or "",
            "fileName": file_name or row.get("fileName"),
            "position": row.get("position"),
            "page_number": row.get("page_number"),
            "section": row.get("section"),
            "score": score,
            "via_reference": chunk_id in followed,
        })
    chunks.sort(key=lambda c: (-(c["score"] or 0), str(c["fileName"]), c["position"] or 0))
    return chunks


def retrieve_context(graph, question, document_names=None, mode=None, embedding_provider=None, embedding_model=None,
                     retrieval_settings=None, model=None):
    """/chat_bot と同じ検索を行い、チャンク・エンティティとリレーションの ID・LLM に渡すのと同じ文脈を返す。

    model は LLM を呼ぶためではなく、文脈に入れる文書行の上限を /chat_bot と同じにするためだけに使う（省略可）。
    """
    start_time = time.time()
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question is required")
    mode = (mode or "").strip() or CHAT_DEFAULT_MODE
    if mode == CHAT_GRAPH_MODE:
        raise ValueError(f"mode '{CHAT_GRAPH_MODE}' builds a Cypher query with the LLM and has no retrieval-only form")

    # 共有の設定辞書を書き換えないよう写しを作り、検索設定を載せる（QA_RAG と同じ）
    chat_mode_settings = dict(get_chat_mode_settings(mode=mode))
    chat_mode_settings["retrieval_settings"] = resolve_retrieval_settings(_parse_retrieval_settings(retrieval_settings),
                                                                         mode_defaults=chat_mode_settings.get("retrieval_defaults"))
    document_names = _parse_document_names(document_names)
    if document_names and not chat_mode_settings["document_filter"]:
        # /chat_bot は同じ条件で検索せずに案内文を返す。検索だけの口では失敗として返す
        raise ValueError(f"mode '{mode}' does not support filtering by document; send an empty document_names")

    retriever = get_neo4j_retriever(
        graph=graph, document_names=document_names, chat_mode_settings=chat_mode_settings,
        embedding_provider=embedding_provider, embedding_model=embedding_model)
    doc_retriever = create_document_retriever_chain(_NO_LLM, retriever, embedding_provider, embedding_model)
    # メッセージ 1 件で呼ぶと、質問はそのまま retriever に渡る（書き直しの枝は通らない）
    docs = doc_retriever.invoke({"messages": [HumanMessage(content=question)]})

    context, chunks, entity_ids, relationship_ids, references = "", [], [], [], []
    if docs:
        cutoff_model = model.strip() if isinstance(model, str) and model.strip() else _NO_MODEL
        context, sources, entitydetails, _communities = format_documents(docs, cutoff_model, chat_mode_settings)
        # モードごとの振り分けは process_documents と同じ（チャンクを索引にするモードだけがチャンクの一覧を持つ）
        if mode == CHAT_ENTITY_VECTOR_MODE:
            entity_ids = entitydetails.get("entityids", [])
        elif mode != CHAT_GLOBAL_VECTOR_FULLTEXT_MODE:
            chunkdetails = get_sources_and_chunks(sources, docs)["chunkdetails"]
            # 足した参照先の控え（文脈に入れた文書行の分）。根拠を表示する側が、どの本文のどの一文からたどったかを示すのに使う
            references = collect_reference_trace(docs, sources)
            chunks = _chunk_rows(graph, chunkdetails, docs, references)
            entity_ids = entitydetails.get("entityids", [])
            relationship_ids = entitydetails.get("relationshipids", [])

    logging.info(f"retrieve_context: mode={mode}, chunks={len(chunks)}, entities={len(entity_ids)}, "
                 f"relationships={len(relationship_ids)}, context_chars={len(context)}, {time.time() - start_time:.2f} seconds")
    return {
        "question": question,
        "mode": chat_mode_settings["mode"],
        "retrieval_settings": chat_mode_settings["retrieval_settings"],
        "chunks": chunks,
        "entityids": sorted(entity_ids),
        "relationshipids": sorted(relationship_ids),
        "references": references,
        "context": context,
    }
