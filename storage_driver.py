"""KV-драйвер PostgreSQL с локальным in-memory backend для разработки."""

import copy
import json
import logging
import threading
import time

import config

_log = logging.getLogger(__name__)
_connection = None
_memory = {}
_memory_locks = {}
_connection_lock = threading.RLock()
_READ_CACHE_TTL = 5
_read_cache = {}


class StorageUnavailableError(RuntimeError):
    """Настроенное постоянное хранилище временно недоступно."""


def _json_safe(value):
    """Приводит поддерживаемые контейнеры к виду, который одинаково хранится в памяти и JSONB."""
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


def _cache_get(key):
    cached = _read_cache.get(key)
    if not cached or time.monotonic() - cached[0] >= _READ_CACHE_TTL:
        return None
    return copy.deepcopy(cached[1])


def _cache_set(key, value):
    _read_cache[key] = (time.monotonic(), copy.deepcopy(value))


def db():
    global _connection

    if not config.DATABASE_URL:
        return None

    if _connection is not None and not _connection.closed:
        return _connection

    try:
        import psycopg2

        _connection = psycopg2.connect(config.DATABASE_URL)
        _connection.autocommit = True

        with _connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS kv "
                "(key TEXT PRIMARY KEY, value JSONB)"
            )

        return _connection

    except Exception as error:
        _connection = None
        _log.warning(
            "storage: DB connect failed; persistent backend unavailable: %s",
            error,
        )
        return None


def ping():
    """Проверяет именно активный backend, не маскируя PostgreSQL памятью."""
    if not config.DATABASE_URL:
        return True
    connection = db()
    if connection is None:
        raise StorageUnavailableError("PostgreSQL connection unavailable")
    try:
        with _connection_lock:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                row = cursor.fetchone()
        return bool(row and row[0] == 1)
    except Exception as error:
        _invalidate_connection()
        raise StorageUnavailableError("PostgreSQL health check failed") from error


def _invalidate_connection():
    global _connection

    connection, _connection = _connection, None

    if connection is not None:
        try:
            connection.close()
        except Exception:
            pass


def load(key):
    cached = _cache_get(key)
    if cached is not None:
        return cached

    connection = db()

    if connection is None:
        if config.DATABASE_URL:
            raise StorageUnavailableError("PostgreSQL connection unavailable")
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

    try:
        migrated = False
        with _connection_lock:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT value FROM kv WHERE key = %s",
                    (key,),
                )
                row = cursor.fetchone()

                if row is None:
                    for legacy_key in _legacy_keys(key):
                        cursor.execute(
                            "SELECT value FROM kv WHERE key = %s",
                            (legacy_key,),
                        )
                        row = cursor.fetchone()
                        if row is not None:
                            migrated = True
                            break

        value = row[0] if row else {}
        if migrated:
            # Копируем, а не удаляем старый ключ: откат версии остаётся
            # безопасным, а новая версия дальше работает только с canonical key.
            save(key, value)
        _cache_set(key, value)
        return copy.deepcopy(value)

    except Exception as error:
        _invalidate_connection()
        _log.warning("storage: load(%s) DB error: %s", key, error)
        raise StorageUnavailableError(f"PostgreSQL load failed for {key}") from error


def save(key, data):
    data = _json_safe(data)
    connection = db()

    if connection is None:
        if config.DATABASE_URL:
            raise StorageUnavailableError("PostgreSQL connection unavailable")
        _memory[key] = copy.deepcopy(data)
        _cache_set(key, data)
        return

    try:
        with _connection_lock:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO kv (key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) "
                    "DO UPDATE SET value = EXCLUDED.value",
                    (
                        key,
                        json.dumps(data, ensure_ascii=False),
                    ),
                )

        _cache_set(key, data)

    except Exception as error:
        _invalidate_connection()

        _log.warning("storage: save(%s) DB error: %s", key, error)
        raise StorageUnavailableError(f"PostgreSQL save failed for {key}") from error


def mutate(key, mutator):
    """Атомарно загружает, изменяет и сохраняет одну JSON KV-запись."""

    if not config.DATABASE_URL:
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

    try:
        # Короткие KV-мутации используют уже открытое соединение текущего
        # процесса. Advisory lock по-прежнему координирует несколько процессов,
        # а локальная блокировка не допускает параллельных транзакций на нём.
        with _connection_lock:
            connection = db()
            if connection is None:
                raise RuntimeError("PostgreSQL connection unavailable")
            connection.autocommit = False
            try:
                with connection.cursor() as cursor:
                    # Транзакционная advisory-блокировка гарантирует,
                    # что один KV-ключ не изменяется одновременно
                    # несколькими процессами или потоками.
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (key,),
                    )

                    cursor.execute(
                        "SELECT value FROM kv "
                        "WHERE key = %s "
                        "FOR UPDATE",
                        (key,),
                    )

                    row = cursor.fetchone()
                    current = row[0] if row else {}
                    new_value, result = mutator(
                        current if isinstance(current, dict) else {}
                    )
                    new_value = _json_safe(new_value)

                    cursor.execute(
                        "INSERT INTO kv (key, value) VALUES (%s, %s) "
                        "ON CONFLICT (key) "
                        "DO UPDATE SET value = EXCLUDED.value",
                        (key, json.dumps(new_value, ensure_ascii=False)),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if not connection.closed:
                    connection.autocommit = True

            _cache_set(key, new_value)
            return result

    except Exception as error:
        _log.exception(
            "storage: mutate(%s) DB error: %s",
            key,
            error,
        )
        raise


def delete(key):
    """Удаляет KV-запись из активного backend."""

    connection = db()

    if connection is None:
        if config.DATABASE_URL:
            raise StorageUnavailableError("PostgreSQL connection unavailable")
        _memory.pop(key, None)
        _read_cache.pop(key, None)
        return

    try:
        with _connection_lock:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM kv WHERE key = %s",
                    (key,),
                )

        _read_cache.pop(key, None)

    except Exception as error:
        _invalidate_connection()
        _log.warning("storage: delete(%s) DB error: %s", key, error)
        raise StorageUnavailableError(f"PostgreSQL delete failed for {key}") from error
