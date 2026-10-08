"""Перенос KV из PostgreSQL (DATABASE_URL) в SQLite-файл (DATABASE_PATH).

Запускать на VM при остановленном боте:

    python3 tools/migrate_to_sqlite.py           # что будет перенесено
    python3 tools/migrate_to_sqlite.py --apply   # записать файл и сверить

Файл с данными не перезаписывается: так повторный запуск не затрёт
уже работающую базу более старой копией.
"""
import argparse
import json
import os
import sqlite3
import sys


def _read_postgres(url):
    import psycopg2

    connection = psycopg2.connect(url, connect_timeout=10)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT key, value FROM kv ORDER BY key")
            return cursor.fetchall()
    finally:
        connection.close()


def _write_sqlite(path, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        if connection.execute("SELECT count(*) FROM kv").fetchone()[0]:
            sys.exit(f"{path} уже содержит данные — перенос остановлен, чтобы их не затереть.")
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            "INSERT INTO kv (key, value) VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in rows],
        )
        connection.execute("COMMIT")
        stored = dict(connection.execute("SELECT key, value FROM kv").fetchall())
    finally:
        connection.close()
    mismatched = [key for key, value in rows if json.loads(stored.get(key, "null")) != value]
    if len(stored) != len(rows) or mismatched:
        sys.exit(f"Сверка не прошла: {len(stored)}/{len(rows)} ключей, расхождения: {mismatched[:10]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="записать SQLite-файл")
    args = parser.parse_args()
    url, path = os.environ.get("DATABASE_URL", ""), os.environ.get("DATABASE_PATH", "")
    if not url or not path:
        sys.exit("Нужны DATABASE_URL (откуда) и DATABASE_PATH (куда).")

    rows = _read_postgres(url)
    size = sum(len(json.dumps(value, ensure_ascii=False)) for _key, value in rows)
    print(f"PostgreSQL: {len(rows)} ключей, {size / 1024:.0f} KB")
    if not args.apply:
        print(f"Пробный прогон. Для записи в {path} добавь --apply.")
        return
    _write_sqlite(path, rows)
    print(f"Готово: {len(rows)} ключей перенесено в {path}, сверка пройдена.")


if __name__ == "__main__":
    main()
