import asyncio

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ExtBot

import telegram_runtime


def _markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё один вариант", callback_data="again")],
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
        ["Ещё один вариант"], ["Добавить слово"], ["Комедия", "Не добавлять"],
        ["Удалить", "2/5"], ["Назад"],
    ]
    styles = {button["text"]: button.get("style") for row in rows for button in row}
    assert styles["Добавить слово"] == "success" and styles["Удалить"] == "danger"
    assert styles["Ещё один вариант"] == "success" and styles["Комедия"] == "success"
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
        [InlineKeyboardButton("✨ Разное", callback_data="cat")],
        [InlineKeyboardButton("✨ Обновить", callback_data="w_look")],
        [InlineKeyboardButton("✨ Подобрать новые слова", callback_data="seed")],
    ])
    asyncio.run(bot._post("sendMessage", {"chat_id": 1, "reply_markup": markup}))

    rows = sent[0]["reply_markup"]["inline_keyboard"]
    texts = [row[0]["text"] for row in rows]
    assert texts == [
        "Обновить", "Подобрать новые слова", "Разное",
    ]
    assert all(row[0].get("style") == "success" for row in rows[:2])
    assert "style" not in rows[2][0]


def test_meaningful_emoji_stay_and_main_menu_is_untouched():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎚️ Мой шкаф", callback_data="w_closet")],
        [InlineKeyboardButton("□ Драма", callback_data="p1"), InlineKeyboardButton("🟢 Яйца", callback_data="f1")],
        [InlineKeyboardButton("⬅️", callback_data="prev"), InlineKeyboardButton("🇳🇱 Нидерланды", callback_data="nl")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    rows = telegram_runtime._enhance_markup(markup)["inline_keyboard"]
    assert [[button["text"] for button in row] for row in rows] == [
        ["Мой шкаф"], ["Драма", "🟢 Яйца"], ["←", "🇳🇱 Нидерланды"], ["Главная"],
    ]
    assert rows[3][0]["style"] == "primary" and "style" not in rows[1][0]  # не выбрано — стандартная

    main = InlineKeyboardMarkup([
        [InlineKeyboardButton("☀️ Мой день", callback_data="m_myday")],
        [InlineKeyboardButton("👔 Гардероб", callback_data="m_wardrobe")],
    ])
    assert telegram_runtime._enhance_markup(main) is None


def test_disable_button_is_red(sent):
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔕 Отключить уведомления", callback_data="off")]])
    bot = telegram_runtime.MenuCleanupBot("1:x")
    asyncio.run(bot._post("sendMessage", {"chat_id": 1, "reply_markup": markup}))

    assert sent[0]["reply_markup"]["inline_keyboard"][0][0] == {
        "text": "Отключить уведомления", "callback_data": "off", "style": "danger",
    }


def test_toggles_become_green_or_standard_without_marks_and_keep_marks_without_color():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 🇳🇱 Нидерландский", callback_data="nl")],
        [InlineKeyboardButton("□ ⭐️ 7+", callback_data="r7")],
        [InlineKeyboardButton("✅ Добавить отмеченные", callback_data="add")],
    ])
    rows = telegram_runtime._enhance_markup(markup)["inline_keyboard"]
    assert [(row[0]["text"], row[0].get("style")) for row in rows] == [
        ("Добавить отмеченные", "success"), ("Нидерландский", "success"), ("7+", None),
    ]

    plain = telegram_runtime._enhance_markup(markup, level=0)["inline_keyboard"]
    assert [row[0]["text"] for row in plain][1:] == ["✅ 🇳🇱 Нидерландский", "□ ⭐️ 7+"]


def test_preferences_button_is_green_right_under_add():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Мои фильмы", callback_data="list")],
        [InlineKeyboardButton("📝 Выбрать предпочтения", callback_data="movie_prefs")],
        [InlineKeyboardButton("✅ Добавить фильм", callback_data="add")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])
    rows = telegram_runtime._enhance_markup(markup)["inline_keyboard"]
    assert [(row[0]["text"], row[0].get("style")) for row in rows] == [
        ("Добавить фильм", "success"), ("Выбрать предпочтения", "success"),
        ("Мои фильмы", None), ("Назад", "primary"),
    ]
    no_add = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Мои фильмы", callback_data="list")],
        [InlineKeyboardButton("📝 Выбрать предпочтения", callback_data="movie_prefs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])
    assert [row[0]["text"] for row in telegram_runtime._enhance_markup(no_add)["inline_keyboard"]] == [
        "Мои фильмы", "Выбрать предпочтения", "Назад"]


def test_forecast_buttons_have_standard_color():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗓️ Полный прогноз на сегодня", callback_data="a_w_full")],
        [InlineKeyboardButton("🗓️ Погода на неделю", callback_data="a_w_week")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    rows = telegram_runtime._enhance_markup(markup)["inline_keyboard"]
    assert [(row[0]["text"], row[0].get("style")) for row in rows] == [
        ("Полный прогноз на сегодня", None), ("Погода на неделю", None), ("Главная", "primary"),
    ]


def test_explicitly_styled_button_keeps_its_place():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("Любой жанр", callback_data="movie_next", api_kwargs={"style": "success"})],
        [InlineKeyboardButton("Драма", callback_data="movie_g_18", api_kwargs={"style": "success"})],
        [InlineKeyboardButton("🆕 Новинка", callback_data="nov_movie", api_kwargs={"style": "success"})],
    ])
    rows = telegram_runtime._enhance_markup(markup)["inline_keyboard"]
    assert [row[0]["text"] for row in rows] == ["Любой жанр", "Драма", "Новинка"]


