# グラフ確認画面で 1 文書あたりに描くチャンクの上限。本家は 50 の固定値。環境変数 GRAPH_CHUNK_LIMIT で上書き、0 以下は上限なし（既定）
GRAPH_CHUNK_LIMIT = 0

# 共有グラフ運用時の保護対象ラベル。
# チャット履歴(Session/Message)と外部プログラム(graphrag等)の管理ノードを、
# 後処理（エンティティ埋め込み・名寄せ・コミュニティ投影・未接続列挙・スキーマ統合）から除外する
# グラフの手動修正の管理用ノード（修正台帳・操作の履歴・排他。backend/src/graph_edit）
GRAPH_EDIT_MANAGEMENT_LABELS = ["__GraphEdit__", "__GraphEditOp__", "__GraphLock__"]
PROTECTED_MANAGEMENT_LABELS = ["Session", "Message", "SchemaMeta", "ProcessedChunk", "GraphProvenance", "CondFact", "Cond"] + GRAPH_EDIT_MANAGEMENT_LABELS
# 章・節の層（段 3 B4）の Section は規則で毎回作り直すので、後処理（埋め込み・コミュニティ投影・未接続列挙・スキーマ統合）の対象から外す
PROTECTED_MANAGEMENT_LABELS = PROTECTED_MANAGEMENT_LABELS + ['Section']
# graph-evidence が書く根拠の記録 Evidence（11/9）も、後処理・件数・スキーマの対象から外す
PROTECTED_MANAGEMENT_LABELS = PROTECTED_MANAGEMENT_LABELS + ['Evidence']
# 節の要点の控え SectionSummary（11/9。後処理 build_section_summaries が書く。関係を持たない管理用のノード）も、後処理・件数・スキーマの対象から外す
PROTECTED_MANAGEMENT_LABELS = PROTECTED_MANAGEMENT_LABELS + ['SectionSummary']
# Cypherラベル式 (:!A&!B...) に連結する形式
PROTECTED_LABELS_CYPHER = "".join(f"&!`{label}`" for label in PROTECTED_MANAGEMENT_LABELS)
# WHERE NOT (e:A OR e:B ...) に連結する形式
PROTECTED_LABELS_WHERE = " OR ".join(f"e:`{label}`" for label in PROTECTED_MANAGEMENT_LABELS)
# 外部プログラムが張る関係型（スキーマ統合のリネーム対象から保護）
PROTECTED_RELATIONSHIP_TYPES = ["NEXT", "LAST_MESSAGE", "REFERS_TO", "ALIAS_OF", "WHEN", "MENTIONS"]
# 文書層・章・節の層（段 3）が張る関係型。スキーマ統合の付け替えの候補から外す（graphDB_dataAccess.get_nodelabels_relationships だけが使う）
# PROTECTED_RELATIONSHIP_TYPES には足さない（グラフの手動修正の予約のリレーション種類がそこから作られ、APPLIES_TO は EDC の述語でもあるため）
LAYER_RELATIONSHIP_TYPES = ['IS_VERSION_OF', 'LISTS', 'ABOLISHES', 'APPLIES_TO', 'REPLACES', 'HAS_SECTION', 'IN_SECTION']

#query
# グラフ確認画面の問い合わせ（C4 で書き直し）。対象の文書・チャンク・チャンクのエンティティ（全文書表示では
# どのチャンクにも結ばれていないエンティティも）・コミュニティを集め、その間の関係をすべて 1 行で返す。
# 本家の問い合わせ（GRAPH_QUERY_UPSTREAM）は行が掛け算で増え、7 本で 38 秒かかった。ノードの集合は同じで 0.5 秒。
GRAPH_QUERY = """
MATCH (d:Document)
WHERE d.fileName IN $document_names
CALL {{
  WITH d
  OPTIONAL MATCH (d)<-[:PART_OF|FIRST_CHUNK]-(c:Chunk)
  RETURN c {chunk_limit_clause}
}}
WITH collect(DISTINCT d) AS docs, collect(DISTINCT c) AS chunks
CALL {{
  WITH chunks
  UNWIND chunks AS c
  MATCH (c)-[:HAS_ENTITY]->(e)
  RETURN collect(DISTINCT e) AS ents
}}
CALL {{
  OPTIONAL MATCH (o:__Entity__)
  WHERE $include_orphans AND NOT EXISTS {{ (o)<-[:HAS_ENTITY]-(:Chunk) }} AND EXISTS {{ (o)--(:!Chunk&!Document) }}
  RETURN collect(DISTINCT o) AS orphans
}}
WITH docs, chunks, ents + [o IN orphans WHERE NOT o IN ents] AS ents
CALL {{
  WITH ents
  UNWIND ents AS n
  OPTIONAL MATCH (n)-[:IN_COMMUNITY]->(p:__Community__)
  OPTIONAL MATCH (p)-[:PARENT_COMMUNITY*]->(p2:__Community__)
  RETURN collect(DISTINCT p) + collect(DISTINCT p2) AS comms
}}
// 段 3: 文書層（文書番号・型式のエンティティ）と章・節の層（Section）も描く
CALL {{
  WITH docs
  UNWIND docs AS d
  OPTIONAL MATCH (d)-[:IS_VERSION_OF|REFERS_TO|LISTS|ABOLISHES|APPLIES_TO]->(x:__Entity__)
  OPTIONAL MATCH (d)-[:HAS_SECTION*1..6]->(sec:Section)
  RETURN collect(DISTINCT x) + collect(DISTINCT sec) AS layer
}}
WITH docs + chunks + ents + comms + [l IN layer WHERE NOT l IN ents] AS nodes
CALL {{
  WITH nodes
  UNWIND nodes AS n
  MATCH (n)-[r]->(m)
  WHERE m IN nodes
  RETURN collect(DISTINCT r) AS rels
}}
RETURN nodes, rels
"""

