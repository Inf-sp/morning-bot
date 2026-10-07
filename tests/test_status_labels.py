import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import pytest

import bot_callbacks
from fakes import RecordingBot


def test_movie_recommendation_keeps_main_screen_while_loading(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def stop(self, delete=True):
            calls.append(("stop", delete))

    async def start_inline(q, bot=None, cid=None, stages=None, preserve_message=False):
        calls.append(("start_inline", preserve_message))
        return Status()

    async def send_recos(bot, cid, kind, status=None):
        calls.append(("send_recos", cid, kind, status))

    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(bot_callbacks.leisure_movies, "send_recos", send_recos)
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)

    class Query:
        data = "movie_reco"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = object()

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert calls[0] == ("start_inline", True)
    assert calls[1][:3] == ("send_recos", "42", "movie")
    assert calls[-1] == ("stop", True)


@pytest.mark.parametrize("data, refresh", [("vg_reco", None), ("vg_next", True)])
def test_game_buttons_open_weekly_pick_and_other_requests_fresh(monkeypatch, data, refresh):
    calls = []

    class Status:
        mode = "inline"

        async def stop(self, delete=True):
            calls.append(("stop", delete))

    async def start_inline(q, bot=None, cid=None, stages=None, preserve_message=False):
        return Status()

    async def send_game_recommendation(bot, cid, **kwargs):
        calls.append((cid, kwargs.get("refresh")))

    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(
        bot_callbacks.leisure_games,
        "send_game_recommendation",
        send_game_recommendation,
    )
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)

    class Query:
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    Query.data = data

    class Update:
        callback_query = Query()

    class Context:
        bot = object()

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert calls[0] == ("42", refresh)


def test_weather_warning_opens_myday_without_replacing_the_warning(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def stop(self, delete=True):
            calls.append(("stop", delete))

    async def start_inline(q, bot=None, cid=None, stages=None, preserve_message=False):
        calls.append(("start_inline", preserve_message))
        return Status()

    async def send_plany(bot, cid, status=None):
        calls.append(("send_plany", cid, status.mode))

    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(bot_callbacks.myday, "send_plany", send_plany)
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)

    class Query:
        data = "weather_myday"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = object()

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert calls == [
        ("start_inline", True),
        ("send_plany", "42", "inline"),
        ("stop", True),
    ]


def test_learning_notification_opens_learning_without_replacing_words(monkeypatch):
    sent = []
    markup = object()

    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.trainer, "cancel", lambda _cid: None)
    monkeypatch.setattr(
        bot_callbacks.menu,
        "menu_screen",
        lambda key, cid: (f"{key}:{cid}", [], markup),
    )

    class Query:
        data = "notify_learning"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = RecordingBot(sent)

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert sent == [{
        "chat_id": "42",
        "text": "m_learn:42",
        "entities": [],
        "reply_markup": markup,
        "transient": True,
    }]


def test_inline_status_starts_with_action_specific_text():
    cases = {
        "m_food_next": "🍽️ Ищу место...",
        "w_look": "⏳ Ищу образ...",
        "movie_reco": "🎬 Ищу кино...",
        "book_reco": "📚 Ищу книгу...",
        "music_g_indie": "🎧 Ищу музыку...",
        "listen_no": "🎧 Ищу музыку...",
        "a_concerts_nl": "🎫 Ищу концерт...",
        "game_again": "🕵️ Ищу загадку...",
        "a_watch": "🎬 Ищу кино...",
        "a_read": "📚 Ищу книгу...",
        "a_listen": "🎧 Ищу музыку...",
        "m_food": "🍽️ Ищу место...",
        "m_wardrobe": "⏳ Ищу образ...",
        "movie_next": "🎬 Ищу кино...",
        "book_next": "📚 Ищу книгу...",
        "music_next": "🎧 Ищу музыку...",
        "vg_next": "👾 Ищу игру...",
        "m_myday": "☀️ Собираю мой день...",
    }

    for data, expected in cases.items():
        assert bot_callbacks._status_stages(data)[0][1] == expected


def test_long_main_screens_have_a_specific_tracking_topic():
    cases = {
        "m_myday": "myday",
        "m_wardrobe": "wardrobe",
        "m_food": "food",
        "m_leisure": "leisure",
        "lz_prem": "leisure",
        "lz_lib": "leisure",
        "notify_learning": "learning",
    }

    for data, expected in cases.items():
        assert bot_callbacks._status_topic(data) == expected


def test_long_inline_actions_have_three_distinct_progress_stages():
    for data in (
        "game_again", "m_food_next", "w_look", "movie_reco", "book_reco",
        "listen_no", "music_g_indie", "a_concerts_nl",
        "a_dictadd_smart_nl", "ex_next_task", "m_myday",
    ):
        stages = bot_callbacks._status_stages(data)
        assert [delay for delay, _text in stages] == [0, 2, 6]
        assert len({text for _delay, text in stages}) == 3


def test_removed_travel_callbacks_open_main_menu(monkeypatch):
    sent = []

    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.menu, "main_menu_screen", lambda _cid: ("menu", [], "kb"))

    for data in ("m_travel", "a_trav_go", "a_trav_country_NL_0", "a_trav_countries_0"):
        query = type("Query", (), {
            "data": data, "message": type("Message", (), {"chat_id": "42", "message_id": 7})(),
        })()
        update = type("Update", (), {"callback_query": query})()
        asyncio.run(bot_callbacks.handle(update, type("Context", (), {"bot": RecordingBot(sent)})(), None))

    assert [(m["text"], m["reply_markup"]) for m in sent] == [("menu", "kb")] * 4
    assert bot_callbacks._status_topic("a_trav_go") is None
