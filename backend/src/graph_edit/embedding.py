"""エンティティの埋め込みの作り直し（設計メモ 3 章の末尾）。

文は id + " " + coalesce(description, "")（post_processing.update_embeddings と同じ）。
埋め込みは環境変数の EMBEDDING_PROVIDER・EMBEDDING_MODEL で common_fn.load_embedding_model から作る
（vllm なら VLLM_EMBEDDING_ENDPOINT、この機体では常駐の ruri）。
トランザクションの前に計算し、中で db.create.setNodeVectorProperty で書く。次元が索引（entity_vector）と違うときと、
計算に失敗したときは書かず、embedding を消す（null）。空の埋め込みのエンティティは検索の起点の並びで最後になるだけで、検索は動く。
"""
import concurrent.futures
import logging

from src.shared.common_fn import get_value_from_env, load_embedding_model

ENTITY_VECTOR_INDEX = "entity_vector"
DEFAULT_TIMEOUT_SECONDS = 60
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="graph-edit-embed")


def entity_text(entity_id, description):
    """post_processing.fetch_entities_for_embedding の e.id + " " + coalesce(e.description, "") と同じ文。"""
    return f"{'' if entity_id is None else entity_id} {'' if description is None else description}"


def index_dimension(run):
    """entity_vector 索引の次元。無ければ None。run は (query, **params) -> result の関数（tx.run など）。"""
    record = run("SHOW VECTOR INDEXES YIELD name, options WHERE name = $name "
                 "RETURN options.indexConfig['vector.dimensions'] AS dimension", name=ENTITY_VECTOR_INDEX).single()
    if record is None or record["dimension"] is None:
        return None
    return int(record["dimension"])


def _embed(texts):
    provider = get_value_from_env("EMBEDDING_PROVIDER", "sentence-transformer")
    model = get_value_from_env("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    embeddings, dimension = load_embedding_model(provider, model)
    return [embeddings.embed_query(text) for text in texts], dimension


def embed_texts(texts):
    """文の一覧を埋め込む。{文: ベクトル}（失敗した文は入れない）と、モデルの次元を返す。"""
    unique = [t for t in dict.fromkeys(texts) if t is not None]
    if not unique:
        return {}, None
    timeout = get_value_from_env("GRAPH_EDIT_EMBEDDING_TIMEOUT", DEFAULT_TIMEOUT_SECONDS, "int")
    try:
        vectors, dimension = _executor.submit(_embed, unique).result(timeout=timeout)
    except Exception as e:
        logging.warning(f"graph_edit: could not create entity embeddings ({type(e).__name__}: {e}); they will be removed")
        return {}, None
    out = {}
    for text, vector in zip(unique, vectors):
        if isinstance(vector, (list, tuple)) and vector:
            out[text] = [float(x) for x in vector]
    return out, dimension


def vector_for(vectors, text, index_dim):
    """書いてよいベクトル（索引の次元と同じもの）か None。"""
    vector = vectors.get(text)
    if vector is None:
        return None
    if index_dim is not None and len(vector) != index_dim:
        logging.warning(f"graph_edit: embedding dimension {len(vector)} does not match the index ({index_dim}); not written")
        return None
    return vector


def write_embedding(tx, element_id, vector):
    """埋め込みを書く。vector が None なら embedding を消す。"""
    if vector is None:
        tx.run("MATCH (n) WHERE elementId(n) = $eid REMOVE n.embedding", eid=element_id)
    else:
        tx.run("MATCH (n) WHERE elementId(n) = $eid CALL db.create.setNodeVectorProperty(n, 'embedding', $vector)",
               eid=element_id, vector=vector)
