import logging
from langchain_neo4j import Neo4jGraph
from src.graph_query import get_graphDB_driver
from src.shared.common_fn import load_embedding_model,execute_graph_query,get_value_from_env
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from src.shared.constants import GRAPH_CLEANUP_PROMPT, GRAPH_EDIT_MANAGEMENT_LABELS, PROTECTED_LABELS_WHERE
from src.llm import get_llm
from src.graphDB_dataAccess import graphDBdataAccess
from src.graph_edit.lease import hold_lease
from src.graph_edit.ledger import rename_schema_types_in_ledger
from src.graph_edit.validate import RESERVED_LABELS, RESERVED_RELATIONSHIP_TYPES

# Constants for Full-Text Indexes
LABELS_QUERY = "CALL db.labels()"
# 修正台帳などの管理用ノード（GRAPH_EDIT_MANAGEMENT_LABELS）もエンティティの全文索引に入れない
FILTER_LABELS = ["Chunk","Document","__Community__","Evidence"] + GRAPH_EDIT_MANAGEMENT_LABELS
# 索引は存在しないときだけ作る（後処理のたびに消して作り直さない。中身は書き込みに合わせて Neo4j が更新する）
FULL_TEXT_QUERY = "CREATE FULLTEXT INDEX entities IF NOT EXISTS FOR (n{labels_str}) ON EACH [n.id, n.description];"
HYBRID_SEARCH_FULL_TEXT_QUERY = "CREATE FULLTEXT INDEX keyword IF NOT EXISTS FOR (n:Chunk) ON EACH [n.text]" 
COMMUNITY_INDEX_FULL_TEXT_QUERY = "CREATE FULLTEXT INDEX community_keyword IF NOT EXISTS FOR (n:`__Community__`) ON EACH [n.summary]" 

# Constants for Vector Indexes
CHUNK_VECTOR_INDEX_NAME = "vector"
ENTITY_VECTOR_INDEX_NAME = "entity_vector"
COMMUNITY_VECTOR_INDEX_NAME = "community_vector"
VECTOR_EMBEDDING_DEFAULT_DIMENSION = 384

CREATE_VECTOR_INDEX_QUERY = """
CREATE VECTOR INDEX {index_name} IF NOT EXISTS FOR (n:{node_label}) ON (n.{embedding_property})
OPTIONS {{
  indexConfig: {{
    `vector.dimensions`: {embedding_dimension},
    `vector.similarity_function`: 'cosine'
  }}
}}
"""

# Index Configurations
FULLTEXT_INDEXES = [
    {"type": "entities", "query": FULL_TEXT_QUERY},
    {"type": "hybrid", "query": HYBRID_SEARCH_FULL_TEXT_QUERY},
    {"type": "community", "query": COMMUNITY_INDEX_FULL_TEXT_QUERY}
]

VECTOR_INDEXES = [
    {"name": CHUNK_VECTOR_INDEX_NAME, "label": "Chunk", "property": "embedding"},
    {"name": ENTITY_VECTOR_INDEX_NAME, "label": "__Entity__", "property": "embedding"},
    {'name': COMMUNITY_VECTOR_INDEX_NAME, 'label': '__Community__', 'property': 'embedding'}
]

def create_vector_index(session, index_name, node_label, embedding_property, embedding_dimension):
    """Creates a vector index in the Neo4j database if it does not exist."""
    query = CREATE_VECTOR_INDEX_QUERY.format(
        index_name=index_name,
        node_label=node_label,
        embedding_property=embedding_property,
        embedding_dimension=embedding_dimension
    )
    session.run(query)
    # 既にある索引は作り直さない。次元が違うときの作り直しは /drop_create_vector_index で行う
    record = session.run("SHOW VECTOR INDEXES YIELD name, options WHERE name = $name "
                         "RETURN options.indexConfig['vector.dimensions'] AS dimension", name=index_name).single()
    if record and record["dimension"] is not None and record["dimension"] != embedding_dimension:
        logging.warning(f"Vector index '{index_name}' has dimension {record['dimension']}, but the embedding model has {embedding_dimension}. "
                        "Use /drop_create_vector_index to re-create it.")
    else:
        logging.info(f"Vector index '{index_name}' is ready.")

