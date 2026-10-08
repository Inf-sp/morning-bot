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
    monkeypatch.setattr(telegram_runtime, "_button_level", 2)
    return calls


def test_add_and_delete_lose_emoji_get_colors_and_add_goes_first(sent):
    bot = telegram_runtime.MenuCleanupBot("1:x")
    asyncio.run(bot._post("sendMessage", {"chat_id": 1, "reply_markup": _markup()}))

    rows = sent[0]["reply_markup"]["inline_keyboard"]
    texts = [[button["text"] for button in row] for row in rows]
    assert texts == [
        ["Другой фильм"], ["Добавить слово"], ["✅ Комедия", "Не добавлять"],
        ["Удалить", "2/5"], ["Назад"],
    ]
    styles = {button["text"]: button.get("style") for row in rows for button in row}
    assert styles["Добавить слово"] == "success" and styles["Удалить"] == "danger"
    assert styles["Другой фильм"] == "success" and styles["✅ Комедия"] is None
    assert styles["Назад"] == "primary"
    assert rows[3][1] == {"text": "2/5", "disabled": {}}


def test_waiting_indicator_is_green_and_disabled(sent):
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🍳 Подбираю рецепт...", callback_data="noop")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_menu")],
    ])
    bot = telegram_runtime.MenuCleanupBot("1:x")
    asyncio.run(bot._post("sendMessage", {"chat_id": 1, "reply_markup": markup}))

    rows = sent[0]["reply_markup"]["inline_keyboard"]
    assert rows[0][0] == {"text": "Подбираю рецепт...", "style": "success", "disabled": {}}


def test_rejected_disabled_field_keeps_colors_and_no_emoji(sent, monkeypatch):
    monkeypatch.setattr(telegram_runtime, "_button_level", 2)
    calls = []

    async def fake_post(self, endpoint, data=None, **_kwargs):
        calls.append(data)
        rows = (data.get("reply_markup") or {}).get("inline_keyboard", []) \
            if isinstance(data.get("reply_markup"), dict) else []
        if any("disabled" in button for row in rows for button in row):
            raise BadRequest("Bad Request: can't parse inline keyboard button: disabled")
        return True

    monkeypatch.setattr(ExtBot, "_post", fake_post)
    bot = telegram_runtime.MenuCleanupBot("1:x")

    assert asyncio.run(bot._post("sendMessage", {"chat_id": 1, "reply_markup": _markup()})) is True
    rows = calls[-1]["reply_markup"]["inline_keyboard"]
    buttons = {button["text"]: button for row in rows for button in row}
    assert buttons["Удалить"]["style"] == "danger"
    assert buttons["2/5"]["callback_data"] == "noop"
    assert telegram_runtime._button_level == 1


def test_non_keyboard_error_goes_straight_to_original_request(sent):
    bot = telegram_runtime.MenuCleanupBot("1:x")
    bot._reject = "Bad Request: wrong file identifier/HTTP URL specified"

    assert asyncio.run(bot._post("sendPhoto", {"chat_id": 1, "reply_markup": _markup()})) is True
    assert len(sent) == 2
    assert isinstance(sent[-1]["reply_markup"], InlineKeyboardMarkup)
    assert telegram_runtime._button_level == 2


def test_not_modified_error_is_not_retried(sent):
    bot = telegram_runtime.MenuCleanupBot("1:x")
    bot._reject = "Bad Request: message is not modified"

    with pytest.raises(BadRequest):
        asyncio.run(bot._post("editMessageText", {"chat_id": 1, "reply_markup": _markup()}))
    assert len(sent) == 1 and telegram_runtime._buttons_enhanced is True


def test_refresh_buttons_are_green_without_emoji_and_on_top(sent):
    bot = telegram_runtime.MenuCleanupBot("1:x")
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Что докупить", callback_data="w_buy")],
        [InlineKeyboardButton("✨ Разное", callback_data="cat")],
        [InlineKeyboardButton("✨ Обновить", callback_data="w_look")],
        [InlineKeyboardButton("✨ Подобрать новые слова", callback_data="seed")],
        [InlineKeyboardButton("🔄 Обновить карточки", callback_data="adm")],
    ])
    asyncio.run(bot._post("sendMessage", {"chat_id": 1, "reply_markup": markup}))

    rows = sent[0]["reply_markup"]["inline_keyboard"]
    texts = [row[0]["text"] for row in rows]
    assert texts == [
        "Обновить", "Подобрать новые слова", "Обновить карточки", "Что докупить", "Разное",
    ]
    assert all(row[0].get("style") == "success" for row in rows[:3])
    assert "style" not in rows[4][0]


def test_meaningful_emoji_stay_and_main_menu_is_untouched():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎚️ Мой шкаф", callback_data="w_closet")],
        [InlineKeyboardButton("□ Драма", callback_data="p1"), InlineKeyboardButton("🟢 Яйца", callback_data="f1")],
        [InlineKeyboardButton("⬅️", callback_data="prev"), InlineKeyboardButton("🇳🇱 Нидерланды", callback_data="nl")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    rows = telegram_runtime._enhance_markup(markup)["inline_keyboard"]
    assert [[button["text"] for button in row] for row in rows] == [
        ["Мой шкаф"], ["□ Драма", "🟢 Яйца"], ["⬅️", "🇳🇱 Нидерланды"], ["Главная"],
    ]
    assert rows[3][0]["style"] == "primary"

    main = InlineKeyboardMarkup([
        [InlineKeyboardButton("☀️ Мой день", callback_data="m_myday")],
        [InlineKeyboardButton("🧵 Гардероб", callback_data="m_wardrobe")],
    ])
    assert telegram_runtime._enhance_markup(main) is None
