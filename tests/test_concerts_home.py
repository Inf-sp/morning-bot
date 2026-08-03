import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_concerts
from ui import leisure as leisure_ui


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


def test_concert_country_picker_returns_to_the_concert_list():
    class Bot:
        sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    bot = Bot()
    asyncio.run(leisure_concerts.concert_pick_country(bot, "42"))

    buttons = bot.sent[0]["reply_markup"].inline_keyboard[-1]
    assert [(button.text, button.callback_data) for button in buttons] == [
        ("⬅️ Назад", "a_concerts_find"),
        ("#️⃣ Главная", "m_menu"),
    ]


def test_concerts_card_keeps_classic_text_and_link():
    message = leisure_ui.concerts_list(
        "Концерты · Нидерланды",
        [{
            "artist": "Romy",
            "date": "21 августа 2099",
            "place": "Biddinghuizen",
            "flag": "🇳🇱",
            "context": "Фестиваль · Lowlands 2099",
            "verification": "confirmed",
            "url": "https://example.com/romy",
        }],
    )

    assert "21 августа 2099 · Biddinghuizen 🇳🇱" in message.text
    assert any(entity.type == "text_link" for entity in message.entities)
    assert message.rich_message is None


def test_concerts_keep_the_full_classic_list_instead_of_rich_blocks():
    events = [
        {"artist": artist, "date": f"{index} августа 2099", "place": "Амстердам"}
        for index, artist in enumerate(("Romy", "FKA twigs", "Bicep", "Mitski"), start=1)
    ]

    message = leisure_ui.concerts_list("Концерты · Нидерланды", events)

    assert message.rich_message is None
    assert all(event["artist"] in message.text for event in events)


def test_nearest_concerts_uses_the_full_classic_delivery(monkeypatch):
    event = {
        "id": "romy-2099",
        "_artist": "Romy",
        "_source": "ticketmaster",
        "dates": {"start": {"localDate": "2099-08-21"}},
        "_embedded": {"venues": [{"city": {"name": "Biddinghuizen"}}]},
        "url": "https://example.com/romy",
    }
    monkeypatch.setattr(leisure_concerts, "_ensure_artists", lambda _cid: ["Romy"])
    monkeypatch.setattr(leisure_concerts.store, "get_settings", lambda _cid: {
        "cc": "NL", "country": "Нидерланды",
    })
    monkeypatch.setattr(leisure_concerts, "_concerts_cache_get", lambda _cid, _cc: [event])
    monkeypatch.setattr(leisure_concerts.config, "TICKETMASTER_API_KEY", "test-key")
    monkeypatch.setattr(leisure_concerts.config, "TELEGRAM_RICH_MESSAGES", True)

    class RichBot:
        def __init__(self):
            self.rich = []
            self.classic = []

        async def send_rich_message(self, **kwargs):
            self.rich.append(kwargs)

        async def send_message(self, **kwargs):
            self.classic.append(kwargs)

    rich_bot = RichBot()
    asyncio.run(leisure_concerts.find_concerts(rich_bot, "rich-concerts"))
    assert rich_bot.rich == []
    assert _labels(rich_bot.classic[0]["reply_markup"]) == [
        ["🌍 Нидерланды"], ["⬅️ Назад", "#️⃣ Главная"],
    ]
    assert "Romy" in rich_bot.classic[0]["text"]
    assert rich_bot.classic[0]["disable_web_page_preview"] is True

    class ClassicBot:
        def __init__(self):
            self.classic = []

        async def send_message(self, **kwargs):
            self.classic.append(kwargs)

    classic_bot = ClassicBot()
    asyncio.run(leisure_concerts.find_concerts(classic_bot, "classic-concerts"))
    assert len(classic_bot.classic) == 1
    assert _labels(classic_bot.classic[0]["reply_markup"]) == [
        ["🌍 Нидерланды"], ["⬅️ Назад", "#️⃣ Главная"],
    ]
    assert "Romy" in classic_bot.classic[0]["text"]
    assert classic_bot.classic[0]["disable_web_page_preview"] is True
