import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot
import config
import storage_driver
import tracking
from telegram.error import TimedOut
from telegram.request import HTTPXRequest


def test_storage_reuses_connection_without_probe_and_caches_reads(monkeypatch):
    class Cursor:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, params=None):
            self.connection.queries.append(query)

        def fetchone(self):
            return ({"value": 1},)

    class Connection:
        closed = False
        queries = []

        def cursor(self):
            return Cursor(self)

    connection = Connection()
    monkeypatch.setattr(config, "DATABASE_URL", "postgres://test")
    monkeypatch.setattr(storage_driver, "_connection", connection)
    storage_driver._read_cache.pop("fast-key", None)

    assert storage_driver.load("fast-key") == {"value": 1}
    assert storage_driver.load("fast-key") == {"value": 1}
    assert len(connection.queries) == 1
    assert "SELECT 1" not in connection.queries


def test_activity_tracking_is_throttled(monkeypatch):
    calls = {"load": 0, "save": 0}
    monkeypatch.setattr(tracking.store, "_load", lambda _key: calls.__setitem__("load", calls["load"] + 1) or {})
    monkeypatch.setattr(tracking.store, "_save", lambda _key, _data: calls.__setitem__("save", calls["save"] + 1))
    tracking._last_touch.pop("fast-user", None)

    tracking.touch("fast-user")
    tracking.touch("fast-user")

    assert calls == {"load": 1, "save": 1}


def test_callback_ack_runs_in_parallel_with_handler(monkeypatch):
    answer_started = asyncio.Event()
    release_answer = asyncio.Event()

    class Query:
        class Message:
            chat_id = "fast-callback"

        message = Message()

        async def answer(self):
            answer_started.set()
            await release_answer.wait()

    class Update:
        callback_query = Query()

    class Context:
        bot = object()

    async def fake_handle(*args, **kwargs):
        await answer_started.wait()
        release_answer.set()

    monkeypatch.setattr(bot.bot_callbacks, "handle", fake_handle)
    monkeypatch.setattr(bot.tracking, "touch", lambda _cid: None)

    asyncio.run(asyncio.wait_for(bot.answer_callback(Update(), Context()), timeout=1))


def test_telegram_connect_timeout_is_retried_once(monkeypatch):
    calls = {"count": 0}

    async def fake_request(self, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            cause = type("ConnectTimeout", (Exception,), {})()
            raise TimedOut("connect timeout") from cause
        return 200, b"{}"

    monkeypatch.setattr(HTTPXRequest, "do_request", fake_request)
    request = bot._RetryingHTTPXRequest(connection_pool_size=2)

    async def run():
        try:
            return await request.do_request("https://example.invalid", "POST")
        finally:
            await request.shutdown()

    assert asyncio.run(run()) == (200, b"{}")
    assert calls["count"] == 2