GRAPH_QUERY_UPSTREAM = """
MATCH docs = (d:Document) 
WHERE d.fileName IN $document_names
WITH docs, d 
ORDER BY d.createdAt DESC

// Fetch chunks for documents, currently with limit
CALL {{
  WITH d
  OPTIONAL MATCH chunks = (d)<-[:PART_OF|FIRST_CHUNK]-(c:Chunk)
  RETURN c, chunks {chunk_limit_clause}
}}

WITH collect(distinct docs) AS docs, 
     collect(distinct chunks) AS chunks, 
     collect(distinct c) AS selectedChunks

// Select relationships between selected chunks
WITH *, 
     [c IN selectedChunks | 
       [p = (c)-[:NEXT_CHUNK|SIMILAR|REFERS_TO]-(other) 
       WHERE other IN selectedChunks | p]] AS chunkRels

// Fetch entities and relationships between entities
CALL {{
  WITH selectedChunks
  UNWIND selectedChunks AS c
  OPTIONAL MATCH entities = (c:Chunk)-[:HAS_ENTITY]->(e)
  OPTIONAL MATCH entityRels = (e)--(e2:!Chunk) 
  WHERE exists {{
    (e2)<-[:HAS_ENTITY]-(other) WHERE other IN selectedChunks
  }}
  RETURN entities, entityRels, collect(DISTINCT e) AS entity
}}

WITH docs, chunks, chunkRels, 
     collect(entities) AS entities, 
     collect(entityRels) AS entityRels, 
     entity

// 全文書表示のときだけ、どのチャンクからも HAS_ENTITY で結ばれていないエンティティとその関係も描く
CALL {{
  OPTIONAL MATCH orphanRels = (o:__Entity__)-[]-(x:!Chunk&!Document)
  WHERE $include_orphans AND NOT EXISTS {{ (o)<-[:HAS_ENTITY]-(:Chunk) }}
  RETURN collect(orphanRels) AS orphanPaths
}}

WITH *

CALL {{
  WITH entity
  UNWIND entity AS n
  OPTIONAL MATCH community = (n:__Entity__)-[:IN_COMMUNITY]->(p:__Community__)
  OPTIONAL MATCH parentcommunity = (p)-[:PARENT_COMMUNITY*]->(p2:__Community__) 
  RETURN collect(community) AS communities, 
         collect(parentcommunity) AS parentCommunities
}}

WITH apoc.coll.flatten(docs + chunks + chunkRels + entities + entityRels + orphanPaths + communities + parentCommunities, true) AS paths

// Distinct nodes and relationships
CALL {{
  WITH paths 
  UNWIND paths AS path 
  UNWIND nodes(path) AS node 
  WITH distinct node 
  RETURN collect(node /* {{.*, labels:labels(node), elementId:elementId(node), embedding:null, text:null}} */) AS nodes 
}}

CALL {{
  WITH paths 
  UNWIND paths AS path 
  UNWIND relationships(path) AS rel 
  RETURN collect(distinct rel) AS rels 
}}  

RETURN nodes, rels

"""

CHUNK_QUERY = """
MATCH (chunk:Chunk)
WHERE chunk.id IN $chunksIds
MATCH (chunk)-[:PART_OF]->(d:Document)

WITH d, 
     collect(distinct chunk) AS chunks

// Collect relationships and nodes
WITH d, chunks, 
     collect {
         MATCH ()-[r]->() 
         WHERE elementId(r) IN $relationshipIds
         RETURN r
     } AS rels,
     collect {
         MATCH (e) 
         WHERE elementId(e) IN $entityIds
         RETURN e
     } AS nodes

WITH d, 
     chunks, 
     apoc.coll.toSet(apoc.coll.flatten(rels)) AS rels, 
     nodes

RETURN 
    d AS doc, 
    [chunk IN chunks | 
        chunk {.*, embedding: null, element_id: elementId(chunk)}
    ] AS chunks,
    [
        node IN nodes | 
        {
            element_id: elementId(node),
            labels: labels(node),
            properties: {
                id: node.id,
                description: node.description
            }
        }
    ] AS nodes,
    [
        r IN rels | 
        {
            startNode: {
                element_id: elementId(startNode(r)),
                labels: labels(startNode(r)),
                properties: {
                    id: startNode(r).id,
                    description: startNode(r).description
                }
            },
            endNode: {
                element_id: elementId(endNode(r)),
                labels: labels(endNode(r)),
                properties: {
                    id: endNode(r).id,
                    description: endNode(r).description
                }
            },
            relationship: {
                type: type(r),
                element_id: elementId(r)
            }
        }
    ] AS entities
"""

COUNT_CHUNKS_QUERY = """
MATCH (d:Document {fileName: $file_name})<-[:PART_OF]-(c:Chunk)
RETURN count(c) AS total_chunks
"""

CHUNK_TEXT_QUERY = """
MATCH (d:Document {fileName: $file_name})<-[:PART_OF]-(c:Chunk)
RETURN c.text AS chunk_text, c.position AS chunk_position, c.page_number AS page_number
ORDER BY c.position
SKIP $skip
LIMIT $limit
"""

