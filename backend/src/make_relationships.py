from langchain_neo4j import Neo4jGraph
from langchain_core.documents import Document
from src.shared.common_fn import load_embedding_model,execute_graph_query,get_value_from_env
import logging
from typing import List
import hashlib
import os
import json
import time
from langchain_neo4j import Neo4jVector

logging.basicConfig(format='%(asctime)s - %(message)s',level='INFO')

def merge_relationship_between_chunk_and_entites(graph: Neo4jGraph, graph_documents_chunk_chunk_Id : list):
    batch_data = []
    logging.info("Create HAS_ENTITY relationship between chunks and entities")
    
    for graph_doc_chunk_id in graph_documents_chunk_chunk_Id:
        for node in graph_doc_chunk_id['graph_doc'].nodes:
            query_data={
                'chunk_id': graph_doc_chunk_id['chunk_id'],
                'node_type': node.type,
                'node_id': node.id
            }
            batch_data.append(query_data)
          
    if batch_data:
        # 保存したエンティティを __Entity__ と名前で引く。種類のラベルで探して無ければ作る作りだと、
        # 保存の後に名前や種類が変わったとき __Entity__ を持たないノードが作られ、そこへ張られる（無ければ張らない）
        unwind_query = """
                    UNWIND $batch_data AS data
                    MATCH (c:Chunk {id: data.chunk_id})
                    MATCH (n:__Entity__ {id: data.node_id})
                    MERGE (c)-[:HAS_ENTITY]->(n)
                """
        execute_graph_query(graph,unwind_query, params={"batch_data": batch_data})

    
def create_chunk_embeddings(graph, chunkId_chunkDoc_list, file_name, embedding_provider, embedding_model):
    isEmbedding= get_value_from_env("IS_EMBEDDING", "True" ,"bool")
    
    embeddings, dimension = load_embedding_model(embedding_provider, embedding_model)
    logging.info(f'embedding model:{embeddings} and dimesion:{dimension}')
    data_for_query = []
    logging.info("update embedding and vector index for chunks")
    for row in chunkId_chunkDoc_list:
        if isEmbedding:
            embeddings_arr = embeddings.embed_query(row['chunk_doc'].page_content)
                                    
            data_for_query.append({
                "chunkId": row['chunk_id'],
                "embeddings": embeddings_arr
            })
    
    query_to_create_embedding = """
        UNWIND $data AS row
        MATCH (d:Document {fileName: $fileName})
        MERGE (c:Chunk {id: row.chunkId})
        SET c.embedding = row.embeddings
        MERGE (c)-[:PART_OF]->(d)
    """       
    execute_graph_query(graph,query_to_create_embedding, params={"fileName":file_name, "data":data_for_query})
    
def chunk_id_for(file_name: str, position: int, text: str) -> str:
    """チャンクの ID。既定（CHUNK_ID_MODE=document）は 文書名＋文書内の位置＋本文 のハッシュ。
    本文だけのハッシュ（CHUNK_ID_MODE=content、元の作り）だと、同じ本文の節（共通の判定基準、枠の定型文、
    版をまたいで変わらなかった節）が別の文書どうしで 1 つのノードに統合され、PART_OF と NEXT_CHUNK が文書をまたぐ。
    content は過去の実験の記録（本文のハッシュを ID にしている）を再現するためだけに残す。"""
    mode = os.environ.get("CHUNK_ID_MODE", "document").strip().lower()
    if mode == "content":
        return hashlib.sha1(text.encode()).hexdigest()
    return hashlib.sha1(f"{file_name}\x1f{position}\x1f{text}".encode()).hexdigest()


