import asyncio

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ExtBot

import telegram_runtime


def _markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Другой фильм", callback_data="movie_next")],
        [InlineKeyboardButton("✅ Комедия", callback_data="pref"),
         InlineKeyboardButton("❌ Не добавлять", callback_data="skip")],
        [InlineKeyboardButton("❌ Удалить", callback_data="del"),
         InlineKeyboardButton("2/5", callback_data="noop")],
        [InlineKeyboardButton("✅ Добавить слово", callback_data="add")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_menu")],
    ])


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake_post(self, endpoint, data=None, **_kwargs):
        calls.append(data)
        if getattr(self, "_reject", None) and isinstance(data.get("reply_markup"), dict):
            raise BadRequest(self._reject)
        return True

    monkeypatch.setattr(ExtBot, "_post", fake_post)
    monkeypatch.setattr(telegram_runtime, "_buttons_enhanced", True)
    return calls


def test_add_and_delete_lose_emoji_get_colors_and_add_goes_first(sent):
    bot = telegram_runtime.MenuCleanupBot("1:x")
    asyncio.run(bot._post("sendMessage", {"chat_id": 1, "reply_markup": _markup()}))

    rows = sent[0]["reply_markup"]["inline_keyboard"]
    texts = [[button["text"] for button in row] for row in rows]
    assert texts == [
        ["Добавить слово"], ["✨ Другой фильм"], ["✅ Комедия", "❌ Не добавлять"],
        ["Удалить", "2/5"], ["⬅️ Назад"],
    ]
    styles = {button["text"]: button.get("style") for row in rows for button in row}
    assert styles["Добавить слово"] == "success" and styles["Удалить"] == "danger"
    assert styles["✨ Другой фильм"] is None and styles["✅ Комедия"] is None
    assert rows[3][1] == {"text": "2/5", "disabled": {}}


def test_rejected_styles_fall_back_to_plain_keyboard(sent):
    bot = telegram_runtime.MenuCleanupBot("1:x")
    bot._reject = "Bad Request: can't parse inline keyboard button"

    assert asyncio.run(bot._post("sendMessage", {"chat_id": 1, "reply_markup": _markup()})) is True
    assert isinstance(sent[-1]["reply_markup"], InlineKeyboardMarkup)
    assert telegram_runtime._buttons_enhanced is False


def test_not_modified_error_is_not_retried(sent):
    bot = telegram_runtime.MenuCleanupBot("1:x")
    bot._reject = "Bad Request: message is not modified"

    with pytest.raises(BadRequest):
        asyncio.run(bot._post("editMessageText", {"chat_id": 1, "reply_markup": _markup()}))
    assert len(sent) == 1 and telegram_runtime._buttons_enhanced is True