NODEREL_COUNT_QUERY_WITH_COMMUNITY = """
MATCH (d:Document)
WHERE d.fileName IS NOT NULL
OPTIONAL MATCH (d)<-[po:PART_OF]-(c:Chunk)
OPTIONAL MATCH (c)-[he:HAS_ENTITY]->(e:__Entity__)
OPTIONAL MATCH (c)-[sim:SIMILAR]->(c2:Chunk)
OPTIONAL MATCH (c)-[nc:NEXT_CHUNK]->(c3:Chunk)
OPTIONAL MATCH (e)-[ic:IN_COMMUNITY]->(comm:__Community__)
OPTIONAL MATCH (comm)-[pc1:PARENT_COMMUNITY]->(first_level:__Community__)
OPTIONAL MATCH (first_level)-[pc2:PARENT_COMMUNITY]->(second_level:__Community__)
OPTIONAL MATCH (second_level)-[pc3:PARENT_COMMUNITY]->(third_level:__Community__)
WITH
  d.fileName AS filename,
  count(DISTINCT c) AS chunkNodeCount,
  count(DISTINCT po) AS partOfRelCount,
  count(DISTINCT he) AS hasEntityRelCount,
  count(DISTINCT sim) AS similarRelCount,
  count(DISTINCT nc) AS nextChunkRelCount,
  count(DISTINCT e) AS entityNodeCount,
  collect(DISTINCT e) AS entities,
  count(DISTINCT comm) AS baseCommunityCount,
  count(DISTINCT first_level) AS firstlevelcommCount,
  count(DISTINCT second_level) AS secondlevelcommCount,
  count(DISTINCT third_level) AS thirdlevelcommCount,
  count(DISTINCT ic) AS inCommunityCount,
  count(DISTINCT pc1) AS parentCommunityRelCount1,
  count(DISTINCT pc2) AS parentCommunityRelCount2,
  count(DISTINCT pc3) AS parentCommunityRelCount3
WITH
  filename,
  chunkNodeCount,
  partOfRelCount + hasEntityRelCount + similarRelCount + nextChunkRelCount AS chunkRelCount,
  entityNodeCount,
  entities,
  baseCommunityCount + firstlevelcommCount + secondlevelcommCount + thirdlevelcommCount AS commCount,
  inCommunityCount + parentCommunityRelCount1 + parentCommunityRelCount2 + parentCommunityRelCount3 AS communityRelCount
CALL (entities) {
  UNWIND entities AS e
  RETURN sum(COUNT { (e)-->(e2:__Entity__) WHERE e2 in entities }) AS entityEntityRelCount
}
RETURN
  filename,
  COALESCE(chunkNodeCount, 0) AS chunkNodeCount,
  COALESCE(chunkRelCount, 0) AS chunkRelCount,
  COALESCE(entityNodeCount, 0) AS entityNodeCount,
  COALESCE(entityEntityRelCount, 0) AS entityEntityRelCount,
  COALESCE(commCount, 0) AS communityNodeCount,
  COALESCE(communityRelCount, 0) AS communityRelCount
"""
NODEREL_COUNT_QUERY_WITHOUT_COMMUNITY = """
MATCH (d:Document)
WHERE d.fileName = $document_name
OPTIONAL MATCH (d)<-[po:PART_OF]-(c:Chunk)
OPTIONAL MATCH (c)-[he:HAS_ENTITY]->(e:__Entity__)
OPTIONAL MATCH (c)-[sim:SIMILAR]->(c2:Chunk)
OPTIONAL MATCH (c)-[nc:NEXT_CHUNK]->(c3:Chunk)
WITH
  d.fileName AS filename,
  count(DISTINCT c) AS chunkNodeCount,
  count(DISTINCT po) AS partOfRelCount,
  count(DISTINCT he) AS hasEntityRelCount,
  count(DISTINCT sim) AS similarRelCount,
  count(DISTINCT nc) AS nextChunkRelCount,
  count(DISTINCT e) AS entityNodeCount,
  collect(DISTINCT e) AS entities
WITH
  filename,
  chunkNodeCount,
  partOfRelCount + hasEntityRelCount + similarRelCount + nextChunkRelCount AS chunkRelCount,
  entityNodeCount,
  entities
CALL (entities) {
  UNWIND entities AS e
  RETURN sum(COUNT { (e)-->(e2:__Entity__) WHERE e2 in entities }) AS entityEntityRelCount
}
RETURN
  filename,
  COALESCE(chunkNodeCount, 0) AS chunkNodeCount,
  COALESCE(chunkRelCount, 0) AS chunkRelCount,
  COALESCE(entityNodeCount, 0) AS entityNodeCount,
  COALESCE(entityEntityRelCount, 0) AS entityEntityRelCount
"""


## CHAT SETUP
CHAT_MAX_TOKENS = 1000
CHAT_SEARCH_KWARG_SCORE_THRESHOLD = 0.5
CHAT_DOC_SPLIT_SIZE = 3000
CHAT_EMBEDDING_FILTER_SCORE_THRESHOLD = 0.10

CHAT_TOKEN_CUT_OFF = {
  ("gemini_3.5_flash","groq-llama3",'groq_llama3_70b','anthropic_claude_4') : 4,
  ("openai_gpt_5.5","diffbot","anthropic_claude_4.7_opus") : 28,
     ("ollama_llama3") : 2  
}  

### CHAT TEMPLATES 
CHAT_SYSTEM_TEMPLATE = """
You are an AI-powered question-answering agent. Your task is to provide accurate and comprehensive responses to user queries based on the given context, chat history, and available resources.

### Response Guidelines:
1. **Direct Answers**: Provide clear and thorough answers to the user's queries without headers unless requested. Avoid speculative responses.
2. **Utilize History and Context**: Leverage relevant information from previous interactions, the current user input, and the context provided below.
3. **No Greetings in Follow-ups**: Start with a greeting in initial interactions. Avoid greetings in subsequent responses unless there's a significant break or the chat restarts.
4. **Admit Unknowns**: Clearly state if an answer is unknown. Avoid making unsupported statements.
5. **Avoid Hallucination**: Only provide information based on the context provided. Do not invent information.
6. **Response Length**: Keep responses concise and relevant. Aim for clarity and completeness within 4-5 sentences unless more detail is requested.
7. **Tone and Style**: Maintain a professional and informative tone. Be friendly and approachable.
8. **Error Handling**: If a query is ambiguous or unclear, ask for clarification rather than providing a potentially incorrect answer.
9. **Fallback Options**: If the required information is not available in the provided context, provide a polite and helpful response. Example: "I don't have that information right now." or "I'm sorry, but I don't have that information. Is there something else I can help with?"
10. **Context Availability**: If the context is empty, do not provide answers based solely on internal knowledge. Instead, respond appropriately by indicating the lack of information.


**IMPORTANT** : DO NOT ANSWER FROM YOUR KNOWLEDGE BASE USE THE BELOW CONTEXT

### Context:
<context>
{context}
</context>

### Example Responses:
User: Hi 
AI Response: 'Hello there! How can I assist you today?'

User: "What is Langchain?"
AI Response: "Langchain is a framework that enables the development of applications powered by large language models, such as chatbots. It simplifies the integration of language models into various applications by providing useful tools and components."

User: "Can you explain how to use memory management in Langchain?"
AI Response: "Langchain's memory management involves utilizing built-in mechanisms to manage conversational context effectively. It ensures that the conversation remains coherent and relevant by maintaining the history of interactions and using it to inform responses."

User: "I need help with PyCaret's classification model."
AI Response: "PyCaret simplifies the process of building and deploying machine learning models. For classification tasks, you can use PyCaret's setup function to prepare your data. After setup, you can compare multiple models to find the best one, and then fine-tune it for better performance."

User: "What can you tell me about the latest realtime trends in AI?"
AI Response: "I don't have that information right now. Is there something else I can help with?"

Note: This system does not generate answers based solely on internal knowledge. It answers from the information provided in the user's current and previous inputs, and from the context.
"""

# 参照（「…による」「…参照」）の扱い。検索設定 reference_prompt がオンのときだけ、Response Guidelines の末尾に足す。
# すべての回答に効くので、短く、文書の種類に依らない書き方にしている（波括弧は使わない: プロンプトの差し込み記号と衝突する）。
# 2 文目の「手元の文書に含まれていないと明言する」は外さないこと。「どこを見ればよいかを答える」だけにすると、
# 答えが文書群に無い問いで「書かれていない」と述べる回答が減った（開発用の問い 11 問で 9 → 8。この文にして 9 に戻った）。
CHAT_REFERENCE_GUIDELINE = (
    "11. **Cross-references**: When a passage in the context points to another place (for example 「…による」「…参照」) "
    "and the referenced passage is also in the context (it may be labeled 「参照先」), answer concretely with what the referenced passage says. "
    "If the referenced passage is not in the context, state clearly that its content is not included in the provided documents, "
    "say where to look, and do not guess its content."
)


