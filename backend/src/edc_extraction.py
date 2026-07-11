"""EDC (Extract-Define-Canonicalize) API 連携アダプタ。

モデルに "edc" を選ぶと、LLMGraphTransformer の代わりに別プロセスで動く
EDC API (POST /extract) へチャンクテキストを送り、返ってきたトリプルを
langchain の GraphDocument に変換して既存の保存パイプラインへ流す。
EDC 本体には手を入れず、HTTP 経由でのみ連携する。

環境変数 LLM_MODEL_CONFIG_EDC (カンマ区切り、URL 以外は省略可):
    <base_url>[,<doctype>[,<chunk_method>[,<concurrency>]]]
    例: http://host.docker.internal:8080,auto,recursive,2
      - doctype: "auto"(既定) はファイル先頭サンプルで1回だけ /classify し、
                 結果の文書タイプを全チャンクに適用する（チャンクごとの判定ブレ防止）。
                 "none" で文書タイプ判定なし（スキーマフリー発見）。タイプ名の直接指定も可。
      - chunk_method: EDC 側の再分割方式 (既定 recursive)。
      - concurrency: EDC への同時リクエスト数 (既定 2)。
    タイムアウト秒は EDC_EXTRACTION_TIMEOUT で指定 (既定 900)。
"""

import asyncio
import logging
import re

import httpx
from langchain_community.graphs.graph_document import GraphDocument, Node, Relationship

from src.shared.common_fn import get_value_from_env
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException

DEFAULT_DOCTYPE = "auto"
DEFAULT_CHUNK_METHOD = "recursive"
DEFAULT_CONCURRENCY = 2
DEFAULT_TIMEOUT_SECONDS = 900
CLASSIFY_SAMPLE_CHARS = 3000
DEFAULT_NODE_TYPE = "Entity"


def _get_edc_config():
    env_value = get_value_from_env("LLM_MODEL_CONFIG_EDC")
    if not env_value:
        raise LLMGraphBuilderException(
            "Environment variable 'LLM_MODEL_CONFIG_EDC' is not defined "
            "(expected: <edc_api_base_url>[,<doctype>[,<chunk_method>[,<concurrency>]]])"
        )
    parts = [p.strip() for p in env_value.split(",")]
    base_url = parts[0].rstrip("/")
    doctype = parts[1] if len(parts) > 1 and parts[1] else DEFAULT_DOCTYPE
    chunk_method = parts[2] if len(parts) > 2 and parts[2] else DEFAULT_CHUNK_METHOD
    try:
        concurrency = int(parts[3]) if len(parts) > 3 and parts[3] else DEFAULT_CONCURRENCY
    except ValueError:
        concurrency = DEFAULT_CONCURRENCY
    if doctype.lower() == "none":
        doctype = None
    return base_url, doctype, chunk_method, max(1, concurrency)


def _normalize_rel_type(relation: str) -> str:
    return re.sub(r"\s+", "_", relation.strip())


def _triplets_to_graph_document(triplets, source_document) -> GraphDocument:
    """EDC のトリプル配列 ({subject, relation, object, subject_type?, object_type?}) を GraphDocument へ変換する。

    主語・関係・目的語のいずれかが欠落した不完全トリプルは、孤立ノードを作らないよう丸ごと除外する。
    """
    complete_triplets = []
    for triplet in triplets:
        subject = str(triplet.get("subject") or "").strip()
        obj = str(triplet.get("object") or "").strip()
        relation = _normalize_rel_type(str(triplet.get("relation") or ""))
        if subject and obj and relation:
            complete_triplets.append((triplet, subject, relation, obj))

    node_types = {}
    for triplet, subject, _, obj in complete_triplets:
        for entity, type_key in ((subject, "subject_type"), (obj, "object_type")):
            entity_type = str(triplet.get(type_key) or "").strip()
            # 同一エンティティが型付き/型なし両方で現れた場合は型付きを優先する
            if entity not in node_types or (entity_type and node_types[entity] == DEFAULT_NODE_TYPE):
                node_types[entity] = entity_type or DEFAULT_NODE_TYPE

    nodes = {entity: Node(id=entity, type=node_type) for entity, node_type in node_types.items()}

    relationships = []
    seen = set()
    for _, subject, relation, obj in complete_triplets:
        dedup_key = (subject, relation, obj)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        relationships.append(Relationship(source=nodes[subject], target=nodes[obj], type=relation))

    return GraphDocument(nodes=list(nodes.values()), relationships=relationships, source=source_document)