def create_fulltext_index(session, index_type, query):
    """Creates a full-text index in the Neo4j database if it does not exist."""
    # IF NOT EXISTS は同じ名前の索引があれば何もしない。entities の型ラベルの一覧は作成した時点のものだが、
    # __Entity__ を含むので、後から増えた型のエンティティも索引に入る（作り直さない）
    if index_type == "entities":
        result = session.run(LABELS_QUERY)
        labels = [record["label"] for record in result if record["label"] not in FILTER_LABELS]
        if labels:
            labels_str = ":" + "|".join([f"`{label}`" for label in labels])
            query = query.format(labels_str=labels_str)
        else:
            logging.info("Full-text index for entities not created as no labels were found.")
            return
            
    session.run(query)
    logging.info(f"Full-text index for '{index_type}' is ready.")


def create_vector_fulltext_indexes(credentials, embedding_provider, embedding_model):
    """Creates all configured full-text and vector indexes."""
    logging.info("Starting the process of creating full-text and vector indexes.")
    
    _, dimension = load_embedding_model(embedding_provider, embedding_model)
    if not dimension:
        dimension = VECTOR_EMBEDDING_DEFAULT_DIMENSION

    try:
        driver = get_graphDB_driver(credentials)
        driver.verify_connectivity()
        logging.info("Database connectivity verified.")

        with driver.session() as session:
            # Create Full-Text Indexes
            for index_config in FULLTEXT_INDEXES:
                try:
                    create_fulltext_index(session, index_config["type"], index_config["query"])
                except Exception as e:
                    logging.error(f"Failed to create full-text index for type '{index_config['type']}': {e}")

            # Create Vector Indexes
            for index_config in VECTOR_INDEXES:
                try:
                    create_vector_index(session, index_config["name"], index_config["label"], index_config["property"], dimension)
                except Exception as e:
                    logging.error(f"Failed to create vector index '{index_config['name']}': {e}")

    except Exception as e:
        logging.error(f"An error occurred during the index creation process: {e}", exc_info=True)
    finally:
        if 'driver' in locals() and driver:
            driver.close()
            logging.info("Driver closed successfully.")
    
    logging.info("Full-text and vector index creation process completed.")


def create_entity_embedding(graph:Neo4jGraph, embedding_provider, embedding_model):
    rows = fetch_entities_for_embedding(graph)
    for i in range(0, len(rows), 1000):
        update_embeddings(rows[i:i+1000],graph, embedding_provider, embedding_model)
            
def fetch_entities_for_embedding(graph):
    query = """
                MATCH (e)
                WHERE NOT (e:Chunk OR e:Document OR e:`__Community__` OR """ + PROTECTED_LABELS_WHERE + """) AND e.embedding IS NULL AND e.id IS NOT NULL
                RETURN elementId(e) AS elementId, e.id + " " + coalesce(e.description, "") AS text
                """
    result = execute_graph_query(graph,query)
    return [{"elementId": record["elementId"], "text": record["text"]} for record in result]

def update_embeddings(rows, graph, embedding_provider, embedding_model):
    embeddings, dimension = load_embedding_model(embedding_provider, embedding_model)
    logging.info("update embedding for entities")
    for row in rows:
        row['embedding'] = embeddings.embed_query(row['text'])                        
    query = """
      UNWIND $rows AS row
      MATCH (e) WHERE elementId(e) = row.elementId
      CALL db.create.setNodeVectorProperty(e, "embedding", row.embedding)
      """  
    return execute_graph_query(graph,query,params={'rows':rows})          