def chat_system_template(reference_guideline: bool = False) -> str:
    """回答のシステムプロンプト。reference_guideline が真なら、参照の扱いの 1 項を Response Guidelines の末尾に足す。"""
    if not reference_guideline:
        return CHAT_SYSTEM_TEMPLATE
    marker = "\n**IMPORTANT**"
    head, found, tail = CHAT_SYSTEM_TEMPLATE.partition(marker)
    if not found:
        return CHAT_SYSTEM_TEMPLATE + "\n" + CHAT_REFERENCE_GUIDELINE + "\n"
    return head.rstrip("\n") + "\n" + CHAT_REFERENCE_GUIDELINE + "\n\n" + marker + tail


QUESTION_TRANSFORM_TEMPLATE = "Given the below conversation, generate a search query to look up in order to get information relevant to the conversation. Only respond with the query, nothing else." 

## CHAT QUERIES
VECTOR_SEARCH_TOP_K = 5

VECTOR_SEARCH_QUERY = """
WITH node AS chunk, score
MATCH (chunk)-[:PART_OF]->(d:Document)
WITH d, 
     collect(distinct {chunk: chunk, score: score}) AS chunks, 
     avg(score) AS avg_score

WITH d, avg_score, 
     [c IN chunks | c.chunk.text] AS texts, 
     [c IN chunks | {id: c.chunk.id, score: c.score}] AS chunkdetails

WITH d, avg_score, chunkdetails, 
     apoc.text.join(texts, "\n----\n") AS text

RETURN text, 
       avg_score AS score, 
       {source: COALESCE(CASE WHEN d.url CONTAINS "None" 
                             THEN d.fileName 
                             ELSE d.url 
                       END, 
                       d.fileName), 
        chunkdetails: chunkdetails} AS metadata
""" 

### Vector graph search 
VECTOR_GRAPH_SEARCH_ENTITY_LIMIT = 40
VECTOR_GRAPH_SEARCH_EMBEDDING_MIN_MATCH = 0.3
VECTOR_GRAPH_SEARCH_EMBEDDING_MAX_MATCH = 0.9
VECTOR_GRAPH_SEARCH_ENTITY_LIMIT_MINMAX_CASE = 20
VECTOR_GRAPH_SEARCH_ENTITY_LIMIT_MAX_CASE = 40

VECTOR_GRAPH_SEARCH_QUERY_PREFIX = """
WITH node as chunk, score
// find the document of the chunk
MATCH (chunk)-[:PART_OF]->(d:Document)
// aggregate chunk-details
WITH d, collect(DISTINCT {chunk: chunk, score: score}) AS chunks, avg(score) as avg_score
// fetch entities
CALL { WITH chunks
UNWIND chunks as chunkScore
WITH chunkScore.chunk as chunk
"""

VECTOR_GRAPH_SEARCH_ENTITY_QUERY = """
    OPTIONAL MATCH (chunk)-[:HAS_ENTITY]->(e)
    WITH e, count(*) AS numChunks 
    ORDER BY numChunks DESC 
    LIMIT {no_of_entites}

    WITH 
    CASE 
        WHEN e.embedding IS NULL OR ({embedding_match_min} <= vector.similarity.cosine($query_vector, e.embedding) AND vector.similarity.cosine($query_vector, e.embedding) <= {embedding_match_max}) THEN 
            collect {{
                OPTIONAL MATCH path=(e)(()-[rels:!HAS_ENTITY&!PART_OF&!TOUCHED]-()){{0,1}}(:!Chunk&!Document&!__Community__&!Evidence) 
                RETURN path LIMIT {entity_limit_minmax_case}
            }}
        WHEN e.embedding IS NOT NULL AND vector.similarity.cosine($query_vector, e.embedding) >  {embedding_match_max} THEN
            collect {{
                OPTIONAL MATCH path=(e)(()-[rels:!HAS_ENTITY&!PART_OF&!TOUCHED]-()){{0,2}}(:!Chunk&!Document&!__Community__&!Evidence) 
                RETURN path LIMIT {entity_limit_max_case} 
            }} 
        ELSE 
            collect {{ 
                MATCH path=(e) 
                RETURN path 
            }}
    END AS paths, e
"""

# VECTOR_GRAPH_SEARCH_QUERY_SUFFIX = """
#     WITH apoc.coll.toSet(apoc.coll.flatten(collect(DISTINCT paths))) AS paths, 
#          collect(DISTINCT e) AS entities

#     // De-duplicate nodes and relationships across chunks
#     RETURN 
#         collect {
#             UNWIND paths AS p 
#             UNWIND relationships(p) AS r 
#             RETURN DISTINCT r
#         } AS rels,
#         collect {
#             UNWIND paths AS p 
#             UNWIND nodes(p) AS n 
#             RETURN DISTINCT n
#         } AS nodes, 
#         entities
# }

# // Generate metadata and text components for chunks, nodes, and relationships
# WITH d, avg_score,
#      [c IN chunks | c.chunk.text] AS texts, 
#      [c IN chunks | {id: c.chunk.id, score: c.score}] AS chunkdetails,
#      [n IN nodes | elementId(n)] AS entityIds,
#      [r IN rels | elementId(r)] AS relIds,
#      apoc.coll.sort([
#          n IN nodes | 
#          coalesce(apoc.coll.removeAll(labels(n), ['__Entity__'])[0], "") + ":" + 
#          n.id + 
#          (CASE WHEN n.description IS NOT NULL THEN " (" + n.description + ")" ELSE "" END)
#      ]) AS nodeTexts,
#      apoc.coll.sort([
#          r IN rels | 
#          coalesce(apoc.coll.removeAll(labels(startNode(r)), ['__Entity__'])[0], "") + ":" + 
#          startNode(r).id + " " + type(r) + " " + 
#          coalesce(apoc.coll.removeAll(labels(endNode(r)), ['__Entity__'])[0], "") + ":" + endNode(r).id
#      ]) AS relTexts,
#      entities

