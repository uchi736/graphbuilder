# -*- coding: utf-8 -*-
"""文書台帳の正本の表（人が直す表。Postgres の document_registry）を読む。

文書層の後処理（document_layer.py の build_document_layer）が、表があれば読み、表の値をグラフの値（文書から取り出した値）より優先して
文書番号のノード（:Standard）に写す。表に無い文書は従来どおり文書から作る。グラフを作り直しても、人が表に書いた値は消えない。

設定（環境変数。既定はどれも空で、表を使わない）:
    DOCUMENT_REGISTRY_DSN    接続先（postgresql://<利用者>:<パスワード>@<ホスト>:<ポート>/<データベース>）。空なら表を使わない
    DOCUMENT_REGISTRY_TABLE  表の名前（既定 document_registry。スキーマ付き「registry.document_registry」も可）

表が無い・つながらない・ドライバが無いときは、警告を出して従来どおり動く（load_registry は (None, 理由) を返す）。
接続文字列やパスワードは、ログにも戻り値にも出さない。

表の形（tools/kg/registry/schema.sql が正本）:
    doc_number text 主キー（文書番号の正規形）/ official_name 正式名称 / kind 種類 / department 所管部門 / current_rev 現行版の符号 /
    aliases text[] 別名 / parent_doc 上位文書の文書番号 / note 備考 / updated_by 更新者 / updated_at 更新日時
"""
import logging
import os
import re
import urllib.parse

DEFAULT_TABLE = "document_registry"
COLUMNS = ["doc_number", "official_name", "kind", "department", "current_rev", "aliases", "parent_doc", "note", "updated_by", "updated_at"]


def registry_dsn():
    return (os.environ.get("DOCUMENT_REGISTRY_DSN") or "").strip()


def registry_table():
    t = (os.environ.get("DOCUMENT_REGISTRY_TABLE") or DEFAULT_TABLE).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", t):
        raise ValueError("DOCUMENT_REGISTRY_TABLE は英数字と下線（スキーマ付きは 1 つの点）だけにする")
    return t


def _redact(message, dsn):
    """例外の文から接続文字列とパスワードを消す。"""
    msg = str(message or "")
    if dsn:
        msg = msg.replace(dsn, "<dsn>")
        pw = urllib.parse.urlsplit(dsn).password
        if pw:
            msg = msg.replace(pw, "***")
    msg = re.sub(r"password\s*=\s*\S+", "password=***", msg, flags=re.I)
    return msg.splitlines()[0][:160] if msg else ""


def _connect(dsn, timeout=3):
    """入っているドライバでつなぐ（psycopg 3 → psycopg2 → pg8000 の順）。どれも無ければ ImportError。"""
    try:
        import psycopg  # noqa: F401
        return psycopg.connect(dsn, connect_timeout=timeout)
    except ImportError:
        pass
    try:
        import psycopg2
        return psycopg2.connect(dsn, connect_timeout=timeout)
    except ImportError:
        pass
    try:
        import pg8000.dbapi
    except ImportError:
        raise ImportError("Postgres のドライバ（psycopg・psycopg2・pg8000 のどれか）が backend のイメージに無い")
    u = urllib.parse.urlsplit(dsn)
    return pg8000.dbapi.connect(user=urllib.parse.unquote(u.username or ""), password=urllib.parse.unquote(u.password or ""),
                                host=u.hostname or "localhost", port=u.port or 5432, database=(u.path or "/").lstrip("/") or None, timeout=timeout)


def load_registry(dsn=None, table=None, connect=None):
    """正本の表の全行を読む。戻り値 (行の一覧 または None, 状態の文)。状態は "off"（設定が空）/ "ok: N rows" / "unavailable: 理由"。
    connect は試験用（引数なしで DB-API の接続を返す関数）。"""
    dsn = registry_dsn() if dsn is None else dsn
    if not dsn and connect is None:
        return None, "off"
    try:
        table = table or registry_table()
        conn = connect() if connect else _connect(dsn)
        try:
            cur = conn.cursor()
            cur.execute("SELECT %s FROM %s" % (", ".join(COLUMNS), table))
            rows = [dict(zip(COLUMNS, r)) for r in cur.fetchall()]
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:  # 表が無い・つながらない・ドライバが無い: 従来どおり文書だけで作る
        status = "unavailable: %s: %s" % (type(e).__name__, _redact(e, dsn))
        logging.warning("document registry %s", status)
        return None, status
    for r in rows:
        r["aliases"] = [a for a in (r.get("aliases") or []) if a and str(a).strip()]
        if r.get("updated_at") is not None:
            r["updated_at"] = str(r["updated_at"])
    return rows, "ok: %d rows" % len(rows)
