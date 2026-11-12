"""グラフの手動修正の入口（docs/graph-edit/DESIGN.md の 3 章）。score.py は app.include_router(router) の 1 行で取り込む。

処理は src/graph_edit の各モジュールに置き、ここは Form の値を渡すだけ。応答の形は api.call が作る。
"""
from fastapi import APIRouter, Depends, Form

from src.entities.user_credential import Neo4jCredentials, get_neo4j_credentials
from src.graph_edit import api as graph_edit_api, ops as graph_edit_ops, read as graph_edit_read

router = APIRouter()


@router.post("/graph_edit/node_detail")
async def graph_edit_node_detail(credentials: Neo4jCredentials = Depends(get_neo4j_credentials), element_id=Form(None)):
    return await graph_edit_api.call("node_detail", credentials, graph_edit_read.node_detail, element_id=element_id)


@router.post("/graph_edit/search_entities")
async def graph_edit_search_entities(credentials: Neo4jCredentials = Depends(get_neo4j_credentials), q=Form(None),
                                     limit=Form(None)):
    return await graph_edit_api.call("search_entities", credentials, graph_edit_read.search_entities, q=q, limit=limit)


@router.post("/graph_edit/neighborhood")
async def graph_edit_neighborhood(credentials: Neo4jCredentials = Depends(get_neo4j_credentials), element_id=Form(None)):
    return await graph_edit_api.call("neighborhood", credentials, graph_edit_read.neighborhood, element_id=element_id)


@router.post("/graph_edit/vocabulary")
async def graph_edit_vocabulary(credentials: Neo4jCredentials = Depends(get_neo4j_credentials)):
    return await graph_edit_api.call("vocabulary", credentials, graph_edit_read.vocabulary)


@router.post("/graph_edit/rename")
async def graph_edit_rename(credentials: Neo4jCredentials = Depends(get_neo4j_credentials), element_id=Form(None),
                            expected_id=Form(None), new_id=Form(None), user=Form(None)):
    return await graph_edit_api.call("rename", credentials, graph_edit_ops.rename, element_id=element_id,
                                     expected_id=expected_id, new_id=new_id,
                                     user=graph_edit_api.default_user(user, credentials))


@router.post("/graph_edit/set_labels")
async def graph_edit_set_labels(credentials: Neo4jCredentials = Depends(get_neo4j_credentials), element_id=Form(None),
                                expected_id=Form(None), labels=Form(None), user=Form(None)):
    return await graph_edit_api.call("set_labels", credentials, graph_edit_ops.set_labels, element_id=element_id,
                                     expected_id=expected_id, labels=labels,
                                     user=graph_edit_api.default_user(user, credentials))


@router.post("/graph_edit/update_props")
async def graph_edit_update_props(credentials: Neo4jCredentials = Depends(get_neo4j_credentials), element_id=Form(None),
                                  expected_id=Form(None), props=Form(None), user=Form(None)):
    return await graph_edit_api.call("update_props", credentials, graph_edit_ops.update_props, element_id=element_id,
                                     expected_id=expected_id, props=props,
                                     user=graph_edit_api.default_user(user, credentials))


@router.post("/graph_edit/merge")
async def graph_edit_merge(credentials: Neo4jCredentials = Depends(get_neo4j_credentials),
                           target_element_id=Form(None), target_expected_id=Form(None), sources=Form(None),
                           keep_id=Form(None), labels=Form(None), description_mode=Form(None), description=Form(None),
                           user=Form(None)):
    return await graph_edit_api.call("merge", credentials, graph_edit_ops.merge, target_element_id=target_element_id,
                                     target_expected_id=target_expected_id, sources=sources, keep_id=keep_id,
                                     labels=labels, description_mode=description_mode, description=description,
                                     user=graph_edit_api.default_user(user, credentials))


