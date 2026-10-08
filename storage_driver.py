"""KV-хранилище в SQLite-файле на VM с in-memory backend для локальной разработки."""

import copy
import json
import logging
import os
import sqlite3
import threading
import time

import config

_log = logging.getLogger(__name__)
_connection = None
_memory = {}
_memory_locks = {}
_connection_lock = threading.RLock()
_PRELOAD_MAX_BYTES = 1_000_000
_BUSY_TIMEOUT_SECONDS = 10
_read_cache = {}


class StorageUnavailableError(RuntimeError):
    """Настроенное постоянное хранилище недоступно."""


def _json_safe(value):
    """Приводит поддерживаемые контейнеры к виду, который одинаково хранится в памяти и JSON."""
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_json_safe(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return value


def _legacy_keys(key):
    return tuple(getattr(config, "LEGACY_STORAGE_KEYS", {}).get(key, ()))


def _persistent():
    """SQLite при DATABASE_PATH; память — только локально без настроек БД.

    Старый DATABASE_URL без DATABASE_PATH означает, что данные ещё не перенесены:
    молча работать в памяти нельзя — бот «потерял» бы пользователей.
    """
    if config.DATABASE_PATH:
        return True
    if config.DATABASE_URL:
        raise StorageUnavailableError(
            "DATABASE_PATH is not set: run tools/migrate_to_sqlite.py --apply first"
        )
    return False


def check_backend():
    """Проверка при старте: настроенное хранилище открывается, иначе исключение."""
    if _persistent():
        db()


# Бот — единственный процесс, который пишет в kv, и каждая запись проходит через
# save/mutate/delete ниже, обновляя этот кэш. Поэтому значения не устаревают и
# читаются из файла один раз за жизнь процесса. Ручные правки БД в обход бота
# видны после рестарта сервиса.
def _cache_get(key):
    cached = _read_cache.get(key)
    return None if cached is None else copy.deepcopy(cached)


def _cache_set(key, value):
    _read_cache[key] = copy.deepcopy(value)


def _connect(path):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    connection = sqlite3.connect(
        path, timeout=_BUSY_TIMEOUT_SECONDS, check_same_thread=False, isolation_level=None,
    )
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return connection


def db():
    """Общее соединение с файлом БД (потоки делят его под _connection_lock)."""
    global _connection
    if not _persistent():
        return None
    with _connection_lock:
        if _connection is None:
            try:
                _connection = _connect(config.DATABASE_PATH)
            except sqlite3.Error as error:
                raise StorageUnavailableError(f"SQLite open failed: {error}") from error
        return _connection


def _run(operation, action):
    with _connection_lock:
        try:
            return operation(db())
        except StorageUnavailableError:
            raise
        except sqlite3.Error as error:
            _log.warning("storage: %s DB error: %s", action, error)
            raise StorageUnavailableError(f"SQLite {action} failed") from error


def _dumps(value):
    return json.dumps(value, ensure_ascii=False)


def ping():
    """Проверяет именно активный backend, не маскируя файл БД памятью."""
    if not _persistent():
        return True
    row = _run(lambda connection: connection.execute("SELECT 1").fetchone(), "ping")
    return bool(row and row[0] == 1)


def query_latency():
    """Время SELECT 1 на отдельном соединении — для админской проверки."""
    connection = sqlite3.connect(config.DATABASE_PATH, timeout=_BUSY_TIMEOUT_SECONDS)
    try:
        started = time.monotonic()
        connection.execute("SELECT 1").fetchone()
        return time.monotonic() - started
    finally:
        connection.close()


def load(key):
    cached = _cache_get(key)
    if cached is not None:
        return cached

    if not _persistent():
        if key not in _memory:
            for legacy_key in _legacy_keys(key):
                if legacy_key in _memory:
                    value = copy.deepcopy(_memory[legacy_key])
                    _memory[key] = copy.deepcopy(value)
                    _cache_set(key, value)
                    return value
        value = {
            k: list(v) if isinstance(v, list) else v
            for k, v in _memory.get(key, {}).items()
        }
        _cache_set(key, value)
        return value

    def operation(connection):
        row = connection.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        if row is not None:
            # Кэш ставится под локом: иначе параллельный mutate мог бы
            # записать свежее значение, а этот поток затёр бы его старым.
            value = json.loads(row[0])
            _cache_set(key, value)
            return value, False
        for legacy_key in _legacy_keys(key):
            row = connection.execute("SELECT value FROM kv WHERE key = ?", (legacy_key,)).fetchone()
            if row is not None:
                return json.loads(row[0]), True
        _cache_set(key, {})
        return {}, False

    value, migrated = _run(operation, f"load({key})")
    if migrated:
        # Копируем, а не удаляем старый ключ: откат версии остаётся
        # безопасным, а новая версия дальше работает только с canonical key.
        save(key, value)
    return copy.deepcopy(value)


def preload(max_bytes=_PRELOAD_MAX_BYTES):
    """Одним запросом кладёт в кэш все небольшие ключи, чтобы меню открывались без БД."""
    if not _persistent():
        return 0

    def operation(connection):
        rows = connection.execute(
            "SELECT key, value FROM kv WHERE length(value) <= ?", (max_bytes,),
        ).fetchall()
        for key, value in rows:
            if key not in _read_cache:
                _cache_set(key, json.loads(value))
        return len(rows)

    return _run(operation, "preload")


def save(key, data):
    data = _json_safe(data)

    if not _persistent():
        _memory[key] = copy.deepcopy(data)
        _cache_set(key, data)
        return

    def operation(connection):
        connection.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, _dumps(data)),
        )
        _cache_set(key, data)

    _run(operation, f"save({key})")


def mutate(key, mutator):
    """Атомарно загружает, изменяет и сохраняет одну JSON KV-запись."""

    if not _persistent():
        lock = _memory_locks.setdefault(key, threading.Lock())

        with lock:
            current = copy.deepcopy(_memory.get(key, {}))

            new_value, result = mutator(
                current if isinstance(current, dict) else {}
            )
            new_value = _json_safe(new_value)

            _memory[key] = copy.deepcopy(new_value)
            _cache_set(key, new_value)

            return result

    def operation(connection):
        # BEGIN IMMEDIATE берёт блокировку записи файла сразу: второй процесс
        # (ручной скрипт) не прочитает устаревшее значение посреди изменения.
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            current = json.loads(row[0]) if row else {}
            new_value, result = mutator(current if isinstance(current, dict) else {})
            new_value = _json_safe(new_value)
            connection.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, _dumps(new_value)),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        _cache_set(key, new_value)
        return result

    return _run(operation, f"mutate({key})")


def delete(key):
    """Удаляет KV-запись из активного backend."""

    if not _persistent():
        _memory.pop(key, None)
        _read_cache.pop(key, None)
        return

    def operation(connection):
        connection.execute("DELETE FROM kv WHERE key = ?", (key,))
        _read_cache.pop(key, None)

    _run(operation, f"delete({key})")