def create_relation_between_chunks(graph, file_name, chunks: List[Document])->list:
    logging.info("creating FIRST_CHUNK and NEXT_CHUNK relationships between chunks")
    current_chunk_id = ""
    lst_chunks_including_hash = []
    batch_data = []
    relationships = []
    offset=0
    for i, chunk in enumerate(chunks):
        previous_chunk_id = current_chunk_id
        position = i + 1 
        current_chunk_id = chunk_id_for(file_name, position, chunk.page_content)
        if i>0:
            offset += len(chunks[i-1].page_content)
        if i == 0:
            firstChunk = True
        else:
            firstChunk = False  
        metadata = {"position": position,"length": len(chunk.page_content), "content_offset":offset}
        chunk_document = Document(
            page_content=chunk.page_content, metadata=metadata
        )
        
        chunk_data = {
            "id": current_chunk_id,
            "pg_content": chunk_document.page_content,
            "position": position,
            "length": chunk_document.metadata["length"],
            "f_name": file_name,
            "previous_id" : previous_chunk_id,
            "content_offset" : offset
        }
        
        if 'page_number' in chunk.metadata:
            chunk_data['page_number'] = chunk.metadata['page_number']
        chunk_data['section'] = chunk.metadata.get('section')
        # 段 3 B4: 見出しの経路を配列（JSON 文字列）でも保存する。section は長いと途中の段が省かれるため
        chunk_data['section_path'] = json.dumps(chunk.metadata.get('section_path'), ensure_ascii=False) if chunk.metadata.get('section_path') else None
         
        if 'start_timestamp' in chunk.metadata and 'end_timestamp' in chunk.metadata:
            chunk_data['start_time'] = chunk.metadata['start_timestamp']
            chunk_data['end_time'] = chunk.metadata['end_timestamp'] 
               
        batch_data.append(chunk_data)
        
        lst_chunks_including_hash.append({'chunk_id': current_chunk_id, 'chunk_doc': chunk})
        
        # create relationships between chunks
        if firstChunk:
            relationships.append({"type": "FIRST_CHUNK", "chunk_id": current_chunk_id})
        else:
            relationships.append({
                "type": "NEXT_CHUNK",
                "previous_chunk_id": previous_chunk_id,  # ID of previous chunk
                "current_chunk_id": current_chunk_id
            })
          
    query_to_create_chunk_and_PART_OF_relation = """
        UNWIND $batch_data AS data
        MERGE (c:Chunk {id: data.id})
        SET c.text = data.pg_content, c.position = data.position, c.length = data.length, c.fileName=data.f_name, c.content_offset=data.content_offset
        WITH data, c
        SET c.page_number = CASE WHEN data.page_number IS NOT NULL THEN data.page_number END,
            c.start_time = CASE WHEN data.start_time IS NOT NULL THEN data.start_time END,
            c.end_time = CASE WHEN data.end_time IS NOT NULL THEN data.end_time END,
            c.section = CASE WHEN data.section IS NOT NULL THEN data.section END,
            c.section_path = CASE WHEN data.section_path IS NOT NULL THEN data.section_path END
        WITH data, c
        MATCH (d:Document {fileName: data.f_name})
        MERGE (c)-[:PART_OF]->(d)
    """
    execute_graph_query(graph,query_to_create_chunk_and_PART_OF_relation, params={"batch_data": batch_data})
    
    query_to_create_FIRST_relation = """ 
        UNWIND $relationships AS relationship
        MATCH (d:Document {fileName: $f_name})
        MATCH (c:Chunk {id: relationship.chunk_id})
        FOREACH(r IN CASE WHEN relationship.type = 'FIRST_CHUNK' THEN [1] ELSE [] END |
                MERGE (d)-[:FIRST_CHUNK]->(c))
        """
    execute_graph_query(graph,query_to_create_FIRST_relation, params={"f_name": file_name, "relationships": relationships})
    
    query_to_create_NEXT_CHUNK_relation = """ 
        UNWIND $relationships AS relationship
        MATCH (c:Chunk {id: relationship.current_chunk_id})
        WITH c, relationship
        MATCH (pc:Chunk {id: relationship.previous_chunk_id})
        FOREACH(r IN CASE WHEN relationship.type = 'NEXT_CHUNK' THEN [1] ELSE [] END |
                MERGE (c)<-[:NEXT_CHUNK]-(pc))
        """  
    execute_graph_query(graph,query_to_create_NEXT_CHUNK_relation, params={"relationships": relationships})
    return lst_chunks_including_hash


def create_chunk_vector_index(graph, embedding_provider, embedding_model):
    start_time = time.time()
    try:
        vector_index_query = "SHOW INDEXES YIELD name, type, labelsOrTypes, properties WHERE name = 'vector' AND type = 'VECTOR' AND 'Chunk' IN labelsOrTypes AND 'embedding' IN properties RETURN name"
        vector_index = execute_graph_query(graph,vector_index_query)
        if not vector_index:
            EMBEDDING_FUNCTION , EMBEDDING_DIMENSION = load_embedding_model(embedding_provider,embedding_model)
            vector_store = Neo4jVector(embedding=EMBEDDING_FUNCTION,
                                    graph=graph,
                                    node_label="Chunk", 
                                    embedding_node_property="embedding",
                                    index_name="vector",
                                    embedding_dimension=EMBEDDING_DIMENSION
                                    )
            vector_store.create_new_index()
            logging.info(f"Index created successfully. Time taken: {time.time() - start_time:.2f} seconds")
        else:
            logging.info(f"Index already exist,Skipping creation. Time taken: {time.time() - start_time:.2f} seconds")
    except Exception as e:
        if ("EquivalentSchemaRuleAlreadyExists" in str(e) or "An equivalent index already exists" in str(e)):
            logging.info("Vector index already exists, skipping creation.")
        else:
            raise