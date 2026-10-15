import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import bot_callbacks
import learning_router
import settings


def _route(data, monkeypatch, module, name):
    calls = []

    async def fake(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(module, name, fake)
    return calls


def test_dictionary_and_trainer_delete_at_once(monkeypatch):
    deleted = _route("dict", monkeypatch, learning_router.dictionary, "del_dict_entry_by_id")
    removed = _route("trainer", monkeypatch, learning_router.trainer, "remove_from_training")

    asyncio.run(learning_router.handle_action(None, "42", None, "dictdelid_w1", None))
    asyncio.run(learning_router.handle_callback(None, "42", "ex_remove_task1", None))

    assert deleted[0][0][2] == "w1"
    assert removed[0][1]["task_id"] == "task1"


def test_movie_delete_at_once(monkeypatch):
    movies = _route("mfd", monkeypatch, bot_callbacks.leisure_movies, "delete_favorite_movie")

    route = bot_callbacks._first(bot_callbacks.ROUTES, "mfd:tok:abcd:1:2")
    asyncio.run(route.handler(SimpleNamespace(bot=None, cid="42", q=None, data="mfd:tok:abcd:1:2")))

    assert movies[0][0][2:4] == ("tok", "abcd")