async def _classify_doctype(client, base_url, combined_chunk_document_list):
    """ファイル先頭サンプルで文書タイプを1回だけ判定し、全チャンクで使い回す。"""
    sample = "\n".join(doc.page_content for doc in combined_chunk_document_list[:3])[:CLASSIFY_SAMPLE_CHARS]
    try:
        response = await client.post(f"{base_url}/classify", json={"text": sample})
        response.raise_for_status()
        doctype = (response.json() or {}).get("type") or "UNKNOWN"
        logging.info(f"EDC classified document as doctype '{doctype}'")
        return doctype
    except Exception as e:
        logging.warning(f"EDC /classify failed ({e}); falling back to per-chunk doctype 'auto'")
        return "auto"


async def _extract_chunk(client, semaphore, base_url, doc, doctype, chunk_method):
    payload = {"text": doc.page_content, "doctype": doctype, "chunk_method": chunk_method}
    async with semaphore:
        response = await client.post(f"{base_url}/extract", json=payload)
        response.raise_for_status()
        triplets = (response.json() or {}).get("triplets") or []
    return _triplets_to_graph_document(triplets, doc)


async def get_graph_from_edc(combined_chunk_document_list):
    """結合済みチャンクのリストを EDC API で抽出し (GraphDocumentのリスト, token_usage) を返す。

    GraphDocument.source には入力チャンクの Document をそのまま入れる
    (metadata の combined_chunk_ids が下流の HAS_ENTITY リンク作成に使われる)。
    EDC はトークン使用量を返さないため token_usage は常に 0。
    """
    base_url, doctype, chunk_method, concurrency = _get_edc_config()
    timeout_seconds = get_value_from_env("EDC_EXTRACTION_TIMEOUT", DEFAULT_TIMEOUT_SECONDS, int)
    logging.info(
        f"EDC extraction: url={base_url}, doctype={doctype}, chunk_method={chunk_method}, "
        f"concurrency={concurrency}, timeout={timeout_seconds}s"
    )

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=10)) as client:
        try:
            health = await client.get(f"{base_url}/health", timeout=10)
            health.raise_for_status()
        except Exception as e:
            raise LLMGraphBuilderException(f"EDC API is not reachable at {base_url}: {e}")

        if doctype == "auto":
            doctype = await _classify_doctype(client, base_url, combined_chunk_document_list)

        semaphore = asyncio.Semaphore(concurrency)
        results = await asyncio.gather(
            *[
                _extract_chunk(client, semaphore, base_url, doc, doctype, chunk_method)
                for doc in combined_chunk_document_list
            ],
            return_exceptions=True,
        )

    graph_document_list = []
    failed_chunks = 0
    for result in results:
        if isinstance(result, BaseException):
            failed_chunks += 1
            logging.warning(f"Skipping chunk due to EDC extraction error: {result}")
        else:
            graph_document_list.append(result)
    if failed_chunks:
        logging.warning(f"EDC extraction skipped {failed_chunks}/{len(combined_chunk_document_list)} combined chunks")
    if combined_chunk_document_list and failed_chunks == len(combined_chunk_document_list):
        raise LLMGraphBuilderException(f"EDC extraction failed for all chunks: {results[0]}")

    total_relationships = sum(len(gd.relationships) for gd in graph_document_list)
    logging.info(
        f"EDC extraction produced {total_relationships} relationships across {len(graph_document_list)} graph documents"
    )
    return graph_document_list, 0