# // Combine texts into response text
# WITH d, avg_score, chunkdetails, entityIds, relIds,
#      "Text Content:\n" + apoc.text.join(texts, "\n----\n") +
#      "\n----\nEntities:\n" + apoc.text.join(nodeTexts, "\n") +
#      "\n----\nRelationships:\n" + apoc.text.join(relTexts, "\n") AS text, 
#      entities

# RETURN 
#     text, 
#     avg_score AS score, 
#     {
#         length: size(text), 
#         source: COALESCE(CASE WHEN d.url CONTAINS "None" THEN d.fileName ELSE d.url END, d.fileName), 
#         chunkdetails: chunkdetails, 
#         entities : {
#             entityids: entityIds, 
#             relationshipids: relIds
#         }
#     } AS metadata
# """
VECTOR_GRAPH_SEARCH_QUERY_SUFFIX = """
   WITH apoc.coll.toSet(apoc.coll.flatten(collect(DISTINCT paths))) AS paths,
        collect(DISTINCT e) AS entities
   // De-duplicate nodes and relationships across chunks
   RETURN
       collect {
           UNWIND paths AS p
           UNWIND relationships(p) AS r
           RETURN DISTINCT r
       } AS rels,
       collect {
           UNWIND paths AS p
           UNWIND nodes(p) AS n
           RETURN DISTINCT n
       } AS nodes,
       entities
}
// Generate metadata and text components for chunks, nodes, and relationships
WITH d, avg_score,
    [c IN chunks | c.chunk.text] AS texts,
    [c IN chunks | {id: c.chunk.id, score: c.score}] AS chunkdetails,
    [n IN nodes | elementId(n)] AS entityIds,
    [r IN rels | elementId(r)] AS relIds,
    apoc.coll.sort([
        n IN nodes |
        coalesce(apoc.coll.removeAll(labels(n), ['__Entity__'])[0], "") + ":" +
        coalesce(
            n.id,
            n[head([k IN keys(n) WHERE k =~ "(?i)(name|title|id|description)$"])],
            ""
        ) +
        (CASE WHEN n.description IS NOT NULL THEN " (" + n.description + ")" ELSE "" END)
    ]) AS nodeTexts,
    apoc.coll.sort([
        r IN rels |
        coalesce(apoc.coll.removeAll(labels(startNode(r)), ['__Entity__'])[0], "") + ":" +
        coalesce(
            startNode(r).id,
            startNode(r)[head([k IN keys(startNode(r)) WHERE k =~ "(?i)(name|title|id|description)$"])],
            ""
        ) + " " + type(r) + (CASE WHEN size([k IN keys(r) WHERE NOT k IN ['embedding', 'source_chunk_id', 'evidence', 'evidence_kind', 'provenance_method'] AND (CASE WHEN valueType(r[k]) STARTS WITH 'LIST' THEN apoc.text.join([x IN r[k] | toString(x)], ' / ') ELSE toString(r[k]) END) <> '']) > 0 THEN " {{" + apoc.text.join([k IN keys(r) WHERE NOT k IN ['embedding', 'source_chunk_id', 'evidence', 'evidence_kind', 'provenance_method'] AND (CASE WHEN valueType(r[k]) STARTS WITH 'LIST' THEN apoc.text.join([x IN r[k] | toString(x)], ' / ') ELSE toString(r[k]) END) <> '' | k + ": " + (CASE WHEN valueType(r[k]) STARTS WITH 'LIST' THEN apoc.text.join([x IN r[k] | toString(x)], ' / ') ELSE toString(r[k]) END)], ", ") + "}}" ELSE "" END) + " " +
        coalesce(apoc.coll.removeAll(labels(endNode(r)), ['__Entity__'])[0], "") + ":" +
        coalesce(
            endNode(r).id,
            endNode(r)[head([k IN keys(endNode(r)) WHERE k =~ "(?i)(name|title|id|description)$"])],
            ""
        )
    ]) AS relTexts,
    entities
// Combine texts into response text
WITH d, avg_score, chunkdetails, entityIds, relIds,
    "Text Content:\n" + apoc.text.join(texts, "\n----\n") +
    "\n----\nEntities:\n" + apoc.text.join(nodeTexts, "\n") +
    "\n----\nRelationships:\n" + apoc.text.join(relTexts, "\n") AS text,
    entities
RETURN
   text,
   avg_score AS score,
   {
       length: size(text),
       source: COALESCE(CASE WHEN d.url CONTAINS "None" THEN d.fileName ELSE d.url END, d.fileName),
       chunkdetails: chunkdetails,
       entities : {
           entityids: entityIds,
           relationshipids: relIds
       }
   } AS metadata
"""

VECTOR_GRAPH_SEARCH_QUERY = VECTOR_GRAPH_SEARCH_QUERY_PREFIX+ VECTOR_GRAPH_SEARCH_ENTITY_QUERY.format(
    no_of_entites=VECTOR_GRAPH_SEARCH_ENTITY_LIMIT,
    embedding_match_min=VECTOR_GRAPH_SEARCH_EMBEDDING_MIN_MATCH,
    embedding_match_max=VECTOR_GRAPH_SEARCH_EMBEDDING_MAX_MATCH,
    entity_limit_minmax_case=VECTOR_GRAPH_SEARCH_ENTITY_LIMIT_MINMAX_CASE,
    entity_limit_max_case=VECTOR_GRAPH_SEARCH_ENTITY_LIMIT_MAX_CASE
) + VECTOR_GRAPH_SEARCH_QUERY_SUFFIX

### Local community search
LOCAL_COMMUNITY_TOP_K = 10
LOCAL_COMMUNITY_TOP_CHUNKS = 3
LOCAL_COMMUNITY_TOP_COMMUNITIES = 3
LOCAL_COMMUNITY_TOP_OUTSIDE_RELS = 10