def _usable_mapping(mapping, existing, reserved):
    """LLM の応答の読み替えのうち、使えるもの（元の名前が今のグラフにあり、新しい名前が予約でなく、識別子に埋め込める）。"""
    usable = {}
    for old, new in mapping.items():
        if not isinstance(old, str) or not isinstance(new, str):
            continue
        if old not in existing or old in reserved or new in reserved:
            logging.warning(f"graph_schema_consolidation: skipped the mapping {old!r} -> {new!r}")
            continue
        if new.strip() != new or new == "" or "`" in new:
            logging.warning(f"graph_schema_consolidation: skipped the mapping {old!r} -> {new!r} (invalid name)")
            continue
        usable[old] = new
    return usable


def graph_schema_consolidation(graph):
    graphDb_data_Access = graphDBdataAccess(graph)
    node_labels,relation_labels = graphDb_data_Access.get_nodelabels_relationships()
    parser = JsonOutputParser()
    prompt = ChatPromptTemplate(
        messages=[("system", GRAPH_CLEANUP_PROMPT), ("human", "{input}")],
        partial_variables={"format_instructions": parser.get_format_instructions()}
    )
    graph_cleanup_model = get_value_from_env("GRAPH_CLEANUP_MODEL", 'openai_gpt_5.4_mini')
    llm, _, _ = get_llm(graph_cleanup_model)
    chain = prompt | llm | parser

    nodes_relations_input = {'nodes': node_labels, 'relationships': relation_labels}
    mappings = chain.invoke({'input': nodes_relations_input})
    node_mapping = {old: new for new, old_list in mappings['nodes'].items() for old in old_list if new != old}
    relation_mapping = {old: new for new, old_list in mappings['relationships'].items() for old in old_list if new != old}
    node_mapping = _usable_mapping(node_mapping, set(node_labels), set(RESERVED_LABELS) | {"Document", "__Entity__"})
    relation_mapping = _usable_mapping(relation_mapping, set(relation_labels), set(RESERVED_RELATIONSHIP_TYPES))

    logging.info(f"Node Labels: Total = {len(node_labels)}, Reduced to = {len(set(node_mapping.values()))} (from {len(node_mapping)})")
    logging.info(f"Relationship Types: Total = {len(relation_labels)}, Reduced to = {len(set(relation_mapping.values()))} (from {len(relation_mapping)})")
    if not node_mapping and not relation_mapping:
        return None

    # 書き換えの間は貸し出し（lease）を取り、編集の API と取り込みの保存を待たせる（設計メモ 2.6）。LLM の呼び出しの間は取らない。
    # 書き換えたエンティティの版（_v）を上げ（前の手動の操作の取り消しが旧い種類名に戻さないため）、修正台帳の
    # 種類名（type_override・manual_rel・tombstone_rel）も書き換える（再適用で旧い種類名が作り直されないため）
    with hold_lease(graph, "schema_consolidation"):
        for old_label, new_label in node_mapping.items():
            query = f"""
                    MATCH (n:`{old_label}`)
                    SET n:`{new_label}`
                    REMOVE n:`{old_label}`
                    WITH n WHERE n:__Entity__
                    SET n._v = coalesce(n._v, 0) + 1
                    """
            execute_graph_query(graph,query)

        for old_label, new_label in relation_mapping.items():
            # apoc.refactor.setType はリレーションのプロパティを写す（CREATE で作り直すとプロパティが落ちる）
            query = f"""
                    MATCH (n)-[r:`{old_label}`]->(m)
                    CALL apoc.refactor.setType(r, $new_type) YIELD output
                    WITH n, m
                    FOREACH (x IN [y IN [n, m] WHERE y:__Entity__] | SET x._v = coalesce(x._v, 0) + 1)
                    """
            execute_graph_query(graph,query,{"new_type": new_label})

        rename_schema_types_in_ledger(graph, node_mapping, relation_mapping)

    return None
