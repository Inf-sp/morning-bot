import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot


def test_callback_is_answered_before_tts_handler_starts(monkeypatch):
    events = []

    class Query:
        data = "tts_word:word-id"
        message = SimpleNamespace(chat_id="42", message_id=7, reply_markup=None)

        async def answer(self):
            events.append("answered")

    async def fake_handle(*args, **kwargs):
        events.append("handled")

    monkeypatch.setattr(bot.access, "is_allowed", lambda cid: True)
    monkeypatch.setattr(bot.tracking, "touch", lambda cid: None)
    monkeypatch.setattr(bot.bot_callbacks, "handle", fake_handle)
    update = SimpleNamespace(callback_query=Query())
    context = SimpleNamespace(bot=SimpleNamespace())

    asyncio.run(bot.answer_callback(update, context))

    assert events == ["answered", "handled"]
