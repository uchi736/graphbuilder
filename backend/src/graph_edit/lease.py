"""グラフの貸し出し（lease、設計メモ 2.6）。

編集の API は排他のノード（__GraphLock__ {name:'graph'}）の書き込み鍵で直列になる。複数の自動コミットに分かれて
グラフを書き換える処理（取り込みの保存 A2、文書の削除 B2、再処理の削除 B1、スキーマの統合、コミュニティの作成、
取り込み直しの削除、文書層・章節の層の作成）は、
その間だけ貸し出しを取り、編集の API を BUSY で待たせる。

- acquire_lease: 取れるまで待つ（既定で最大 LEASE_MAX_WAIT_SECONDS 秒。60 回で諦めない）
- renew_lease: 期限を延ばす（各段の区切りで呼ぶ）
- hold_lease / LeaseHeartbeat: 持っている間、別のスレッドで LEASE_HEARTBEAT_SECONDS 秒おきに延長し続ける
  （1 つの問い合わせが LEASE_MS を超えても切れない。プロセスが落ちれば延長が止まり、LEASE_MS で切れる）
- run_with_lease: 関数を貸し出しを持ったまま動かす（文書層・章節の層の作成、取り込み直しの削除。asyncio.to_thread で呼ぶ）
- release_lease: 返す
"""
import logging
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

from src.graph_edit.ledger import ensure_schema
from src.graph_edit.validate import GRAPH_LOCK_LABEL
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException

LEASE_MS = 120000
LEASE_INTERVAL_SECONDS = 1.0
# 取れないときに待つ上限（他の取り込みの保存や文書の削除、コミュニティの作成が終わるのを待つ）
LEASE_MAX_WAIT_SECONDS = 3600
# 持っている間の延長の間隔と、延長し続ける上限（固まった処理が編集を止め続けないため）
LEASE_HEARTBEAT_SECONDS = 30.0
LEASE_MAX_HOLD_SECONDS = 4 * 3600
LEASE_LOG_EVERY = 60

# 貸し出し: 先に排他のノードの書き込み鍵を取ってから、空いているかを読む（読んでから書くと、同時に来た 2 つが両方取れる）
LEASE_QUERY = f"""
MATCH (l:{GRAPH_LOCK_LABEL} {{name: 'graph'}})
SET l.lease_probe = timestamp()
WITH l, coalesce(l.lease_until, 0) < timestamp() AS free
SET l.lease_owner = CASE WHEN free THEN $owner ELSE l.lease_owner END,
    l.lease_until = CASE WHEN free THEN timestamp() + $ms ELSE l.lease_until END
RETURN free AS acquired, l.lease_owner AS owner, l.lease_until AS until
"""
RENEW_QUERY = (f"MATCH (l:{GRAPH_LOCK_LABEL} {{name: 'graph'}}) WHERE l.lease_owner = $owner "
               "SET l.lease_until = timestamp() + $ms RETURN count(l) AS n")
RELEASE_QUERY = (f"MATCH (l:{GRAPH_LOCK_LABEL} {{name: 'graph'}}) WHERE l.lease_owner = $owner "
                 "SET l.lease_until = 0, l.lease_owner = null")


def acquire_lease(graph, owner, attempts=None, interval=LEASE_INTERVAL_SECONDS):
    """貸し出しを取る。取れるまで interval 秒おきに待つ（attempts を省くと LEASE_MAX_WAIT_SECONDS 秒まで）。
    返す値（token）を renew_lease・release_lease に渡す。"""
    ensure_schema(graph)
    if attempts is None:
        attempts = max(1, int(LEASE_MAX_WAIT_SECONDS / interval)) if interval > 0 else 1
    token = f"{owner}#{uuid4().hex[:12]}"
    holder = None
    for attempt in range(attempts):
        rows = graph.query(LEASE_QUERY, {"owner": token, "ms": LEASE_MS})
        if rows and rows[0]["acquired"]:
            if attempt:
                logging.info(f"graph_edit: lease acquired by {token} after {attempt} retries")
            return token
        holder = rows[0]["owner"] if rows else None
        if attempt and attempt % LEASE_LOG_EVERY == 0:
            logging.info(f"graph_edit: {owner} is waiting for the graph lease held by {holder} ({attempt} retries)")
        time.sleep(interval)
    raise LLMGraphBuilderException(f"Could not acquire the graph lease for {owner} (held by {holder})")


