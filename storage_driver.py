"""KV-драйвер PostgreSQL с локальным in-memory fallback."""

import copy
import json
import logging
import threading

import config

_log = logging.getLogger(__name__)
_connection = None
_memory = {}
_memory_locks = {}
_connection_lock = threading.RLock()


def db():
    global _connection
    if not config.DATABASE_URL:
        return None
    try:
        if _connection is None or _connection.closed:
            import psycopg2
            _connection = psycopg2.connect(config.DATABASE_URL)
            _connection.autocommit = True
            with _connection.cursor() as cursor:
                cursor.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value JSONB)")
        with _connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return _connection
    except Exception:
        try:
            import psycopg2
            _connection = psycopg2.connect(config.DATABASE_URL)
            _connection.autocommit = True
            with _connection.cursor() as cursor:
                cursor.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value JSONB)")
            return _connection
        except Exception as error:
            _log.warning("storage: DB reconnect failed, using memory: %s", error)
            return None


def load(key):
    connection = db()
    if connection is None:
        return {k: list(v) if isinstance(v, list) else v
                for k, v in _memory.get(key, {}).items()}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT value FROM kv WHERE key = %s", (key,))
            row = cursor.fetchone()
            return row[0] if row else {}
    except Exception as error:
        _log.warning("storage: load(%s) DB error, using memory: %s", key, error)
        return {k: list(v) if isinstance(v, list) else v
                for k, v in _memory.get(key, {}).items()}


def save(key, data):
    connection = db()
    if connection is None:
        _memory[key] = data
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO kv (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, json.dumps(data, ensure_ascii=False)),
            )
    except Exception as error:
        _log.warning("storage: save(%s) DB error, falling back to memory: %s", key, error)
        _memory[key] = data


def mutate(key, mutator):
    """Атомарно загружает, изменяет и сохраняет одну JSON KV-запись."""
    connection = db()
    if connection is None:
        lock = _memory_locks.setdefault(key, threading.Lock())
        with lock:
            current = copy.deepcopy(_memory.get(key, {}))
            new_value, result = mutator(current if isinstance(current, dict) else {})
            _memory[key] = new_value
            return result
    with _connection_lock:
        old_autocommit = connection.autocommit
        connection.autocommit = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
                cursor.execute("SELECT value FROM kv WHERE key = %s FOR UPDATE", (key,))
                row = cursor.fetchone()
                current = row[0] if row else {}
                new_value, result = mutator(current if isinstance(current, dict) else {})
                cursor.execute(
                    "INSERT INTO kv (key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                    (key, json.dumps(new_value, ensure_ascii=False)),
                )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.autocommit = old_autocommit


def delete(key):
    """Удаляет KV-запись из активного backend."""
    connection = db()
    if connection is None:
        _memory.pop(key, None)
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM kv WHERE key = %s", (key,))
    except Exception as error:
        _log.warning("storage: delete(%s) DB error: %s", key, error)
