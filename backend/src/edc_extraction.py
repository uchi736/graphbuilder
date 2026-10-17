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

語彙の扱い (EDC_ENRICH_SCHEMA, 既定 false):
    false なら EDC は文書タイプの語彙に写せないリレーションを足さずに落とし、応答の dropped に
    定義文付きで返す。アダプタはそれを EDC_PROPOSALS_DIR (既定 /code/edc_proposals) の
    proposals.jsonl に追記する。人が見て、必要なものだけ EDC の語彙 CSV に足す。
    true なら従来どおり語彙外の名前もそのまま足される (語彙が閉じない)。

語彙外のリレーションの畳み込み (EDC_FOLD_UNKNOWN_RELATIONS, 既定 false):
    enrich で残った語彙外のリレーション (LLM が付けた小文字の名前。語彙の名前は CSV どおり大文字) を、
    Neo4j の種類にはせず RELATED_TO に畳み、元の名前をプロパティ label に残す。種類の数が語彙+1 に収まり、
    検索の文字列化はリレーションのプロパティも出すので、元の名前は LLM に読ませられる。
    後で語彙に足すときは、label から種類へ張り替えるだけで済む (再抽出が要らない)。

    語彙の名前は、EDC の registry.json と語彙 CSV を読んで知る (EDC_SCHEMAS_DIR、既定 /edc_schemas に read-only で mount)。
    LLM が付けた名前が大文字小文字の違いだけで語彙の名前と同じなら (applies_to と APPLIES_TO)、畳まずに語彙の種類にする。
    語彙が読めない (mount が無い) ときは、大文字でない名前をすべて畳む。

文のような名前の除外 (EDC_DROP_SENTENCE_NAMES, 既定 false):
    主語か目的語が 15 字以上で読点か句点を含むトリプルを落とす。文をそのままノード名にした行き止まりを作らない。
