from datetime import timedelta

import pytest

import api_usage
import config
import storage_driver
import store


class OperationalError(Exception):
    """Имя совпадает с psycopg2.OperationalError — драйвер распознаёт обрыв по нему."""


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.conn.calls.append(sql)
        if self.conn.broken:
            self.conn.closed = 2
            raise OperationalError("server closed the connection unexpectedly")
        if sql.startswith("INSERT"):
            self.conn.saved = params

    def fetchone(self):
        return ({"42": {"name": "Света"}},)


class FakeConnection:
    def __init__(self, broken=False):
        self.broken = broken
        self.closed = 0
        self.autocommit = True
        self.calls = []
        self.saved = None

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = 1


@pytest.fixture
def postgres(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://configured")
    monkeypatch.setattr(storage_driver, "_connection", None)
    monkeypatch.setattr(storage_driver, "_read_cache", {})
    connections = []

    def install(*items):
        connections.extend(items)
        monkeypatch.setattr(storage_driver, "db", lambda: connections.pop(0) if connections else None)

    return install


def test_load_reconnects_once_after_dropped_connection(postgres):
    dead, fresh = FakeConnection(broken=True), FakeConnection()
    postgres(dead, fresh)

    assert storage_driver.load(config.PROFILE_KEY) == {"42": {"name": "Света"}}
    assert dead.closed and fresh.calls


def test_mutate_retries_when_connection_dropped_before_mutator(postgres):
    dead, fresh = FakeConnection(broken=True), FakeConnection()
    postgres(dead, fresh)
    calls = []

    def change(data):
        calls.append(True)
        return {**data, "x": 1}, "ok"

    assert storage_driver.mutate("k", change) == "ok"
    assert calls == [True]
    assert fresh.saved is not None


def test_mutate_does_not_rerun_mutator_after_drop(postgres):
    conn, spare = FakeConnection(), FakeConnection()
    postgres(conn, spare)
    calls = []

    def change(data):
        calls.append(True)
        conn.broken = True  # обрыв во время записи, после mutator
        return data, None

    with pytest.raises(OperationalError):
        storage_driver.mutate("k", change)
    assert calls == [True]
    assert spare.calls == []


def test_get_profile_does_not_cache_value_read_during_mutation(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(storage_driver, "_memory", {config.PROFILE_KEY: {"42": {"v": 1}}})
    monkeypatch.setattr(storage_driver, "_read_cache", {})
    monkeypatch.setattr(store, "_profile_cache", {})
    real_load = store._load

    def racing_load(key):
        value = real_load(key)  # старое значение уже прочитано
        store.mutate_profile("42", lambda prof: ({**prof, "v": 2}, None))
        return value

    monkeypatch.setattr(store, "_load", racing_load)
    assert store.get_profile("42") == {"v": 1}
    monkeypatch.setattr(store, "_load", real_load)

    assert store.get_profile("42") == {"v": 2}


def test_purge_user_keeps_other_users(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(storage_driver, "_memory", {
        config.PROFILE_KEY: {"1": {"a": 1}, "2": {"b": 2}},
    })
    monkeypatch.setattr(storage_driver, "_read_cache", {})
    monkeypatch.setattr(store, "_profile_cache", {})

    store.purge_user("1")

    assert storage_driver._memory[config.PROFILE_KEY] == {"2": {"b": 2}}


def test_usage_prune_drops_stale_minute_buckets():
    now = api_usage._now()
    old = now - timedelta(days=3)
    svc = {"counts": {
        f"minute:requests:{api_usage._bucket('minute', old)}": 5,
        f"minute:requests:{api_usage._bucket('minute', now)}": 1,
        f"day:requests:{api_usage._bucket('day', old)}": 7,
    }}

    api_usage._prune(svc, int(now.timestamp()))

    assert svc["counts"] == {
        f"minute:requests:{api_usage._bucket('minute', now)}": 1,
        f"day:requests:{api_usage._bucket('day', old)}": 7,
    }


def test_legacy_flat_wardrobe_is_migrated_without_crashing():
    """Старый плоский шкаф {категория: [вещи]} мигрирует в zones без AttributeError."""
    import store

    migrated = store._migrate_legacy_wardrobe({"_v": 3, "футболки": ["белая", "Белая"], "джинсы": ["синие"]})

    items = [item for zone in migrated["zones"].values() for bucket in zone.values() for item in bucket]
    assert sorted(item["name"] for item in items) == ["белая", "синие"]
    assert all(item["id"] and item["zone"] and item["subcategory"] for item in items)
