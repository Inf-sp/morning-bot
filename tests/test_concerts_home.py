import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_concerts


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_concerts_home_opens_nearest_events_instead_of_an_intro(monkeypatch):
    calls = []

    async def fake_find(bot, cid, mode="home", artists_override=None):
        calls.append((bot, cid, mode, artists_override))

    monkeypatch.setattr(leisure_concerts, "find_concerts", fake_find)
    bot = object()

    asyncio.run(leisure_concerts.send_concerts_home(bot, "42"))

    assert calls == [(bot, "42", "home", None)]


def test_concerts_screen_has_no_artist_search_or_favorites(monkeypatch):
    class Bot:
        sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    monkeypatch.setattr(leisure_concerts, "_ensure_artists", lambda _cid: ["Romy"])
    monkeypatch.setattr(leisure_concerts.config, "TICKETMASTER_API_KEY", "")
    bot = Bot()

    asyncio.run(leisure_concerts.find_concerts(bot, "42"))

    labels = _labels(bot.sent[0]["reply_markup"])
    flat = [label for row in labels for label in row]
    assert "🔍 Найти артиста" not in flat
    assert "❤️ Любимые артисты" not in flat