"""

import asyncio
import json
import logging
import os
import re
import time

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
DEFAULT_PROPOSALS_DIR = "/code/edc_proposals"


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


FOLDED_REL_TYPE = "RELATED_TO"
DEFAULT_SCHEMAS_DIR = "/edc_schemas"
_vocabulary_cache = {}


def _read_csv_names(path):
    import csv
    with open(path, encoding="utf-8") as f:
        return {row[0].strip() for row in csv.reader(f) if row and row[0].strip()}


def _load_vocabulary(doctype):
    """文書の種類の語彙 (共通の骨組み ∪ 種類ごとの CSV) のリレーション名の集合。読めなければ None。"""
    if not doctype or doctype in ("auto", "none", "UNKNOWN"):
        return None
    if doctype in _vocabulary_cache:
        return _vocabulary_cache[doctype]
    vocabulary = None
    schemas_dir = get_value_from_env("EDC_SCHEMAS_DIR", DEFAULT_SCHEMAS_DIR)
    try:
        with open(os.path.join(schemas_dir, "registry.json"), encoding="utf-8") as f:
            registry = json.load(f)
        names = set(_read_csv_names(os.path.join(schemas_dir, os.path.basename(registry["backbone"]["schema"]))))
        entry = next((d for d in registry.get("doctypes", []) if d.get("name") == doctype), None)
        if entry is not None:
            names |= _read_csv_names(os.path.join(schemas_dir, os.path.basename(entry["schema"])))
            vocabulary = names
        else:
            logging.warning(f"EDC adapter: doctype '{doctype}' not in registry; folding every lowercase relation")
    except (OSError, KeyError, ValueError, StopIteration) as e:
        logging.warning(f"EDC adapter: could not read the vocabulary from {schemas_dir} ({e}); folding every lowercase relation")
    _vocabulary_cache[doctype] = vocabulary
    return vocabulary
SENTENCE_NAME_MIN_CHARS = 15
_SENTENCE_PUNCT = re.compile(r"[、。]")


def _flag_enabled(name: str, default: bool = False) -> bool:
    return str(get_value_from_env(name, "true" if default else "false")).strip().lower() in ("1", "true", "yes", "on")


def _is_vocabulary_relation(relation: str) -> bool:
    """語彙の名前は CSV どおり大文字 (APPLIES_TO)。LLM が付けた名前は小文字 (applies_to) で、enrich で残ったもの。"""
    return relation == relation.upper()


def _is_sentence_name(name: str) -> bool:
    return len(name) >= SENTENCE_NAME_MIN_CHARS and bool(_SENTENCE_PUNCT.search(name))


def _triplets_to_graph_document(triplets, source_document, vocabulary=None) -> GraphDocument:
    """EDC のトリプル配列 ({subject, relation, object, subject_type?, object_type?}) を GraphDocument へ変換する。

    主語・関係・目的語のいずれかが欠落した不完全トリプルは、孤立ノードを作らないよう丸ごと除外する。
    """
    fold = _flag_enabled("EDC_FOLD_UNKNOWN_RELATIONS")
    drop_sentences = _flag_enabled("EDC_DROP_SENTENCE_NAMES")
    complete_triplets = []
    n_dropped_sentence = n_folded = n_cased = 0
    for triplet in triplets:
        subject = str(triplet.get("subject") or "").strip()
        obj = str(triplet.get("object") or "").strip()
        relation = _normalize_rel_type(str(triplet.get("relation") or ""))
        if not (subject and obj and relation):
            continue
        if drop_sentences and (_is_sentence_name(subject) or _is_sentence_name(obj)):
            n_dropped_sentence += 1
            continue
        if fold and not _is_vocabulary_relation(relation):
            if vocabulary and relation.upper() in vocabulary:
                n_cased += 1
                complete_triplets.append((triplet, subject, relation.upper(), obj, None))
            else:
                n_folded += 1
                complete_triplets.append((triplet, subject, FOLDED_REL_TYPE, obj, relation))
        else:
            complete_triplets.append((triplet, subject, relation, obj, None))
    if n_dropped_sentence or n_folded or n_cased:
        logging.info(f"EDC adapter: folded {n_folded} out-of-vocabulary relations into {FOLDED_REL_TYPE}, "
                     f"uppercased {n_cased} to vocabulary names, dropped {n_dropped_sentence} sentence-like triplets")

    node_types = {}
    for triplet, subject, _, obj, _ in complete_triplets:
        for entity, type_key in ((subject, "subject_type"), (obj, "object_type")):
            entity_type = str(triplet.get(type_key) or "").strip()
            # 同一エンティティが型付き/型なし両方で現れた場合は型付きを優先する
            if entity not in node_types or (entity_type and node_types[entity] == DEFAULT_NODE_TYPE):
                node_types[entity] = entity_type or DEFAULT_NODE_TYPE

    nodes = {entity: Node(id=entity, type=node_type) for entity, node_type in node_types.items()}

    relationships = []
    seen = set()
    for _, subject, relation, obj, label in complete_triplets:
        dedup_key = (subject, relation, obj, label)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        properties = {"label": label} if label else {}
        relationships.append(Relationship(source=nodes[subject], target=nodes[obj], type=relation, properties=properties))

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


def _enrich_schema_enabled() -> bool:
    return str(get_value_from_env("EDC_ENRICH_SCHEMA", "false")).strip().lower() in ("1", "true", "yes", "on")


def _write_proposals(dropped, doc, doctype):
    """語彙に写せず落ちた抽出結果を、語彙を育てる候補として JSONL に追記する。"""
    if not dropped:
        return
    out_dir = get_value_from_env("EDC_PROPOSALS_DIR", DEFAULT_PROPOSALS_DIR)
    try:
        os.makedirs(out_dir, exist_ok=True)
        chunk_ids = (doc.metadata or {}).get("combined_chunk_ids")
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(os.path.join(out_dir, "proposals.jsonl"), "a", encoding="utf-8") as f:
            for rec in dropped:
                f.write(json.dumps({**rec, "doctype": doctype, "chunk_ids": chunk_ids, "at": stamp},
                                   ensure_ascii=False) + "\n")
    except OSError as e:
        logging.warning(f"Could not write EDC proposals to {out_dir}: {e}")


_SELF_REFERENCE = re.compile(r"(この基準|本基準|当基準|この規格|本規格|この要領|本要領)")
_BREADCRUMB_HEAD = re.compile(r"^\[([^\]>]+?)\s*(?:>|\])")
_DOC_NUMBER = re.compile(r"^(?:HS[-\s]?)?([A-Z]{2,4})-([A-Z]?\d{3})([A-Z])?$")


def _self_reference_name(text):
    """チャンク先頭の見出しの経路（[文書名 > 見出し …]）の文書名から、本文の「本基準」に当てる名前を作る。
    文書番号の形（HS-RU-002F など）なら末尾の改訂記号を外す。番号の形でなければ文書名をそのまま使う。"""
    m = _BREADCRUMB_HEAD.match(text or "")
    if not m:
        return None
    name = m.group(1).strip()
    n = _DOC_NUMBER.match(name.replace(" ", ""))
    if n:
        return f"HS-{n.group(1)}-{n.group(2)}"
    return name or None


def _resolve_self_reference(text):
    """本文の「本基準」「この基準」などを文書番号に置き換える。EDC はこれらを雑音の語として 3 つ組ごと捨てるため。
    チャンク先頭の見出しの経路の行は置き換えない。環境変数 EDC_RESOLVE_SELF_REFERENCE=false で止める。"""
    if not _flag_enabled("EDC_RESOLVE_SELF_REFERENCE", default=True):
        return text
    name = _self_reference_name(text)
    if not name:
        return text
    head, sep, body = text.partition("\n")
    if not head.startswith("["):
        head, sep, body = "", "", text
    return head + sep + _SELF_REFERENCE.sub(name, body)


async def _extract_chunk(client, semaphore, base_url, doc, doctype, chunk_method):
    payload = {"text": _resolve_self_reference(doc.page_content), "doctype": doctype, "chunk_method": chunk_method,
               "enrich_schema": _enrich_schema_enabled()}
    async with semaphore:
        response = await client.post(f"{base_url}/extract", json=payload)
        response.raise_for_status()
        body = response.json() or {}
    triplets = body.get("triplets") or []
    dropped = body.get("dropped") or []
    _write_proposals(dropped, doc, doctype)
    stats = {"truncated": int(body.get("n_truncated") or 0),
             "noise": int(body.get("n_noise_dropped") or 0),
             "dropped": len(dropped)}
    vocabulary = _load_vocabulary(doctype) if _flag_enabled("EDC_FOLD_UNKNOWN_RELATIONS") else None
    return _triplets_to_graph_document(triplets, doc, vocabulary), stats


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
        except Exception as e:
            raise LLMGraphBuilderException(f"EDC API is not reachable at {base_url}: {e}")
        if health.status_code != 200:
            # EDC の /health は LLM と埋め込みへの到達まで見る。落ちていれば抽出を始めない
            raise LLMGraphBuilderException(f"EDC API is not ready ({health.status_code}): {health.text[:300]}")

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
    totals = {"truncated": 0, "noise": 0, "dropped": 0}
    for result in results:
        if isinstance(result, BaseException):
            failed_chunks += 1
            logging.warning(f"Skipping chunk due to EDC extraction error: {result}")
        else:
            graph_document, stats = result
            graph_document_list.append(graph_document)
            for k in totals:
                totals[k] += stats.get(k, 0)
    if totals["truncated"]:
        logging.warning(f"EDC: {totals['truncated']} LLM calls hit the output limit; some triples were lost")
    logging.info(
        f"EDC: noise triples dropped={totals['noise']}, out-of-vocabulary triples dropped={totals['dropped']}"
        + (f" (written to {get_value_from_env('EDC_PROPOSALS_DIR', DEFAULT_PROPOSALS_DIR)}/proposals.jsonl)"
           if totals["dropped"] else "")
    )
    if failed_chunks:
        logging.warning(f"EDC extraction skipped {failed_chunks}/{len(combined_chunk_document_list)} combined chunks")
    if combined_chunk_document_list and failed_chunks == len(combined_chunk_document_list):
        raise LLMGraphBuilderException(f"EDC extraction failed for all chunks: {results[0]}")

    total_relationships = sum(len(gd.relationships) for gd in graph_document_list)
    logging.info(
        f"EDC extraction produced {total_relationships} relationships across {len(graph_document_list)} graph documents"
    )
    return graph_document_list, 0
