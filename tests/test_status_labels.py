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

    async def send_recos(bot, cid, kind):
        calls.append(("send_recos", cid, kind))

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
    assert calls[1] == ("send_recos", "42", "movie")
    assert calls[-1] == ("stop", True)


def test_inline_status_starts_with_action_specific_text():
    cases = {
        "m_food_next": "⏳ Ищу рецепт...",
        "w_look": "⏳ Ищу образ...",
        "movie_reco": "🎬 Ищу кино...",
        "book_reco": "📚 Ищу книгу...",
        "listen_no": "🎧 Ищу музыку...",
        "a_concerts_nl": "🎫 Ищу концерт...",
        "a_trav_go": "✈️ Ищу поездку...",
        "game_again": "🕵️ Ищу загадку...",
        "a_watch": "🎬 Ищу кино...",
        "a_read": "📚 Ищу книгу...",
        "a_listen": "🎧 Ищу музыку...",
        "m_food": "⏳ Ищу рецепт...",
        "m_wardrobe": "⏳ Ищу образ...",
        "m_leisure": "🍿 Ищу рекомендации...",
    }

    for data, expected in cases.items():
        assert bot_callbacks._status_stages(data)[0][1] == expected
