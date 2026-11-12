"""router.py の /graph_edit/* の入口から呼ぶ共通の処理。

応答は create_api_response('Success'|'Failed', data=..., message=..., error=...)（HTTP は常に 200）。
失敗の error は英語のコード（NOT_FOUND など）で始め、": " の後に詳細を付ける。コードの無い失敗は詳細だけを返す（画面は UNKNOWN に写す）。
"""
import asyncio
import gc
import logging
import os
import time
from datetime import datetime, timezone

from src.api_response import create_api_response
from src.graph_edit.validate import EditError
from src.logger import CustomLogger
from src.shared.common_fn import create_graph_database_connection, formatted_time

logger = CustomLogger()


def default_user(user, credentials):
    """操作の履歴の user。画面が送る値（接続画面のユーザー名かメール）、無ければ接続のユーザー名かメール。
    画面がバックエンドの環境変数で接続したとき（connection が backendApi）は、画面はユーザー名を送らず、
    接続は環境変数 NEO4J_USERNAME で張られる（Neo4jGraph の既定）。ログインのメールも無ければ、実際に使った接続のユーザー名を残す。"""
    text = (user or "").strip() if isinstance(user, str) else ""
    env_user = ""
    if not (credentials.userName or "").strip() and not (credentials.password or ""):
        env_user = (os.environ.get("NEO4J_USERNAME") or "").strip()
    return text or (credentials.userName or "").strip() or (credentials.email or "").strip() or env_user or "unknown"


async def call(api_name, credentials, func, **kwargs):
    """func(graph, **kwargs) を別のスレッドで動かし、応答の形に直す。"""
    start = time.time()
    graph = None
    try:
        graph = create_graph_database_connection(credentials)
        data = await asyncio.to_thread(func, graph, **kwargs)
        logger.log_struct({"api_name": f"graph_edit/{api_name}", "db_url": credentials.uri, "userName": credentials.userName,
                           "database": credentials.database, "logging_time": formatted_time(datetime.now(timezone.utc)),
                           "elapsed_api_time": f"{time.time() - start:.2f}", "email": credentials.email}, "INFO")
        return create_api_response("Success", data=data)
    except EditError as e:
        logging.info(f"graph_edit/{api_name}: {e.error_text()}")
        return create_api_response("Failed", data=e.data, message=f"Unable to run graph_edit/{api_name}", error=e.error_text())
    except Exception as e:
        logging.exception(f"Exception in graph_edit/{api_name}: {e}")
        return create_api_response("Failed", message=f"Unable to run graph_edit/{api_name}", error=str(e))
    finally:
        if graph is not None:
            try:
                graph._driver.close()
            except Exception:
                pass
        gc.collect()
