"""接続先（Nemotron Parse）ごとの同時数の上限と、応答しない接続先を一時的に呼ばない遮断。

同時数の上限は 2 段で掛ける。
    プロセスの中  接続先ごとに 1 つのセマフォ（同じプロセスで同時に走る変換どうしで共有する）
    プロセスの間  接続先ごとに上限数ぶんのロック用ファイル（PDF_REGIONS_LOCK_DIR、既定は一時フォルダの pdf_regions_slots）を置き、
                  fcntl.flock で 1 つを取ってから送る。同じコンテナの gunicorn の作業プロセスどうしで共有する。
                  プロセスが落ちるとロックは OS が外す。フォルダを作れないときはプロセスの中の上限だけで続ける
上限はこの変換器からの要求だけに掛かる（同じ接続先を使う他のアプリの要求は数えない）。

遮断: 時間切れ・接続の失敗・5xx が続けて FAILURES_TO_OPEN 回起きた接続先は、COOLDOWN 秒のあいだ呼ばずにすぐ失敗にする
（ハングした vLLM は復帰に数分かかる）。プロセスの中で共有する。
"""
from __future__ import annotations

import errno
import hashlib
import logging
import os
import tempfile
import threading
import time
from typing import Optional

FAILURES_TO_OPEN = 2
COOLDOWN = 300.0
POLL = 0.1


class EndpointUnavailable(RuntimeError):
    """遮断中の接続先、または締め切りまでに順番が回ってこなかった"""


def remaining(deadline: Optional[float]) -> Optional[float]:
    return None if deadline is None else deadline - time.time()


class _FileSlots:
    """プロセスをまたぐ上限。上限数ぶんのファイルのどれか 1 つに排他のロックを取る"""

    def __init__(self, url: str, limit: int, directory: str):
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        self.paths = [os.path.join(directory, f"{key}-{k}.lock") for k in range(limit)]

    def acquire(self, deadline: Optional[float]) -> int:
        import fcntl
        while True:
            for path in self.paths:
                fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return fd
                except OSError as error:
                    os.close(fd)
                    if error.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                        raise
            left = remaining(deadline)
            if left is not None and left <= 0:
                raise EndpointUnavailable("no free slot before the deadline")
            time.sleep(POLL if left is None else max(0.0, min(POLL, left)))

    @staticmethod
    def release(fd: int) -> None:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class Endpoint:
    """1 つの接続先の上限と遮断"""

    def __init__(self, url: str, limit: int, lock_dir: Optional[str]):
        self.url = url
        self._sem = threading.BoundedSemaphore(limit)
        self._files = _make_slots(url, limit, lock_dir)
        self._state = threading.Lock()
        self._failures = 0
        self._open_until = 0.0

    # ---- 遮断 ----
    def check(self) -> None:
        with self._state:
            if time.time() < self._open_until:
                raise EndpointUnavailable(f"{self.url} is skipped for {self._open_until - time.time():.0f}s "
                                          f"after {FAILURES_TO_OPEN} consecutive failures")

    def success(self) -> None:
        with self._state:
            self._failures = 0
            self._open_until = 0.0

    def failure(self) -> None:
        with self._state:
            self._failures += 1
            if self._failures >= FAILURES_TO_OPEN:
                self._open_until = time.time() + COOLDOWN
                logging.warning("pdf_regions: %s did not answer %d times in a row; not calling it for %.0fs",
                                self.url, self._failures, COOLDOWN)

    # ---- 同時数の上限 ----
    def slot(self, deadline: Optional[float] = None):
        return _Slot(self, deadline)


class _Slot:
    def __init__(self, endpoint: Endpoint, deadline: Optional[float]):
        self.endpoint = endpoint
        self.deadline = deadline
        self.fd = None

    def __enter__(self):
        ep = self.endpoint
        ep.check()
        left = remaining(self.deadline)
        if left is not None and left <= 0:
            raise EndpointUnavailable("the document deadline has passed")
        if not ep._sem.acquire(timeout=left):
            raise EndpointUnavailable("no free slot before the deadline")
        try:
            if ep._files is not None:
                self.fd = ep._files.acquire(self.deadline)
            ep.check()   # 待っている間に遮断されたら送らない
        except BaseException:
            if self.fd is not None:
                _FileSlots.release(self.fd)
                self.fd = None
            ep._sem.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            if self.fd is not None:
                _FileSlots.release(self.fd)
        finally:
            self.endpoint._sem.release()
        return False


def default_lock_dir() -> str:
    return os.environ.get("PDF_REGIONS_LOCK_DIR") or os.path.join(tempfile.gettempdir(), "pdf_regions_slots")


def _make_slots(url: str, limit: int, lock_dir: Optional[str]):
    if lock_dir == "":
        return None
    directory = lock_dir or default_lock_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        try:
            os.chmod(directory, 0o777)
        except OSError:
            pass
        probe = _FileSlots(url, limit, directory)
        fd = os.open(probe.paths[0], os.O_RDWR | os.O_CREAT, 0o666)
        os.close(fd)
        return probe
    except Exception as error:   # 書けないフォルダ・fcntl の無い環境
        logging.warning("pdf_regions: cannot use %s for the cross-process limit (%s); limiting within this process only",
                        directory, error)
        return None


_ENDPOINTS = {}
_LOCK = threading.Lock()


def endpoint(url: str, limit: int, lock_dir: Optional[str] = None) -> Endpoint:
    """接続先ごとに 1 つ（同じプロセスの中で共有する）。上限は最初に作ったときの値"""
    url = url.rstrip("/")
    limit = max(1, int(limit))
    with _LOCK:
        ep = _ENDPOINTS.get(url)
        if ep is None:
            ep = _ENDPOINTS[url] = Endpoint(url, limit, lock_dir)
        return ep


def is_unavailable_error(error: BaseException) -> bool:
    """接続先が応答しない・落ちている（遮断に数える）失敗か。時間切れ・接続の失敗・5xx"""
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status is not None:
        return status >= 500
    return not isinstance(error, (ValueError, TypeError, KeyError))


def is_retryable(error: BaseException) -> bool:
    """送り直してよい失敗か。接続の失敗・5xx・429 だけ。応答の時間切れは送り直さない（相手が止まっていると待ちが積み重なる）"""
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status is not None:
        return status >= 500 or status == 429
    if is_read_timeout(error):
        return False
    return not isinstance(error, (ValueError, TypeError, KeyError))


def is_read_timeout(error: BaseException) -> bool:
    try:
        import httpx
    except Exception:   # pragma: no cover
        return isinstance(error, TimeoutError)
    if isinstance(error, httpx.ConnectTimeout):
        return False
    return isinstance(error, (httpx.TimeoutException, TimeoutError))
