import logging
from neo4j import time 
from neo4j import GraphDatabase
from src.shared.common_fn import get_value_from_env
import json

from src.shared.constants import GRAPH_CHUNK_LIMIT,GRAPH_QUERY,CHUNK_TEXT_QUERY,COUNT_CHUNKS_QUERY,SCHEMA_VISUALIZATION_QUERY

def get_graphDB_driver(credentials):
    """
    Creates and returns a Neo4j database driver instance configured with the provided credentials.

    Returns:
    Neo4j.Driver: A driver object for interacting with the Neo4j database.

    """
    try:
        logging.info(f"Attempting to connect to the Neo4j database at {credentials.uri}")
        # Prefer credentials values, fallback to env if missing
        username = credentials.userName if credentials.userName is not None else get_value_from_env('NEO4J_USERNAME')
        password = credentials.password if credentials.password is not None else get_value_from_env('NEO4J_PASSWORD')
        database = credentials.database if hasattr(credentials, 'database') and credentials.database is not None else get_value_from_env('NEO4J_DATABASE')

        enable_user_agent = get_value_from_env("ENABLE_USER_AGENT", "False", "bool")
        if enable_user_agent:
            driver = GraphDatabase.driver(
                credentials.uri,
                auth=(username, password),
                database=database,
                user_agent=get_value_from_env("USER_AGENT", "LLM-Graph-Builder")
            )
        else:
            driver = GraphDatabase.driver(
                credentials.uri,
                auth=(username, password),
                database=database
            )
        logging.info("Connection successful")
        return driver
    except Exception:
        error_message = f"graph_query module: Failed to connect to the database at {credentials.uri}."
        logging.error(error_message, exc_info=True)


def execute_query(driver, query,document_names,doc_limit=None,**extra_params):
    """
    Executes a specified query using the Neo4j driver, with parameters based on the presence of a document name.

    Returns:
    tuple: Contains records, summary of the execution, and keys of the records.
    """
    try:
        if document_names:
            logging.info(f"Executing query for documents: {document_names}")
            records, summary, keys = driver.execute_query(query, document_names=document_names, **extra_params)
        else:
            logging.info(f"Executing query with a document limit of {doc_limit}")
            records, summary, keys = driver.execute_query(query, doc_limit=doc_limit)
        return records, summary, keys
    except Exception as e:
        error_message = f"graph_query module: Failed to execute the query. Error: {str(e)}"
        logging.error(error_message, exc_info=True)


def process_node(node):
    """
    Processes a node from a Neo4j database, extracting its ID, labels, and properties,
    while omitting certain properties like 'embedding' and 'text'.

    Returns:
    dict: A dictionary with the node's element ID, labels, and other properties,
          with datetime objects formatted as ISO strings.
    """
    try:
        labels = set(node.labels)
        labels.discard("__Entity__")
        if not labels:
            labels.add('*')
        
        node_element = {
            "element_id": node.element_id,
            "labels": list(labels),
            "properties": {}
        }
        # logging.info(f"Processing node with element ID: {node.element_id}")

        for key in node:
            if key in ["embedding", "text", "summary"]:
                continue
            value = node.get(key)
            if isinstance(value, time.DateTime):
                node_element["properties"][key] = value.isoformat()
                # logging.debug(f"Processed datetime property for {key}: {value.isoformat()}")
            else:
                node_element["properties"][key] = value

        return node_element
    except Exception:
        logging.error("graph_query module:An unexpected error occurred while processing the node")

# プロパティも返すリレーションタイプ（文書層のリレーション。始点が文書のものに限る）。画面の全文書のグラフの「文書間の関係のみ」の詳細欄が、版・目録上の日付・根拠チャンク・引用の表示に使用する
DOCUMENT_LAYER_RELATIONSHIP_TYPES = ("IS_VERSION_OF", "REFERS_TO", "LISTS")


def _json_value(value):
    """プロパティの値を JSON に変換できる値にする。neo4j の日時型は ISO 8601 の文字列、それ以外の型は文字列とする。"""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    # neo4j の Duration は tuple の派生型のため、日時型の判定を list・tuple より先に行う
    if isinstance(value, (time.Date, time.Time, time.DateTime, time.Duration)):
        return value.iso_format()
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def process_relationship_properties(relation):
    """文書層のリレーションのプロパティ。変換できない場合は None を返し、リレーション自体は返す（グラフの表示から欠落させない）。"""
    try:
        return {key: _json_value(relation.get(key)) for key in relation}
    except Exception:
        logging.warning(f"graph_query module: Failed to process properties of relationship {relation.element_id}", exc_info=True)
        return None


def extract_node_elements(records):
    """
    Extracts and processes unique nodes from a list of records, avoiding duplication by tracking seen element IDs.

    Returns:
    list of dict: A list containing processed node dictionaries.
    """
    node_elements = []
    seen_element_ids = set()  

    try:
        for record in records:
            nodes = record.get("nodes", [])
            if not nodes:
                # logging.debug(f"No nodes found in record: {record}")
                continue

            for node in nodes:
                if node.element_id in seen_element_ids:
                    # logging.debug(f"Skipping already processed node with ID: {node.element_id}")
                    continue
                seen_element_ids.add(node.element_id)
                node_element = process_node(node) 
                node_elements.append(node_element)
                # logging.info(f"Processed node with ID: {node.element_id}")

        return node_elements
    except Exception:
        logging.error("graph_query module: An error occurred while extracting node elements from records")

