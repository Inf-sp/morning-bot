import os
from concurrent.futures import ThreadPoolExecutor

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import access
import config
import recipe_state
import storage_driver
import store


def _clear(*keys):
    for key in keys:
        storage_driver.delete(key)


@pytest.fixture(autouse=True)
def clean_shared_keys(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    keys = (
        config.PENDING_INVITES_KEY, config.ALLOWED_CIDS_KEY,
        config.SETTINGS_FILE, config.RECIPE_QUEUE_KEY,
    )
    _clear(*keys)
    yield
    _clear(*keys)


def test_one_time_invite_has_exactly_one_parallel_winner(monkeypatch):
    monkeypatch.setattr(config, "CHAT_ID", "")
    code = access.create_invite()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda cid: access.use_invite(code, cid), ("41", "42")))

    assert sorted(results) == [False, True]
    assert len(access.get_allowed_cids()) == 1


def test_parallel_settings_updates_keep_both_users(monkeypatch):
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(
            lambda cid: store.set_settings(cid, 52.0, 4.0, f"City {cid}"),
            ("41", "42"),
        ))

    assert store.get_settings("41")["city"] == "City 41"
    assert store.get_settings("42")["city"] == "City 42"


def test_parallel_recipe_queue_reads_advance_atomically(monkeypatch):
    recipe_state.set_recipe_queue("42", "dinner", [{"name": "A"}, {"name": "B"}])

    with ThreadPoolExecutor(max_workers=2) as pool:
        items = list(pool.map(lambda _value: recipe_state.queue_next("42"), (1, 2)))

    assert {item["name"] for item in items} == {"A", "B"}
    assert recipe_state.get_recipe_queue("42")["pos"] == 2
