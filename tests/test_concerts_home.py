import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_concerts


def test_concerts_home_opens_nearest_events_instead_of_an_intro(monkeypatch):
    calls = []

    async def fake_find(bot, cid, mode="home", artists_override=None):
        calls.append((bot, cid, mode, artists_override))

    monkeypatch.setattr(leisure_concerts, "find_concerts", fake_find)
    bot = object()

    asyncio.run(leisure_concerts.send_concerts_home(bot, "42"))

    assert calls == [(bot, "42", "home", None)]
