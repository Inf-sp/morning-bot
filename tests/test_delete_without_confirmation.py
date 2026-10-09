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


def test_movie_and_book_delete_at_once(monkeypatch):
    movies = _route("mfd", monkeypatch, bot_callbacks.leisure_movies, "delete_favorite_movie")
    books = _route("bfd", monkeypatch, bot_callbacks.leisure_books, "delete_favorite_book")

    for data in ("mfd:tok:abcd:1:2", "bfd:tok:efgh:0:3"):
        route = bot_callbacks._first(bot_callbacks.ROUTES, data)
        asyncio.run(route.handler(SimpleNamespace(bot=None, cid="42", q=None, data=data)))

    assert movies[0][0][2:4] == ("tok", "abcd")
    assert books[0][0][2:4] == ("tok", "efgh")


def test_dictionary_and_trainer_delete_at_once(monkeypatch):
    deleted = _route("dict", monkeypatch, learning_router.dictionary, "del_dict_entry_by_id")
    removed = _route("trainer", monkeypatch, learning_router.trainer, "remove_from_training")

    asyncio.run(learning_router.handle_action(None, "42", None, "dictdelid_w1", None))
    asyncio.run(learning_router.handle_callback(None, "42", "ex_remove_task1", None))

    assert deleted[0][0][2] == "w1"
    assert removed[0][1]["task_id"] == "task1"


def test_lifehack_delete_at_once(monkeypatch):
    removed = _route("lh", monkeypatch, settings, "delete_lifehack")

    asyncio.run(settings.handle_callback(None, "42", "set_lh_delete_r1"))

    assert removed[0][0][2] == "r1"
