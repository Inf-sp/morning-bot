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
_CONNECT_TIMEOUT = 5
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

    with _connection_lock:
        if _connection is not None and not _connection.closed:
            return _connection

        try:
            import psycopg2

            # Публичный прокси Railway может молча оборвать простаивающий TCP:
            # keepalive выявляет мёртвый сокет, connect_timeout не даёт
            # подключению надолго повесить event loop.
            _connection = psycopg2.connect(
                config.DATABASE_URL,
                connect_timeout=_CONNECT_TIMEOUT,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=3,
            )
            _connection.autocommit = True

            with _connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS kv "
                    "(key TEXT PRIMARY KEY, value JSONB)"
                )

            return _connection

        except Exception as error:
            _invalidate_connection()
            _log.warning(
                "storage: DB connect failed; persistent backend unavailable: %s",
                error,
            )
            return None


def _connection_lost(connection, error):
    if getattr(connection, "closed", 0):
        return True
    return any(
        cls.__name__ in ("OperationalError", "InterfaceError")
        for cls in type(error).__mro__
    )


def _run(operation, can_retry=lambda: True):
    """Выполняет operation(connection) под локом соединения.

    Оборванное соединение (рестарт Postgres, обрыв прокси) закрывается и
    открывается заново; идемпотентная операция повторяется один раз.
    """
    for attempt in range(2):
        with _connection_lock:
            connection = db()
            if connection is None:
                raise StorageUnavailableError("PostgreSQL connection unavailable")
            try:
                return operation(connection)
            except Exception as error:
                if not _connection_lost(connection, error):
                    raise
                _invalidate_connection()
                if attempt or not can_retry():
                    raise
                _log.warning("storage: DB connection lost, reconnecting: %s", error)


def ping():
    """Проверяет именно активный backend, не маскируя PostgreSQL памятью."""
    if not config.DATABASE_URL:
        return True

    def operation(connection):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone()

    try:
        row = _run(operation)
    except StorageUnavailableError:
        raise
    except Exception as error:
        raise StorageUnavailableError("PostgreSQL health check failed") from error
    return bool(row and row[0] == 1)


def query_latency():
    """Время SELECT 1 на отдельном соединении — для админской проверки.

    Общее соединение занято под локом другими записями, поэтому ping через него
    меряет очередь, а не базу. Возвращает секунды одного запроса.
    """
    import psycopg2

    connection = psycopg2.connect(config.DATABASE_URL, connect_timeout=_CONNECT_TIMEOUT)
    try:
        with connection.cursor() as cursor:
            started = time.monotonic()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            return time.monotonic() - started
    finally:
        connection.close()


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

    if not config.DATABASE_URL:
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
        with connection.cursor() as cursor:
            cursor.execute("SELECT value FROM kv WHERE key = %s", (key,))
            row = cursor.fetchone()
            if row is not None:
                # Кэш ставится под локом: иначе параллельный mutate мог бы
                # записать свежее значение, а этот поток затёр бы его старым.
                _cache_set(key, row[0])
                return row[0], False
            for legacy_key in _legacy_keys(key):
                cursor.execute("SELECT value FROM kv WHERE key = %s", (legacy_key,))
                row = cursor.fetchone()
                if row is not None:
                    return row[0], True
        return {}, False

    try:
        value, migrated = _run(operation)
    except StorageUnavailableError:
        raise
    except Exception as error:
        _log.warning("storage: load(%s) DB error: %s", key, error)
        raise StorageUnavailableError(f"PostgreSQL load failed for {key}") from error

    if migrated:
        # Копируем, а не удаляем старый ключ: откат версии остаётся
        # безопасным, а новая версия дальше работает только с canonical key.
        save(key, value)
    return copy.deepcopy(value)


def save(key, data):
    data = _json_safe(data)

    if not config.DATABASE_URL:
        _memory[key] = copy.deepcopy(data)
        _cache_set(key, data)
        return

    def operation(connection):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO kv (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) "
                "DO UPDATE SET value = EXCLUDED.value",
                (key, json.dumps(data, ensure_ascii=False)),
            )
        _cache_set(key, data)

    try:
        _run(operation)
    except StorageUnavailableError:
        raise
    except Exception as error:
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

    mutator_called = []

    def operation(connection):
        # Advisory lock координирует несколько процессов, а _connection_lock
        # не допускает параллельных транзакций на общем соединении.
        connection.autocommit = False
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (key,),
                )
                cursor.execute(
                    "SELECT value FROM kv WHERE key = %s FOR UPDATE",
                    (key,),
                )
                row = cursor.fetchone()
                current = row[0] if row else {}
                mutator_called.append(True)
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
            if not connection.closed:
                connection.rollback()
            raise
        finally:
            if not connection.closed:
                connection.autocommit = True
        _cache_set(key, new_value)
        return result

    try:
        # Повтор только если обрыв случился до вызова mutator: у mutator
        # бывают побочные эффекты, и второй прогон мог бы их задвоить.
        return _run(operation, can_retry=lambda: not mutator_called)
    except Exception as error:
        _log.exception("storage: mutate(%s) DB error: %s", key, error)
        raise


def delete(key):
    """Удаляет KV-запись из активного backend."""

    if not config.DATABASE_URL:
        _memory.pop(key, None)
        _read_cache.pop(key, None)
        return

    def operation(connection):
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM kv WHERE key = %s", (key,))
        _read_cache.pop(key, None)

    try:
        _run(operation)
    except StorageUnavailableError:
        raise
    except Exception as error:
        _log.warning("storage: delete(%s) DB error: %s", key, error)
        raise StorageUnavailableError(f"PostgreSQL delete failed for {key}") from error