LOCAL_COMMUNITY_SEARCH_QUERY = """
WITH collect(node) AS nodes, 
     avg(score) AS score, 
     collect({{entityids: elementId(node), score: score}}) AS metadata

WITH score, nodes, metadata,

     collect {{
         UNWIND nodes AS n
         MATCH (n)<-[:HAS_ENTITY]->(c:Chunk)
         WITH c, count(distinct n) AS freq
         RETURN c
         ORDER BY freq DESC
         LIMIT {topChunks}
     }} AS chunks,

     collect {{
         UNWIND nodes AS n
         OPTIONAL MATCH (n)-[:IN_COMMUNITY]->(c:__Community__)
         WITH c, c.community_rank AS rank, c.weight AS weight
         RETURN c
         ORDER BY rank, weight DESC
         LIMIT {topCommunities}
     }} AS communities,

     collect {{
         UNWIND nodes AS n
         UNWIND nodes AS m
         MATCH (n)-[r]->(m)
         RETURN DISTINCT r
         // TODO: need to add limit
     }} AS rels,

     collect {{
         UNWIND nodes AS n
         MATCH path = (n)-[r]-(m:__Entity__)
         WHERE NOT m IN nodes
         WITH m, collect(distinct r) AS rels, count(*) AS freq
         ORDER BY freq DESC 
         LIMIT {topOutsideRels}
         WITH collect(m) AS outsideNodes, apoc.coll.flatten(collect(rels)) AS rels
         RETURN {{ nodes: outsideNodes, rels: rels }}
     }} AS outside
"""

LOCAL_COMMUNITY_SEARCH_QUERY_SUFFIX = """
RETURN {
  chunks: [c IN chunks | c.text],
  communities: [c IN communities | c.summary],
  entities: [
    n IN nodes | 
    CASE 
      WHEN size(labels(n)) > 1 THEN 
        apoc.coll.removeAll(labels(n), ["__Entity__"])[0] + ":" + n.id + " " + coalesce(n.description, "")
      ELSE 
        n.id + " " + coalesce(n.description, "")
    END
  ],
  relationships: [
    r IN rels | 
    startNode(r).id + " " + type(r) + (CASE WHEN size([k IN keys(r) WHERE NOT k IN ['embedding', 'source_chunk_id', 'evidence', 'evidence_kind', 'provenance_method'] AND (CASE WHEN valueType(r[k]) STARTS WITH 'LIST' THEN apoc.text.join([x IN r[k] | toString(x)], ' / ') ELSE toString(r[k]) END) <> '']) > 0 THEN " {{" + apoc.text.join([k IN keys(r) WHERE NOT k IN ['embedding', 'source_chunk_id', 'evidence', 'evidence_kind', 'provenance_method'] AND (CASE WHEN valueType(r[k]) STARTS WITH 'LIST' THEN apoc.text.join([x IN r[k] | toString(x)], ' / ') ELSE toString(r[k]) END) <> '' | k + ": " + (CASE WHEN valueType(r[k]) STARTS WITH 'LIST' THEN apoc.text.join([x IN r[k] | toString(x)], ' / ') ELSE toString(r[k]) END)], ", ") + "}}" ELSE "" END) + " " + endNode(r).id
  ],
  outside: {
    nodes: [
      n IN outside[0].nodes | 
      CASE 
        WHEN size(labels(n)) > 1 THEN 
          apoc.coll.removeAll(labels(n), ["__Entity__"])[0] + ":" + n.id + " " + coalesce(n.description, "")
        ELSE 
          n.id + " " + coalesce(n.description, "")
      END
    ],
    relationships: [
      r IN outside[0].rels | 
      CASE 
        WHEN size(labels(startNode(r))) > 1 THEN 
          apoc.coll.removeAll(labels(startNode(r)), ["__Entity__"])[0] + ":" + startNode(r).id + " "
        ELSE 
          startNode(r).id + " "
      END + 
      type(r) + " " +
      CASE 
        WHEN size(labels(endNode(r))) > 1 THEN 
          apoc.coll.removeAll(labels(endNode(r)), ["__Entity__"])[0] + ":" + endNode(r).id
        ELSE 
          endNode(r).id
      END
    ]
  }
} AS text,
score,
{entities: metadata} AS metadata
"""

LOCAL_COMMUNITY_DETAILS_QUERY_PREFIX = """
UNWIND $entityIds as id
MATCH (node) WHERE elementId(node) = id
WITH node, 1.0 as score
"""
LOCAL_COMMUNITY_DETAILS_QUERY_SUFFIX = """
WITH *
UNWIND chunks AS c
MATCH (c)-[:PART_OF]->(d:Document)
RETURN 
    [
        c {
            .*,
            embedding: null,
            fileName: d.fileName,
            fileSource: d.fileSource, 
            element_id: elementId(c)
        }
    ] AS chunks,
    [
        community IN communities WHERE community IS NOT NULL | 
        community {
            .*,
            embedding: null,
            element_id:elementId(community)
        }
    ] AS communities,
    [
        node IN nodes + outside[0].nodes | 
        {
            element_id: elementId(node),
            labels: labels(node),
            properties: {
                id: node.id,
                description: node.description
            }
        }
    ] AS nodes, 
    [
        r IN rels + outside[0].rels | 
        {
            startNode: {
                element_id: elementId(startNode(r)),
                labels: labels(startNode(r)),
                properties: {
                    id: startNode(r).id,
                    description: startNode(r).description
                }
            },
            endNode: {
                element_id: elementId(endNode(r)),
                labels: labels(endNode(r)),
                properties: {
                    id: endNode(r).id,
                    description: endNode(r).description
                }
            },
            relationship: {
                type: type(r),
                element_id: elementId(r)
            }
        }
    ] AS entities
"""

LOCAL_COMMUNITY_SEARCH_QUERY_FORMATTED = LOCAL_COMMUNITY_SEARCH_QUERY.format(
    topChunks=LOCAL_COMMUNITY_TOP_CHUNKS,
    topCommunities=LOCAL_COMMUNITY_TOP_COMMUNITIES,
    topOutsideRels=LOCAL_COMMUNITY_TOP_OUTSIDE_RELS)+LOCAL_COMMUNITY_SEARCH_QUERY_SUFFIX

GLOBAL_SEARCH_TOP_K = 10

GLOBAL_VECTOR_SEARCH_QUERY = """
WITH collect(distinct {community: node, score: score}) AS communities,
     avg(score) AS avg_score

WITH avg_score,
     [c IN communities | c.community.summary] AS texts,
     [c IN communities | {id: elementId(c.community), score: c.score}] AS communityDetails

WITH avg_score, communityDetails,
     apoc.text.join(texts, "\n----\n") AS text

RETURN text,
       avg_score AS score,
       {communitydetails: communityDetails} AS metadata
"""



GLOBAL_COMMUNITY_DETAILS_QUERY = """
MATCH (community:__Community__)
WHERE elementId(community) IN $communityids
WITH collect(distinct community) AS communities
RETURN [community IN communities | 
        community {.*, embedding: null, element_id: elementId(community)}] AS communities
"""

## CHAT MODES 