def test_pager_arrows_and_list_button_have_standard_color():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️", callback_data="p0"), InlineKeyboardButton("2/5", callback_data="noop"),
         InlineKeyboardButton("▶️", callback_data="p2")],
        [InlineKeyboardButton("🔢 Показать списком", callback_data="list")],
    ])
    rows = telegram_runtime._enhance_markup(markup)["inline_keyboard"]
    assert [(b["text"], b.get("style")) for b in rows[0]] == [("←", None), ("2/5", None), ("→", None)]
    assert (rows[1][0]["text"], rows[1][0].get("style")) == ("Показать списком", None)


def test_cancel_button_is_red():
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="cancel")]])
    rows = telegram_runtime._enhance_markup(markup)["inline_keyboard"]
    assert rows[0][0] == {"text": "Отмена", "callback_data": "cancel", "style": "danger"}


def test_main_section_actions_have_standard_color():
    labels = ("✨ Новый образ", "💳 Что докупить", "✨ Новый рецепт", "✨ Новое меню", "✨ Другой фильм",
              "✨ Другая книга", "✨ Другой артист", "🎯 Запустить тренировку",
              "🕵️ Угадать персонажа", "🎬 Фильмы и сериалы", "📚 Книги", "🎧 Музыка",
              "🔄 Обновить карточки")
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"x{i}")]
                                   for i, label in enumerate(labels)])
    rows = telegram_runtime._enhance_markup(markup)["inline_keyboard"]
    assert [row[0].get("style") for row in rows] == [None] * len(labels)
    assert [row[0]["text"] for row in rows] == [label.split(" ", 1)[1] for label in labels]
    # «Ещё загадка» тоже стандартная; остальные «Другой … / Ещё …» по-прежнему зелёные.
    other = telegram_runtime._enhance_markup(InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё загадка", callback_data="m")],
        [InlineKeyboardButton("✨ Ещё один вариант", callback_data="v")]]))["inline_keyboard"]
    styles = {row[0]["text"]: row[0].get("style") for row in other}
    assert styles == {"Ещё загадка": None, "Ещё один вариант": "success"}


def test_leisure_dislike_row_goes_above_other_recommendation():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Другой фильм", callback_data="movie_pick_0")],
        [InlineKeyboardButton("Не нравится", callback_data="movie_no_0", api_kwargs={"style": "danger"})],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])
    rows = telegram_runtime._enhance_markup(markup)["inline_keyboard"]
    assert [row[0]["text"] for row in rows] == ["Не нравится", "Другой фильм", "Назад"]


def test_settings_and_home_stack_into_one_column_home_last():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
         InlineKeyboardButton("🎚️ Настроить", callback_data="lz_lib")],
    ])
    back = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="x"),
                                  InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")]])

    assert [[b["text"] for b in row] for row in telegram_runtime._enhance_markup(markup)["inline_keyboard"]] == [
        ["Настроить"], ["Главная"]]
    assert [[b["text"] for b in row] for row in telegram_runtime._enhance_markup(back)["inline_keyboard"]] == [
        ["Назад", "Главная"]]


def test_cuisine_photo_comes_from_photo_layer_cached_with_wikimedia_fallback(monkeypatch):
    import day_menu
    import travel_photos

    saved, queries = {}, []
    monkeypatch.setattr(day_menu.store, "_load", lambda _key: dict(saved))

    def mutate(_key, change):
        data, _ = change(dict(saved))
        saved.clear()
        saved.update(data)

    monkeypatch.setattr(day_menu.store, "mutate_kv", mutate)
    monkeypatch.setattr(travel_photos, "find_landscape", lambda query: queries.append(query) or {
        "url": "https://images.unsplash.com/fuji", "provider": "unsplash"})

    assert day_menu.cuisine_photo("japanese")["url"] == "https://images.unsplash.com/fuji"
    assert day_menu.cuisine_photo("japanese")["url"] == "https://images.unsplash.com/fuji"
    assert queries == ["Mount Fuji Japan"]  # второй раз — из кэша

    monkeypatch.setattr(travel_photos, "find_landscape", lambda _query: None)
    assert day_menu.cuisine_photo("italian")["url"] == day_menu.CUISINE_IMAGE["italian"]
    assert set(day_menu.CUISINE_SCENE) == set(day_menu.CUISINE_IMAGE) == set(day_menu.CUISINE_INTRO)


def test_day_menu_is_sent_as_photo_with_caption_and_falls_back_to_text():
    import menu
    from ui.builder import MessageSpec

    class Bot:
        def __init__(self):
            self.photos, self.texts = [], []

        async def send_photo(self, **kwargs):
            self.photos.append(kwargs)

        async def send_message(self, **kwargs):
            self.texts.append(kwargs)

    bot = Bot()
    msg = MessageSpec(text="🇯🇵 Меню на сегодня · Японская кухня", entities=[])
    asyncio.run(menu._deliver_menu(bot, "42", msg, {"url": "https://images.unsplash.com/fuji"}))
    assert bot.photos[0]["photo"] == "https://images.unsplash.com/fuji"
    assert bot.photos[0]["caption"] == msg.text and not bot.texts

    long_msg = MessageSpec(text="x" * 1100, entities=[])
    asyncio.run(menu._deliver_menu(bot, "42", long_msg, {"url": "https://images.unsplash.com/fuji"}))
    asyncio.run(menu._deliver_menu(bot, "42", msg, {}))
    assert len(bot.photos) == 1 and len(bot.texts) == 2  # длинная подпись и без фото — текстом
