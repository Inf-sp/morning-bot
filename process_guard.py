"""Exclusive process lease for Telegram polling and scheduled jobs."""

import fcntl
import logging
import os
import socket
import time
from datetime import datetime, timezone

_log = logging.getLogger(__name__)
_LOCAL_LOCK_PATH = "/tmp/morning-bot-telegram-polling.lock"


def process_identity(started_at=None):
    return {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "started_at": started_at or datetime.now(timezone.utc).isoformat(),
        "deployment": os.environ.get("RAILWAY_DEPLOYMENT_ID", "") or "local",
        "replica": os.environ.get("RAILWAY_REPLICA_ID", "") or "local",
    }


class PollingLease:
    """Один владелец polling на машине: файловая блокировка живёт, пока жив процесс.

    Бот и его SQLite-файл работают на одной VM, поэтому блокировки файла достаточно:
    второй случайно запущенный процесс ждёт, пока первый не завершится.
    """

    def __init__(self, path=_LOCAL_LOCK_PATH):
        self._path = path
        self._file = None

    def acquire(self, wait_seconds=60, retry_seconds=1):
        deadline = time.monotonic() + max(0, wait_seconds)
        logged_wait = False
        while True:
            if self._try_acquire():
                _log.info("Polling lease acquired path=%s", self._path)
                return True
            if time.monotonic() >= deadline:
                _log.error("Polling lease unavailable after %ss", wait_seconds)
                return False
            if not logged_wait:
                _log.info("Polling lease held by another process; waiting for handover")
                logged_wait = True
            time.sleep(max(0.1, retry_seconds))

    def _try_acquire(self):
        lock_file = open(self._path, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return False
        self._file = lock_file
        return True

    def release(self):
        if self._file is None:
            return
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
        _log.info("Polling lease released path=%s", self._path)
