import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "tools" / "migrate_to_sqlite.py"
spec = importlib.util.spec_from_file_location("migrate_to_sqlite", _PATH)
migrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrate)

ROWS = [("profile.json", {"42": {"name": "Света"}}), ("dict.json", {"42": [{"term": "wazig"}]})]


def _run(monkeypatch, path, *args):
    monkeypatch.setenv("DATABASE_URL", "postgresql://railway")
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setattr(migrate, "_read_postgres", lambda _url: list(ROWS))
    monkeypatch.setattr(sys, "argv", ["migrate_to_sqlite.py", *args])
    migrate.main()


def test_dry_run_does_not_create_file(tmp_path, monkeypatch):
    path = tmp_path / "bot.db"
    _run(monkeypatch, path)
    assert not path.exists()


def test_apply_copies_and_verifies_then_refuses_to_overwrite(tmp_path, monkeypatch):
    path = tmp_path / "data" / "bot.db"
    _run(monkeypatch, path, "--apply")

    with sqlite3.connect(path) as connection:
        stored = dict(connection.execute("SELECT key, value FROM kv").fetchall())
    assert set(stored) == {"profile.json", "dict.json"}
    assert '"Света"' in stored["profile.json"]

    with pytest.raises(SystemExit, match="уже содержит данные"):
        _run(monkeypatch, path, "--apply")
