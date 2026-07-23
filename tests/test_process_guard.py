import sys
import types

import process_guard


def test_postgres_polling_lease_allows_only_one_replica(monkeypatch):
    """Two bot processes share PostgreSQL's one advisory polling lease."""
    locks = set()

    class Cursor:
        def __init__(self, connection):
            self.connection = connection
            self.result = (False,)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, _params):
            if "pg_try_advisory_lock" in query:
                if process_guard._LOCK_KEY not in locks:
                    locks.add(process_guard._LOCK_KEY)
                    self.connection.owns_lock = True
                    self.result = (True,)
                return
            if "pg_advisory_unlock" in query and self.connection.owns_lock:
                locks.discard(process_guard._LOCK_KEY)
                self.connection.owns_lock = False
                self.result = (True,)

        def fetchone(self):
            return self.result

    class Connection:
        def __init__(self):
            self.autocommit = False
            self.owns_lock = False

        def cursor(self):
            return Cursor(self)

        def close(self):
            if self.owns_lock:
                locks.discard(process_guard._LOCK_KEY)
                self.owns_lock = False

    monkeypatch.setattr(process_guard.config, "DATABASE_URL", "postgresql://test")
    monkeypatch.setitem(sys.modules, "psycopg2", types.SimpleNamespace(connect=lambda *_args, **_kwargs: Connection()))

    first = process_guard.PollingLease()
    second = process_guard.PollingLease()
    try:
        assert first.acquire(wait_seconds=0)
        assert first.backend == "postgres"
        assert not second.acquire(wait_seconds=0)
        first.release()
        assert second.acquire(wait_seconds=0)
    finally:
        first.release()
        second.release()