def extract_relationships(records):
    """
    Extracts and processes relationships from a list of records, ensuring that each relationship is processed
    only once by tracking seen element IDs.

    Returns:
    list of dict: A list containing dictionaries of processed relationships.
    """
    all_relationships = []
    seen_element_ids = set()

    try:
        for record in records:
            relationships = []
            relations = record.get("rels", [])
            if not relations:
                continue

            for relation in relations:
                if relation.element_id in seen_element_ids:
                    # logging.debug(f"Skipping already processed relationship with ID: {relation.element_id}")
                    continue
                seen_element_ids.add(relation.element_id)

                try:
                    nodes = relation.nodes
                    if len(nodes) < 2:
                        logging.warning(f"Relationship with ID {relation.element_id} does not have two nodes.")
                        continue

                    relationship = {
                        "element_id": relation.element_id,
                        "type": relation.type,
                        "start_node_element_id": process_node(nodes[0])["element_id"],
                        "end_node_element_id": process_node(nodes[1])["element_id"],
                    }
                    # 文書層のリレーション（始点が文書）に限る。チャンク間の REFERS_TO（引用文などを持つ）は画面で使用しないため返さない
                    if relation.type in DOCUMENT_LAYER_RELATIONSHIP_TYPES and "Document" in nodes[0].labels:
                        properties = process_relationship_properties(relation)
                        if properties is not None:
                            relationship["properties"] = properties
                    relationships.append(relationship)

                except Exception as inner_e:
                    logging.error(f"graph_query module: Failed to process relationship with ID {relation.element_id}. Error: {inner_e}", exc_info=True)
            all_relationships.extend(relationships)
        return all_relationships
    except Exception:
        logging.error("graph_query module: An error occurred while extracting relationships from records", exc_info=True)


def get_completed_documents(driver):
    """
    Retrieves the names of all documents with the status 'Completed' from the database.
    """
    docs_query = "MATCH(node:Document {status:'Completed'}) RETURN node"
    
    try:
        logging.info("Executing query to retrieve completed documents.")
        records, summary, keys = driver.execute_query(docs_query)
        logging.info(f"Query executed successfully, retrieved {len(records)} records.")
        documents = [record["node"]["fileName"] for record in records]
        logging.info("Document names extracted successfully.")
        
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        documents = []
    
    return documents


def get_graph_results(credentials, document_names, include_orphans=False):
    """
    Retrieves graph data by executing a specified Cypher query using credentials and parameters provided.
    Processes the results to extract nodes and relationships and packages them in a structured output.

    Args:
    credentials (Neo4jCredentials): The credentials object containing URI, username, password, and database information.
    document_names (str): A JSON string representing a list of document names to query for, if any.
    Returns:
    dict: Contains the session ID, user-defined messages with nodes and relationships, and the user module identifier.
    """
    try:
        logging.info("Starting graph query process")
        driver = get_graphDB_driver(credentials)  
        document_names= list(map(str, json.loads(document_names)))
        chunk_limit = get_value_from_env("GRAPH_CHUNK_LIMIT", GRAPH_CHUNK_LIMIT, "int")
        chunk_limit_clause = f"LIMIT {chunk_limit}" if chunk_limit and chunk_limit > 0 else ""
        query = GRAPH_QUERY.format(chunk_limit_clause=chunk_limit_clause)
        records, summary , keys = execute_query(driver, query.strip(), document_names, include_orphans=bool(include_orphans))
        document_nodes = extract_node_elements(records)
        document_relationships = extract_relationships(records)

        logging.info(f"no of nodes : {len(document_nodes)}")
        logging.info(f"no of relations : {len(document_relationships)}")
        result = {
            "nodes": document_nodes,
            "relationships": document_relationships
        }

        logging.info("Query process completed successfully")
        return result
    except Exception as e:
        logging.error(f"graph_query module: An error occurred in get_graph_results. Error: {str(e)}")
        raise Exception("graph_query module: An error occurred in get_graph_results. Please check the logs for more details.") from e
    finally:
        logging.info("Closing connection for graph_query api")
        driver.close()


def get_chunktext_results(credentials, document_name, page_no):
   """Retrieves chunk text, position, and page number from graph data with pagination."""
   driver = None
   try:
       logging.info("Starting chunk text query process")
       offset = 10
       skip = (page_no - 1) * offset
       limit = offset
       driver = get_graphDB_driver(credentials)  
       with driver.session(database=credentials.database) as session:
           total_chunks_result = session.run(COUNT_CHUNKS_QUERY, file_name=document_name)
           total_chunks = total_chunks_result.single()["total_chunks"]
           total_pages = (total_chunks + offset - 1) // offset  # Calculate total pages
           records = session.run(CHUNK_TEXT_QUERY, file_name=document_name, skip=skip, limit=limit)
           pageitems = [
               {
                   "text": record["chunk_text"],
                   "position": record["chunk_position"],
                   "pagenumber": record["page_number"]
               }
               for record in records
           ]
           logging.info(f"Query process completed with {len(pageitems)} chunks retrieved")
           return {
               "pageitems": pageitems,
               "total_pages": total_pages
           }
   except Exception as e:
       logging.error(f"An error occurred in get_chunktext_results. Error: {str(e)}")
       raise Exception("An error occurred in get_chunktext_results. Please check the logs for more details.") from e
   finally:
       if driver:
           driver.close()


def visualize_schema(credentials):
   """Retrieves graph schema"""
   driver = None
   try:
       logging.info("Starting visualizing graph schema")
       driver = get_graphDB_driver(credentials)  
       records, summary, keys = driver.execute_query(SCHEMA_VISUALIZATION_QUERY)
       nodes = records[0].get("nodes", [])
       relationships = records[0].get("relationships", [])
       result = {"nodes": nodes, "relationships": relationships}
       return result
   except Exception as e:
       logging.error(f"An error occurred schema retrieval. Error: {str(e)}")
       raise Exception(f"An error occurred schema retrieval. Error: {str(e)}")
   finally:
       if driver:
           driver.close()
