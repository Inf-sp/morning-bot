import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot_callbacks


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
    monkeypatch.setattr(bot_callbacks.balance.thoughts, "cancel_capture", lambda _cid: None)

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


def test_inline_status_starts_with_action_specific_text():
    cases = {
        "m_food_next": "⏳ Ищу рецепт...",
        "w_look": "⏳ Ищу образ...",
        "movie_reco": "🎬 Ищу кино...",
        "book_reco": "📚 Ищу книгу...",
        "music_g_indie": "🎧 Ищу музыку...",
        "listen_no": "🎧 Ищу музыку...",
        "a_concerts_nl": "🎫 Ищу концерт...",
        "a_trav_go": "✈️ Ищу поездку...",
        "game_again": "🕵️ Ищу загадку...",
        "a_watch": "🎬 Ищу кино...",
        "a_read": "📚 Ищу книгу...",
        "a_listen": "🎧 Ищу музыку...",
        "m_food": "⏳ Ищу рецепт...",
        "m_wardrobe": "⏳ Ищу образ...",
        "m_movie": "🎬 Ищу кино...",
        "m_books": "📚 Ищу книгу...",
        "m_music": "🎧 Ищу музыку...",
        "m_myday": "☀️ Собираю мой день...",
    }

    for data, expected in cases.items():
        assert bot_callbacks._status_stages(data)[0][1] == expected


def test_long_main_screens_have_a_specific_tracking_topic():
    cases = {
        "m_myday": "myday",
        "m_wardrobe": "wardrobe",
        "m_food": "food",
        "m_movie": "leisure",
        "m_books": "leisure",
        "m_music": "leisure",
        "m_travel": "travel",
    }

    for data, expected in cases.items():
        assert bot_callbacks._status_topic(data) == expected


def test_long_inline_actions_have_three_distinct_progress_stages():
    for data in (
        "game_again", "m_food_next", "w_look", "movie_reco", "book_reco",
        "listen_no", "music_g_indie", "a_concerts_nl", "a_trav_go", "a_trav_country_NL_0",
        "a_dictadd_smart_nl", "ex_next_task", "m_myday",
    ):
        stages = bot_callbacks._status_stages(data)
        assert [delay for delay, _text in stages] == [0, 2, 6]
        assert len({text for _delay, text in stages}) == 3


def test_thought_review_keeps_health_menu_visible_while_loading(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def stop(self, delete=True):
            calls.append(("stop", delete))

    async def start_inline(q, bot=None, cid=None, stages=None, preserve_message=False):
        calls.append(("start_inline", stages[0][1], preserve_message))
        return Status()

    async def handle_health_callback(bot, cid, q, data, status=None):
        calls.append(("health", cid, data, status))

    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(bot_callbacks.balance, "handle_callback", handle_health_callback)
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.balance.thoughts, "cancel_capture", lambda _cid: None)

    class Query:
        data = "as_daycheck"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = object()

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert calls[0] == ("start_inline", "🧠 Разбираю мысль...", True)
    assert calls[1] == ("health", "42", "as_daycheck", calls[1][3])
    assert calls[1][3].mode == "inline"
    assert calls[-1] == ("stop", True)


def test_saved_country_card_keeps_travel_list_visible_while_loading(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def stop(self, delete=True):
            calls.append(("stop", delete))

    async def start_inline(q, bot=None, cid=None, stages=None, preserve_message=False):
        calls.append(("start_inline", stages[0][1], preserve_message))
        return Status()

    async def handle_country_callback(bot, cid, q, act, status=None):
        calls.append(("country", cid, act, status))

    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(bot_callbacks.travel, "handle_country_callback", handle_country_callback)
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.balance.thoughts, "cancel_capture", lambda _cid: None)

    class Query:
        data = "a_trav_country_NL_0"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = object()

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert calls[0] == ("start_inline", "🗺️ Открываю страну...", True)
    assert calls[1] == ("country", "42", "trav_country_NL_0", calls[1][3])
    assert calls[1][3].mode == "inline"
    assert calls[-1] == ("stop", True)
