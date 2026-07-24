"""KV-драйвер PostgreSQL с локальным in-memory fallback."""

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
            "storage: DB connect failed, using memory: %s",
            error,
        )
        return None


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
        value = {
            k: list(v) if isinstance(v, list) else v
            for k, v in _memory.get(key, {}).items()
        }
        _cache_set(key, value)
        return value

    try:
        with _connection_lock:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT value FROM kv WHERE key = %s",
                    (key,),
                )
                row = cursor.fetchone()

        value = row[0] if row else {}
        _cache_set(key, value)
        return copy.deepcopy(value)

    except Exception as error:
        _invalidate_connection()
        _log.warning(
            "storage: load(%s) DB error, using memory: %s",
            key,
            error,
        )

        return {
            k: list(v) if isinstance(v, list) else v
            for k, v in _memory.get(key, {}).items()
        }


def save(key, data):
    connection = db()

    if connection is None:
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

        _log.warning(
            "storage: save(%s) DB error, falling back to memory: %s",
            key,
            error,
        )

        _memory[key] = copy.deepcopy(data)
        _cache_set(key, data)


def mutate(key, mutator):
    """Атомарно загружает, изменяет и сохраняет одну JSON KV-запись."""

    if not config.DATABASE_URL:
        lock = _memory_locks.setdefault(key, threading.Lock())

        with lock:
            current = copy.deepcopy(_memory.get(key, {}))

            new_value, result = mutator(
                current if isinstance(current, dict) else {}
            )

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

        _log.warning(
            "storage: delete(%s) DB error: %s",
            key,
            error,
        )