@router.post("/graph_edit/delete_nodes")
async def graph_edit_delete_nodes(credentials: Neo4jCredentials = Depends(get_neo4j_credentials), nodes=Form(None),
                                  suppress=Form(None), user=Form(None)):
    return await graph_edit_api.call("delete_nodes", credentials, graph_edit_ops.delete_nodes, nodes=nodes,
                                     suppress=suppress, user=graph_edit_api.default_user(user, credentials))


@router.post("/graph_edit/create_relationship")
async def graph_edit_create_relationship(credentials: Neo4jCredentials = Depends(get_neo4j_credentials),
                                         source_element_id=Form(None), source_expected_id=Form(None),
                                         target_element_id=Form(None), target_expected_id=Form(None), type=Form(None),
                                         props=Form(None), user=Form(None)):
    return await graph_edit_api.call("create_relationship", credentials, graph_edit_ops.create_relationship,
                                     source_element_id=source_element_id, source_expected_id=source_expected_id,
                                     target_element_id=target_element_id, target_expected_id=target_expected_id,
                                     type=type, props=props, user=graph_edit_api.default_user(user, credentials))


@router.post("/graph_edit/delete_relationship")
async def graph_edit_delete_relationship(credentials: Neo4jCredentials = Depends(get_neo4j_credentials),
                                         element_id=Form(None), expected=Form(None), suppress=Form(None),
                                         user=Form(None)):
    return await graph_edit_api.call("delete_relationship", credentials, graph_edit_ops.delete_relationship,
                                     element_id=element_id, expected=expected, suppress=suppress,
                                     user=graph_edit_api.default_user(user, credentials))


@router.post("/graph_edit/update_relationship")
async def graph_edit_update_relationship(credentials: Neo4jCredentials = Depends(get_neo4j_credentials),
                                         element_id=Form(None), expected=Form(None), new_type=Form(None),
                                         reverse=Form(None), props=Form(None), user=Form(None)):
    return await graph_edit_api.call("update_relationship", credentials, graph_edit_ops.update_relationship,
                                     element_id=element_id, expected=expected, new_type=new_type, reverse=reverse,
                                     props=props, user=graph_edit_api.default_user(user, credentials))


@router.post("/graph_edit/history")
async def graph_edit_history(credentials: Neo4jCredentials = Depends(get_neo4j_credentials), limit=Form(None),
                             before_seq=Form(None)):
    return await graph_edit_api.call("history", credentials, graph_edit_ops.history_rows, limit=limit,
                                     before_seq=before_seq)


@router.post("/graph_edit/undo")
async def graph_edit_undo(credentials: Neo4jCredentials = Depends(get_neo4j_credentials), op_id=Form(None),
                          user=Form(None)):
    return await graph_edit_api.call("undo", credentials, graph_edit_ops.undo, op_id=op_id,
                                     user=graph_edit_api.default_user(user, credentials))


@router.post("/graph_edit/ledger")
async def graph_edit_ledger(credentials: Neo4jCredentials = Depends(get_neo4j_credentials), kind=Form(None),
                            limit=Form(None), offset=Form(None)):
    return await graph_edit_api.call("ledger", credentials, graph_edit_read.ledger, kind=kind, limit=limit, offset=offset)


@router.post("/graph_edit/ledger_delete")
async def graph_edit_ledger_delete(credentials: Neo4jCredentials = Depends(get_neo4j_credentials), uid=Form(None),
                                   user=Form(None)):
    return await graph_edit_api.call("ledger_delete", credentials, graph_edit_ops.ledger_delete, uid=uid,
                                     user=graph_edit_api.default_user(user, credentials))


@router.post("/graph_edit/mark_distinct")
async def graph_edit_mark_distinct(credentials: Neo4jCredentials = Depends(get_neo4j_credentials),
                                   a_element_id=Form(None), b_element_id=Form(None), user=Form(None)):
    return await graph_edit_api.call("mark_distinct", credentials, graph_edit_ops.mark_distinct,
                                     a_element_id=a_element_id, b_element_id=b_element_id,
                                     user=graph_edit_api.default_user(user, credentials))
