import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from telegram.ext import ExtBot

from telegram_runtime import MenuCleanupBot


def test_all_text_messages_force_link_previews_off(monkeypatch):
    calls = []

    async def send_message(_bot, chat_id, *args, **kwargs):
        calls.append((chat_id, args, kwargs))
        return SimpleNamespace(message_id=1, reply_markup=None)

    monkeypatch.setattr(ExtBot, "send_message", send_message)
    bot = MenuCleanupBot(token="123:abc")
    asyncio.run(bot.send_message(
        "42", text="https://example.com",
        link_preview_options=object(),
    ))

    assert calls[0][2]["disable_web_page_preview"] is True
    assert "link_preview_options" not in calls[0][2]


def test_all_edited_messages_force_link_previews_off(monkeypatch):
    calls = []

    async def edit_message_text(_bot, *args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(ExtBot, "edit_message_text", edit_message_text)
    bot = MenuCleanupBot(token="123:abc")
    asyncio.run(bot.edit_message_text(
        "https://example.com", chat_id="42", message_id=7,
        disable_web_page_preview=False,
    ))

    assert calls[0][1]["disable_web_page_preview"] is True
    assert "link_preview_options" not in calls[0][1]