CHAT_VECTOR_MODE = "vector"
CHAT_FULLTEXT_MODE = "fulltext"
CHAT_ENTITY_VECTOR_MODE = "entity_vector"
CHAT_VECTOR_GRAPH_MODE = "graph_vector"
CHAT_VECTOR_GRAPH_FULLTEXT_MODE = "graph_vector_fulltext"
# 参照追跡: graph+vector+fulltext と同じ検索に、本文中の参照（項・別表・他文書）の追跡を加えた方式（11/8）。
# 普通の検索（既存の 6 方式）では参照をたどらず、参照の追跡はこの方式に集約する
CHAT_VECTOR_GRAPH_FULLTEXT_REF_MODE = "graph_vector_fulltext_ref"
CHAT_GLOBAL_VECTOR_FULLTEXT_MODE = "global_vector"
CHAT_GRAPH_MODE = "graph"
CHAT_DEFAULT_MODE = "graph_vector_fulltext"

CHAT_MODE_CONFIG_MAP= {
        CHAT_VECTOR_MODE : {
            "retrieval_query": VECTOR_SEARCH_QUERY,
            "top_k": VECTOR_SEARCH_TOP_K,
            "index_name": "vector",
            "keyword_index": None,
            "document_filter": True,
            "node_label": "Chunk",
            "embedding_node_property":"embedding",
            "text_node_properties":["text"],

        },
        CHAT_FULLTEXT_MODE : {
            "retrieval_query": VECTOR_SEARCH_QUERY,  
            "top_k": VECTOR_SEARCH_TOP_K,
            "index_name": "vector",  
            "keyword_index": "keyword", 
            "document_filter": False,            
            "node_label": "Chunk",
            "embedding_node_property":"embedding",
            "text_node_properties":["text"],
        },
        CHAT_ENTITY_VECTOR_MODE : {
            "retrieval_query": LOCAL_COMMUNITY_SEARCH_QUERY_FORMATTED,
            "top_k": LOCAL_COMMUNITY_TOP_K,
            "index_name": "entity_vector",
            "keyword_index": None,
            "document_filter": False,            
            "node_label": "__Entity__",
            "embedding_node_property":"embedding",
            "text_node_properties":["id"],
        },
        CHAT_VECTOR_GRAPH_MODE : {
            "retrieval_query": VECTOR_GRAPH_SEARCH_QUERY,
            "top_k": VECTOR_SEARCH_TOP_K,
            "index_name": "vector",
            "keyword_index": None,
            "document_filter": True,            
            "node_label": "Chunk",
            "embedding_node_property":"embedding",
            "text_node_properties":["text"],
        },
        CHAT_VECTOR_GRAPH_FULLTEXT_MODE : {
            "retrieval_query": VECTOR_GRAPH_SEARCH_QUERY,
            "top_k": VECTOR_SEARCH_TOP_K,
            "index_name": "vector",
            "keyword_index": "keyword",
            "document_filter": False,            
            "node_label": "Chunk",
            "embedding_node_property":"embedding",
            "text_node_properties":["text"],
        },
        CHAT_VECTOR_GRAPH_FULLTEXT_REF_MODE : {
            "retrieval_query": VECTOR_GRAPH_SEARCH_QUERY,
            "top_k": VECTOR_SEARCH_TOP_K,
            "index_name": "vector",
            "keyword_index": "keyword",
            "document_filter": False,
            "node_label": "Chunk",
            "embedding_node_property":"embedding",
            "text_node_properties":["text"],
            # 方式ごとの検索設定の既定値（backend の設定より優先し、画面の上書きより劣後する）。
            # 参照の追跡を全部有効にする。選び方は X05 で効果が出た組み合わせ
            "retrieval_defaults": {
                "reference_max": 2, "reference_select": "both", "reference_depth": 2,
                "reference_documents": True, "reference_sections": True, "reference_document_layer": True,
                "reference_presentation": "inline", "reference_prompt": True,
            },
        },
        CHAT_GLOBAL_VECTOR_FULLTEXT_MODE : {
            "retrieval_query": GLOBAL_VECTOR_SEARCH_QUERY,
            "top_k": GLOBAL_SEARCH_TOP_K,
            "index_name": "community_vector",
            "keyword_index": "community_keyword",
            "document_filter": False,            
            "node_label": "__Community__",
            "embedding_node_property":"embedding",
            "text_node_properties":["summary"],
        },
    }
# 普通の検索（参照追跡以外のチャンクを検索する方式）では参照をたどらない。方式ごとの既定値として reference_max=0 を置く。
# 画面の検索設定で 1 以上にすれば、その質問だけはたどる
for _mode in (CHAT_VECTOR_MODE, CHAT_FULLTEXT_MODE, CHAT_VECTOR_GRAPH_MODE, CHAT_VECTOR_GRAPH_FULLTEXT_MODE):
    CHAT_MODE_CONFIG_MAP[_mode].setdefault("retrieval_defaults", {})["reference_max"] = 0
YOUTUBE_CHUNK_SIZE_SECONDS = 60

# 再処理するチャンクの一覧。位置の順に並べ（同じ位置は id の順）、「最後の位置から再開」で選ぶための印を添える:
# 埋め込みと HAS_ENTITY の有無、文書の処理済みのチャンク数
QUERY_TO_GET_CHUNKS = """
            MATCH (d:Document)
            WHERE d.fileName = $filename
            WITH d
            OPTIONAL MATCH (d)<-[:PART_OF|FIRST_CHUNK]-(c:Chunk)
            RETURN c.id as id, c.text as text, c.position as position,
                   c.embedding IS NOT NULL AS has_embedding, EXISTS { (c)-[:HAS_ENTITY]->() } AS has_entity,
                   coalesce(toInteger(d.processed_chunk), 0) AS processed_chunk,
                   c.id IN coalesce(d.skipped_chunk_ids, []) AS is_skipped
            ORDER BY c.position, c.id
            """
            
