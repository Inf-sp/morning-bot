import sqlite3

import pytest

import config
import storage_driver


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "bot.db"
    monkeypatch.setattr(config, "DATABASE_PATH", str(path))
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(storage_driver, "_connection", None)
    monkeypatch.setattr(storage_driver, "_read_cache", {})
    queries = []
    yield path, queries
    if storage_driver._connection is not None:
        storage_driver._connection.close()


def _count_queries(queries):
    storage_driver.db().set_trace_callback(queries.append)


def _fresh_process():
    """Как после рестарта: новое соединение и пустой кэш."""
    storage_driver._connection.close()
    storage_driver._connection = None
    storage_driver._read_cache.clear()


def test_values_persist_in_file_across_restart(sqlite_db):
    path, _queries = sqlite_db
    storage_driver.save("profile.json", {"42": {"name": "Света", "tags": {"b", "a"}}})
    storage_driver.mutate("profile.json", lambda data: ({**data, "43": {}}, None))

    _fresh_process()

    assert path.exists()
    assert storage_driver.load("profile.json") == {"42": {"name": "Света", "tags": ["a", "b"]}, "43": {}}


def test_reads_hit_file_once_including_missing_keys(sqlite_db):
    _path, queries = sqlite_db
    storage_driver.save("settings.json", {"42": {"city": "Алкмар"}})
    _fresh_process()
    _count_queries(queries)

    for _ in range(3):
        assert storage_driver.load("settings.json") == {"42": {"city": "Алкмар"}}
        assert storage_driver.load("absent.json") == {}

    assert len([q for q in queries if q.startswith("SELECT value")]) == 2


def test_preload_fills_cache_in_one_query(sqlite_db):
    _path, queries = sqlite_db
    storage_driver.save("a.json", {"x": 1})
    storage_driver.save("b.json", {"y": 2})
    _fresh_process()
    _count_queries(queries)

    assert storage_driver.preload() == 2
    assert storage_driver.load("a.json") == {"x": 1} and storage_driver.load("b.json") == {"y": 2}
    assert len(queries) == 1


def test_failed_mutation_is_rolled_back(sqlite_db):
    storage_driver.save("k.json", {"v": 1})

    def broken(data):
        raise ValueError("mutator failed")

    with pytest.raises(ValueError):
        storage_driver.mutate("k.json", broken)
    _fresh_process()
    assert storage_driver.load("k.json") == {"v": 1}
    assert storage_driver.mutate("k.json", lambda data: ({"v": data["v"] + 1}, "ok")) == "ok"
    assert storage_driver.load("k.json") == {"v": 2}


def test_legacy_key_is_copied_to_canonical_key(sqlite_db, monkeypatch):
    monkeypatch.setattr(config, "LEGACY_STORAGE_KEYS", {"new.json": ("old.json",)})
    storage_driver.save("old.json", {"42": ["Паразиты"]})
    _fresh_process()

    assert storage_driver.load("new.json") == {"42": ["Паразиты"]}
    _fresh_process()
    assert storage_driver.load("old.json") == {"42": ["Паразиты"]}  # старый ключ не удалён


def test_delete_removes_value(sqlite_db):
    storage_driver.save("gone.json", {"x": 1})
    storage_driver.delete("gone.json")
    _fresh_process()
    assert storage_driver.load("gone.json") == {}


def test_old_database_url_without_path_refuses_to_start(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", "")
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://railway")
    monkeypatch.setattr(storage_driver, "_read_cache", {})

    with pytest.raises(storage_driver.StorageUnavailableError, match="migrate_to_sqlite"):
        storage_driver.check_backend()
    with pytest.raises(storage_driver.StorageUnavailableError):
        storage_driver.load("profile.json")


def test_ping_and_latency_use_the_file(sqlite_db):
    assert storage_driver.ping() is True
    assert storage_driver.query_latency() >= 0
    assert isinstance(storage_driver.db(), sqlite3.Connection)
