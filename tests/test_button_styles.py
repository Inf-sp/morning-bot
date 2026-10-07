import asyncio

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ExtBot

import telegram_runtime


def _markup():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Добавить слово", callback_data="add"),
        InlineKeyboardButton("❌ Удалить", callback_data="del"),
        InlineKeyboardButton("2/5", callback_data="noop"),
        InlineKeyboardButton("⬅️ Назад", callback_data="m_menu"),
    ]])


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


def test_buttons_get_contract_colors_and_noop_becomes_disabled(sent):
    bot = telegram_runtime.MenuCleanupBot("1:x")
    asyncio.run(bot._post("sendMessage", {"chat_id": 1, "reply_markup": _markup()}))

    row = sent[0]["reply_markup"]["inline_keyboard"][0]
    assert [button.get("style") for button in row] == ["success", "danger", None, None]
    assert row[2] == {"text": "2/5", "disabled": {}}
    assert row[3]["callback_data"] == "m_menu"


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