def renew_lease(graph, token):
    """期限を延ばす。延ばせたら True（期限が切れて他に取られていたら False。警告を残す）。"""
    if not token:
        return False
    rows = graph.query(RENEW_QUERY, {"owner": token, "ms": LEASE_MS})
    renewed = bool(rows and rows[0]["n"])
    if not renewed:
        logging.warning(f"graph_edit: the lease {token} had expired and could not be renewed")
    return renewed


def release_lease(graph, token):
    """貸し出しを返す。返せなくても（接続が切れたなど）例外にしない（処理の例外を隠さない。貸し出しは LEASE_MS で切れる）。"""
    if not token:
        return
    try:
        graph.query(RELEASE_QUERY, {"owner": token})
    except Exception as e:
        logging.warning(f"graph_edit: could not release the lease {token} ({e}); it expires in {LEASE_MS // 1000} seconds")


class LeaseHeartbeat:
    """持っている間、別のスレッドで貸し出しを延長し続ける。stop() で止める（release の前に呼ぶ）。"""

    def __init__(self, graph, token, interval=LEASE_HEARTBEAT_SECONDS, max_hold=LEASE_MAX_HOLD_SECONDS):
        self.graph = graph
        self.token = token
        self.interval = interval
        self.max_hold = max_hold
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"lease-heartbeat-{token}", daemon=True)

    def start(self):
        if self.token:
            self._thread.start()
        return self

    def _run(self):
        started = time.monotonic()
        while not self._stop.wait(self.interval):
            if time.monotonic() - started > self.max_hold:
                logging.warning(f"graph_edit: stopped renewing the lease {self.token} after {self.max_hold} seconds")
                return
            try:
                if not renew_lease(self.graph, self.token):
                    return
            except Exception as e:  # 一時的な接続の切れ目では止めない（次の延長で取り戻す）
                logging.warning(f"graph_edit: could not renew the lease {self.token}: {e}")

    def stop(self):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)


def start_heartbeat(graph, token, interval=LEASE_HEARTBEAT_SECONDS):
    return LeaseHeartbeat(graph, token, interval).start()


@contextmanager
def hold_lease(graph, owner, heartbeat=LEASE_HEARTBEAT_SECONDS, attempts=None, interval=LEASE_INTERVAL_SECONDS):
    """with hold_lease(graph, "delete:a.pdf") as token: ... の間、貸し出しを持ち、延長し続ける。"""
    token = acquire_lease(graph, owner, attempts=attempts, interval=interval)
    beat = start_heartbeat(graph, token, heartbeat)
    try:
        yield token
    finally:
        beat.stop()
        release_lease(graph, token)


def run_with_lease(graph, owner, func, *args, **kwargs):
    """func(*args, **kwargs) を貸し出しを持ったまま動かす（後処理の入口から asyncio.to_thread で呼ぶ）。
    文書層・章節の層の作成のように、複数の自動コミットでグラフを書き換える処理に使う。"""
    with hold_lease(graph, owner):
        return func(*args, **kwargs)


class QueryAdapter:
    """graph.query(query, params) の形を持たない接続（GraphDataScience）を、貸し出しの関数に渡すための薄い包み。"""

    def __init__(self, run_cypher):
        self._run_cypher = run_cypher

    def query(self, query, params=None, session_params=None):
        result = self._run_cypher(query, params or {})
        if hasattr(result, "to_dict"):
            return result.to_dict(orient="records")
        return list(result or [])
