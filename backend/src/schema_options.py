# -*- coding: utf-8 -*-
"""グラフスキーマ設定欄（グラフ設定の「取り込み」タブ、標準抽出用）の選択肢の件数。参照のみ。

ラベルごとのノード数と、リレーションタイプごとのリレーション数を返す。選択肢の名称の一覧は POST /graph_edit/vocabulary が返し、
画面はその名称にこの件数を付与して表示する。対象から除外するラベルとリレーションタイプは vocabulary（src/graph_edit/read.py）と同じ規則とする。
件数は名称ごとに MATCH (n:`ラベル`) RETURN count(n) の形で照会する（Neo4j は件数の統計で応答し、全件を走査しない）。
登録: score.py で app.include_router(router)。POST /schema_options（資格情報は他の照会と同じくフォームで受ける）
接続: create_graph_database_connection（langchain の Neo4jGraph）を使用する。画面が接続を backend の設定に任せた場合（ユーザー名と
パスワードを送らない）は、Neo4jGraph が環境変数 NEO4J_URI・NEO4J_USERNAME・NEO4J_PASSWORD・NEO4J_DATABASE で補う。
照会は読み取りのトランザクションで実行し、書き込みは行わない。
"""
import asyncio
import logging

from fastapi import APIRouter, Depends
from neo4j import unit_of_work

from src.api_response import create_api_response
from src.entities.user_credential import Neo4jCredentials, get_neo4j_credentials
from src.graph_edit.validate import (
    DOCUMENT_LABEL, ENTITY_LABEL, LABEL_PATTERN, RELATIONSHIP_TYPE_PATTERN, RESERVED_LABELS, RESERVED_RELATIONSHIP_TYPES,
)
from src.shared.common_fn import create_graph_database_connection

COUNT_TIMEOUT_SECONDS = 20


def usable_label(label):
    """vocabulary と同じ規則: 予約ラベル・__Entity__・Document を除外し、名称の形が正しいラベルのみを対象とする。
    名称の形は名称全体で判定する（re.fullmatch。規則の末尾の $ は末尾の改行を許すため、match は使用しない）。"""
    return (label not in RESERVED_LABELS and label not in (ENTITY_LABEL, DOCUMENT_LABEL)
            and LABEL_PATTERN.fullmatch(label) is not None)


def usable_type(rel_type):
    """vocabulary と同じ規則: 予約リレーションタイプを除外し、名称の形が正しいリレーションタイプのみを対象とする（名称全体で判定する）。"""
    return rel_type not in RESERVED_RELATIONSHIP_TYPES and RELATIONSHIP_TYPE_PATTERN.fullmatch(rel_type) is not None


def _quote(name):
    """Cypher の識別子として引用する（バッククォートは二重にする）。
    「\\」を含む名称は拒否する。Neo4j はバッククォートで囲んだ識別子の中の Unicode エスケープ（\\u0060）をバッククォートとして
    解釈するため、バッククォートの二重化だけでは引用の外に出られる。照会する名称は usable_label・usable_type で絞るため、通常は発生しない。"""
    if "\\" in name:
        raise ValueError("名称に「\\」を含むため、Cypher の識別子として引用できない")
    return "`" + name.replace("`", "``") + "`"


def count_schema_options(tx):
    """読み取りのトランザクションの中で、対象のラベルとリレーションタイプの件数を集計する。名称の昇順で返す。"""
    labels = [r["label"] for r in tx.run("CALL db.labels() YIELD label RETURN label")]
    rel_types = [r["t"] for r in tx.run("CALL db.relationshipTypes() YIELD relationshipType AS t RETURN t")]
    label_counts = [{"name": label, "count": tx.run(f"MATCH (n:{_quote(label)}) RETURN count(n) AS c").single()["c"]}
                    for label in sorted(name for name in labels if usable_label(name))]
    type_counts = [{"name": rel_type,
                    "count": tx.run(f"MATCH ()-[r:{_quote(rel_type)}]->() RETURN count(r) AS c").single()["c"]}
                   for rel_type in sorted(name for name in rel_types if usable_type(name))]
    return {"labels": label_counts, "relationship_types": type_counts}


@unit_of_work(timeout=COUNT_TIMEOUT_SECONDS)
def _count_with_timeout(tx):
    return count_schema_options(tx)


def collect_schema_options(credentials):
    """接続を作成して件数を集計し、接続を閉じる。資格情報の空の値は Neo4jGraph が環境変数で補う。"""
    graph = create_graph_database_connection(credentials)
    try:
        with graph._driver.session(database=graph._database) as session:
            return session.execute_read(_count_with_timeout)
    finally:
        try:
            graph._driver.close()
        except Exception:
            pass


router = APIRouter()


@router.post("/schema_options")
async def schema_options(credentials: Neo4jCredentials = Depends(get_neo4j_credentials)):
    """グラフスキーマ設定欄の選択肢の件数（参照のみ）。失敗した場合も例外は送出せず、Failed の応答を返す。"""
    try:
        data = await asyncio.to_thread(collect_schema_options, credentials)
        logging.info("schema_options: %d labels, %d relationship types", len(data["labels"]), len(data["relationship_types"]))
        return create_api_response("Success", data=data)
    except Exception as e:
        logging.exception("schema_options failed")
        return create_api_response("Failed", message="グラフスキーマの選択肢の件数を取得できませんでした", error=str(e))
