"""Exclusive process lease for Telegram polling and scheduled jobs."""

import logging
import os
import socket
import time
from datetime import datetime, timezone

import config

_log = logging.getLogger(__name__)
_LOCK_KEY = "morning-bot:telegram-polling"
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
    """Holds one polling owner across Railway containers when Postgres is available.

    A dedicated PostgreSQL session owns the advisory lock for the whole process.
    The local file lock remains a safe fallback for accidental duplicate starts in
    one container; it cannot coordinate separate Railway containers.
    """

    def __init__(self):
        self._connection = None
        self._local_file = None
        self.backend = ""

    def acquire(self, wait_seconds=60, retry_seconds=1):
        deadline = time.monotonic() + max(0, wait_seconds)
        logged_wait = False
        while True:
            if self._try_acquire():
                _log.info("Polling lease acquired backend=%s", self.backend)
                return True
            if time.monotonic() >= deadline:
                _log.error("Polling lease unavailable after %ss", wait_seconds)
                return False
            if not logged_wait:
                _log.info("Polling lease held by another process; waiting for handover")
                logged_wait = True
            time.sleep(max(0.1, retry_seconds))

    def _try_acquire(self):
        if config.DATABASE_URL:
            return self._try_postgres()
        return self._try_local_file()

    def _try_postgres(self):
        import psycopg2

        connection = None
        try:
            connection = psycopg2.connect(config.DATABASE_URL, connect_timeout=5)
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (_LOCK_KEY,))
                acquired = bool(cursor.fetchone()[0])
            if not acquired:
                connection.close()
                return False
            self._connection = connection
            self.backend = "postgres"
            return True
        except Exception as error:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            _log.warning("Polling lease DB check failed: %s", error)
            return False

    def _try_local_file(self):
        import fcntl

        lock_file = open(_LOCAL_LOCK_PATH, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return False
        self._local_file = lock_file
        self.backend = "local-file"
        if os.environ.get("RAILWAY_ENVIRONMENT_ID"):
            _log.warning(
                "DATABASE_URL is not set; polling lease cannot coordinate separate Railway containers"
            )
        return True

    def release(self):
        if self._connection is not None:
            try:
                with self._connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (_LOCK_KEY,))
            except Exception as error:
                _log.warning("Polling lease release failed: %s", error)
            finally:
                self._connection.close()
                self._connection = None
        if self._local_file is not None:
            try:
                import fcntl

                fcntl.flock(self._local_file.fileno(), fcntl.LOCK_UN)
            finally:
                self._local_file.close()
                self._local_file = None
        if self.backend:
            _log.info("Polling lease released backend=%s", self.backend)
            self.backend = ""