# 再処理の「エンティティを消して最初から」で消すエンティティ: この文書のチャンクから指され、
# 他の文書のチャンクからは指されないもの。手動で修正したエンティティ（_curated）と、規則が作った共有のエンティティ（Standard・Model など method = 'rule' のもの、文書から規則のエッジで指されるもの。C8）は消さない。
# 消すエンティティとのリレーションを失う、残るエンティティは版（_v）を 1 上げる（前の手動の操作の取り消しが、
# 消えたリレーションを作り直さないため。設計メモ 2.3）
QUERY_TO_DELETE_EXISTING_ENTITIES = """
                                MATCH (d:Document {fileName:$filename})
                                WITH d
                                MATCH (d)<-[:PART_OF]-(c:Chunk)
                                WITH d,c
                                MATCH (c)-[:HAS_ENTITY]->(e)
                                WHERE NOT EXISTS { (e)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d2:Document) WHERE d2 <> d }
                                  AND coalesce(e._curated, false) <> true
                                  AND coalesce(e.method, '') <> 'rule'
                                  AND NOT EXISTS { (:Document)-[r]->(e) WHERE r.method = 'rule' }
                                WITH collect(DISTINCT e) AS entities
                                WITH entities, apoc.map.fromPairs([e IN entities | [elementId(e), true]]) AS deleted
                                CALL (entities, deleted) {
                                    UNWIND entities AS e
                                    MATCH (e)--(k:__Entity__)
                                    WHERE deleted[elementId(k)] IS NULL
                                    WITH DISTINCT k
                                    SET k._v = coalesce(k._v, 0) + 1
                                    RETURN count(k) AS versioned
                                }
                                FOREACH (e IN entities | DETACH DELETE e)
                                RETURN size(entities) AS deleted, versioned
                                """   

# その文書のチャンクのうち、埋め込みの無い最初のもの。「最後の位置から再開」の一覧は、今は QUERY_TO_GET_CHUNKS の印で選ぶ
# （この問い合わせと次の WITHOUT_ENTITY の位置で一覧を切ると、共有のチャンクの位置の値のために未処理のチャンクが落ちる）
QUERY_TO_GET_LAST_PROCESSED_CHUNK_POSITION="""
                              MATCH (d:Document)
                              WHERE d.fileName = $filename
                              WITH d
                              MATCH (d)<-[:PART_OF]-(c:Chunk) WHERE c.embedding is null 
                              RETURN c.id as id,c.position as position 
                              ORDER BY c.position LIMIT 1
                              """   
QUERY_TO_GET_LAST_PROCESSED_CHUNK_WITHOUT_ENTITY = """
                              MATCH (d:Document)
                              WHERE d.fileName = $filename
                              WITH d
                              MATCH (d)<-[:PART_OF]-(c:Chunk) WHERE NOT exists {(c)-[:HAS_ENTITY]->()}
                              RETURN c.id as id,c.position as position 
                              ORDER BY c.position LIMIT 1
                              """
QUERY_TO_GET_NODES_AND_RELATIONS_OF_A_DOCUMENT = """
                              MATCH (d:Document)<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e) where d.fileName=$filename
                              OPTIONAL MATCH (d)<-[:PART_OF]-(:Chunk)-[:HAS_ENTITY]->(e2:!Chunk)-[rel]-(e)
                              RETURN count(DISTINCT e) as nodes, count(DISTINCT rel) as rels
                              """                              

START_FROM_BEGINNING  = "start_from_beginning"     
DELETE_ENTITIES_AND_START_FROM_BEGINNING = "delete_entities_and_start_from_beginning"
START_FROM_LAST_PROCESSED_POSITION = "start_from_last_processed_position"                                                    

GRAPH_CLEANUP_PROMPT = """
You are tasked with organizing a list of types into semantic categories based on their meanings, including synonyms or morphological similarities. The input will include two separate lists: one for **Node Labels** and one for **Relationship Types**. Follow these rules strictly:
### 1. Input Format
The input will include two keys:
- `nodes`: A list of node labels.
- `relationships`: A list of relationship types.
### 2. Grouping Rules
- Group similar items into **semantic categories** based on their meaning or morphological similarities.
- The name of each category must be chosen from the types in the input list (node labels or relationship types). **Do not create or infer new names for categories**.
- Items that cannot be grouped must remain in their own category.
### 3. Naming Rules
- The category name must reflect the grouped items and must be an existing type in the input list.
- Use a widely applicable type as the category name.
- **Do not introduce new names or types** under any circumstances.
### 4. Output Rules
- Return the output as a JSON object with two keys:
 - `nodes`: A dictionary where each key represents a category name for nodes, and its value is a list of original node labels in that category.
 - `relationships`: A dictionary where each key represents a category name for relationships, and its value is a list of original relationship types in that category.
- Every key and value must come from the provided input lists.
### 5. Examples
#### Example 1:
Input:
{{
 "nodes": ["Person", "Human", "People", "Company", "Organization", "Product"],
 "relationships": ["CREATED_FOR", "CREATED_TO", "CREATED", "PUBLISHED","PUBLISHED_BY", "PUBLISHED_IN", "PUBLISHED_ON"]
}}
Output in JSON:
{{
 "nodes": {{
   "Person": ["Person", "Human", "People"],
   "Organization": ["Company", "Organization"],
   "Product": ["Product"]
 }},
 "relationships": {{
   "CREATED": ["CREATED_FOR", "CREATED_TO", "CREATED"],
   "PUBLISHED": ["PUBLISHED_BY", "PUBLISHED_IN", "PUBLISHED_ON"]
 }}
}}
#### Example 2: Avoid redundant or incorrect grouping
Input:
{{
 "nodes": ["Process", "Process_Step", "Step", "Procedure", "Method", "Natural Process", "Step"],
 "relationships": ["USED_FOR", "USED_BY", "USED_WITH", "USED_IN"]
}}
Output:
{{
 "nodes": {{
   "Process": ["Process", "Process_Step", "Step", "Procedure", "Method", "Natural Process"]
 }},
 "relationships": {{
   "USED": ["USED_FOR", "USED_BY", "USED_WITH", "USED_IN"]
 }}
}}
### 6. Key Rule
If any item cannot be grouped, it must remain in its own category using its original name. Do not repeat values or create incorrect mappings.
Use these rules to group and name categories accurately without introducing errors or new types.
"""

ADDITIONAL_INSTRUCTIONS = """Your goal is to identify and categorize entities while ensuring that specific data 
types such as dates, numbers, revenues, and other non-entity information are not extracted as separate nodes.
Instead, treat these as properties associated with the relevant entities."""

SCHEMA_VISUALIZATION_QUERY = """
CALL db.schema.visualization() YIELD nodes, relationships
RETURN
  [n IN nodes | {
      element_id: elementId(n),
      labels: labels(n),
      properties: apoc.any.properties(n)
  }] AS nodes,
  [r IN relationships | {
      type: type(r),
      properties: apoc.any.properties(r),
      element_id: elementId(r),
      start_node_element_id: elementId(startNode(r)),
      end_node_element_id: elementId(endNode(r))
  }] AS relationships;
"""
