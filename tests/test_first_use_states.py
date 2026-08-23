import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import dictionary_seed
import dictionary_seed_ui
import bot_callbacks
import bot_text
import cooking
import fridge
import learning
import learning_dictionary
import learning_settings
import menu
import onboard
import settings
import store
import wardrobe
from ui import menu as menu_ui


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_learning_empty_state_has_one_clear_next_step():
    message = menu_ui.learning_menu({"has_material": False, "lang_code": "nl"})

    assert message.text == (
        "🧠 Обучение\n\n"
        "Добавляй сюда слова и фразы, которые хочешь запомнить.\n\n"
        "Можно просто написать мне в чате:\n"
        "«добавь в словарь wennen aan»\n\n"
        "Я буду использовать их в практике и повторении."
    )
    assert _labels(message.reply_markup) == [
        ["🆕 Добавить слова"],
        ["✨ Подобрать новые слова"],
    ]


def test_learning_entry_shows_empty_state_without_starting_seed(monkeypatch):
    class Message:
        chat_id = 42
        updated = None

        async def edit_text(self, text, **kwargs):
            self.updated = {"text": text, **kwargs}

    class Query:
        data = "m_learn"
        message = Message()

    class Bot:
        async def send_message(self, **_kwargs):
            raise AssertionError("empty learning state should replace the current menu")

    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.trainer, "cancel", lambda _cid: None)
    monkeypatch.setattr(learning, "build_learning_home", lambda _cid: {
        "has_material": False, "lang_code": "nl",
    })

    import asyncio

    asyncio.run(bot_callbacks.handle(
        SimpleNamespace(callback_query=Query()), SimpleNamespace(bot=Bot()), lambda: None,
    ))

    assert Query.message.updated["text"].startswith("🧠 Обучение\n\nДобавляй сюда слова")
    assert _labels(Query.message.updated["reply_markup"]) == [
        ["🆕 Добавить слова"], ["✨ Подобрать новые слова"],
    ]


def test_learning_home_keeps_trainer_and_detective_as_wide_actions():
    message = menu_ui.learning_menu({
        "has_material": True, "lang_code": "nl", "kind": "word",
        "term": "morgen", "translation": "завтра", "progress": {},
        "focus": "вспомнить перевод до открытия спойлера.",
        "live_language": {
            "text": "Laat maar", "translation": "Ладно, забудь",
            "meaning": "Когда решаешь не продолжать тему.",
        },
        "daily_practice": {
            "entries": [{"term": "Inmiddels", "translation": "Уже"}],
            "tip": "Не должно появиться на главном экране.",
        },
    })

    assert "Живой язык: Laat maar" in message.text
    assert "Inmiddels" not in message.text
    assert "Как запомнить" not in message.text
    assert "🎯 Задание" not in message.text
    assert _labels(message.reply_markup) == [
        ["🎯 Практика изучения языка"],
        ["🕵️ Угадай персонажа"],
        ["🎚️ Мой словарь"],
        ["#️⃣ Главная"],
    ]
    assert "• В изучении 0 слов и фраз" in message.text
    assert "• Повторить сегодня — 0" in message.text
    assert "• Без подсказок — 0%" in message.text
    assert "Фраза дня" not in message.text
    assert "Слово дня" not in message.text
    assert "Живой язык: Laat maar → Ладно, забудь." in message.text
    assert "Когда говорят?" not in message.text
    assert "Когда решаешь не продолжать тему." not in message.text
    spoiler_texts = [
        message.text.encode("utf-16-le")[entity.offset * 2:(entity.offset + entity.length) * 2].decode("utf-16-le")
        for entity in message.entities
        if entity.type == "spoiler"
    ]
    assert spoiler_texts == ["Ладно, забудь."]


def test_dictionary_contains_only_dictionary_actions(monkeypatch):
    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    monkeypatch.setattr(learning_dictionary, "_dict_lang_entries", lambda *_args: [])
    bot = Bot()

    asyncio.run(learning_dictionary.send_dict_lang(bot, "42", "nl"))

    rows = _labels(bot.message["reply_markup"])
    assert "📝 Предпочтения" not in [label for row in rows for label in row]
    assert rows[-3:] == [
        ["✨ Подобрать новые слова"],
        ["🆕 Добавить слово"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_dictionary_home_opens_categories_instead_of_old_word_grid(monkeypatch):
    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    entries = [
        {"id": "1", "term": "Mooi", "pos": "adjective"},
        {"id": "2", "term": "Lopen", "pos": "verb"},
        {"id": "3", "term": "Huis", "pos": "noun"},
        {"id": "4", "term": "Ik", "pos": "pronoun"},
        {"id": "5", "term": "Snel", "pos": "adverb"},
        {"id": "6", "term": "Met", "pos": "preposition"},
        {"id": "7", "term": "Hoe gaat het?", "pos": "sentence"},
    ]
    monkeypatch.setattr(learning_dictionary, "_dict_lang_entries", lambda *_args: entries)
    bot = Bot()

    asyncio.run(learning_dictionary.send_dict_lang(bot, "42", "nl"))

    rows = _labels(bot.message["reply_markup"])
    assert rows[:6] == [
        [f"{category} · 1"]
        for category in learning_dictionary._DICT_VISIBLE_CATEGORY_ORDER
    ]
    assert not any("Местоимения" in label for row in rows for label in row)
    assert not any("Mooi" in label or "Lopen" in label for row in rows for label in row)


def test_dictionary_pagination_shows_current_page(monkeypatch):
    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    entries = [
        {"id": str(index), "term": f"Word {index}", "pos": "noun"}
        for index in range(21)
    ]
    monkeypatch.setattr(learning_dictionary, "_dict_lang_entries", lambda *_args: entries)
    bot = Bot()

    asyncio.run(learning_dictionary.send_dict_category(bot, "42", "nl", 2, page=1))

    assert ["◀️", "2 / 3", "▶️"] in _labels(bot.message["reply_markup"])


def test_learning_preferences_return_to_active_dictionary():
    keyboard = learning_settings.learning_settings_kb("нидерландский", "simple", back="a_dictlang_active")

    assert keyboard.inline_keyboard[0][0].callback_data == "set_learning_language_nl_dict"
    assert keyboard.inline_keyboard[1][0].callback_data == "set_learning_language_en_dict"
    assert keyboard.inline_keyboard[2][0].callback_data == "set_learning_language_none_dict"
    assert all(len(row) == 1 for row in keyboard.inline_keyboard[:-1])
    assert keyboard.inline_keyboard[-1][0].callback_data == "a_dictlang_active"


def test_learning_level_picker_has_two_levels_and_returns_to_language_selection():
    keyboard = learning_settings.learning_level_kb("simple", back="a_dictlang_active")

    assert [row[0].text for row in keyboard.inline_keyboard[:2]] == [
        "✅ 🔽 Простой (A1 - A2)",
        "🔼 Сложный (B1+)",
    ]
    assert [row[0].callback_data for row in keyboard.inline_keyboard[:2]] == [
        "set_learning_level_simple_dict",
        "set_learning_level_hard_dict",
    ]
    assert keyboard.inline_keyboard[-1][0].callback_data == "set_learning_dict"


def test_onboarding_and_dictionary_seed_show_only_two_complexities():
    assert _labels(onboard._lvl_kb("nl")) == [
        ["🔽 Простой (A1 - A2)"],
        ["🔼 Сложный (B1+)"],
    ]
    assert _labels(dictionary_seed_ui.level_keyboard("nl", "hard"))[:2] == [
        ["🔽 Простой (A1 - A2)"],
        ["✅ 🔼 Сложный (B1+)"],
    ]


def test_learning_preferences_can_disable_language_study():
    keyboard = learning_settings.learning_settings_kb("", "")
    labels = [row[0].text for row in keyboard.inline_keyboard[:-1]]

    assert labels == ["🇳🇱 Нидерландский", "🇬🇧 Английский", "✅ 🚫 Не изучаю"]


def test_learning_preferences_from_settings_return_to_settings():
    keyboard = learning_settings.learning_settings_kb("", "", back="m_settings")

    assert keyboard.inline_keyboard[0][0].callback_data == "set_learning_language_nl_settings"
    assert keyboard.inline_keyboard[-1][0].callback_data == "m_settings"


def test_cuisine_preferences_use_one_column():
    keyboard = settings._cuisines_kb("42")

    assert all(len(row) == 1 for row in keyboard.inline_keyboard[:-1])


def test_seed_intro_uses_the_same_learning_empty_state_copy(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(dictionary_seed, "_seed_language", lambda *_args: ("nl", "нидерландский", "simple"))

    asyncio.run(dictionary_seed.send_seed_intro(Bot(), "42"))

    assert sent[0]["text"].startswith("🧠 Обучение\n\nДобавляй сюда слова и фразы")
    assert _labels(sent[0]["reply_markup"]) == [
        ["🆕 Добавить слова"], ["✨ Подобрать новые слова"],
    ]


def test_empty_fridge_opens_the_fill_state_without_recipe_generation(monkeypatch):
    class QueryMessage:
        updated = None

        async def edit_text(self, text, **kwargs):
            self.updated = {"text": text, **kwargs}

    class Query:
        message = QueryMessage()

    class Bot:
        async def send_message(self, **_kwargs):
            raise AssertionError("empty state should replace the current screen")

    monkeypatch.setattr(menu, "has_available_fridge", lambda _cid: False)

    asyncio.run(menu.send_food_menu(Bot(), "42", q=Query()))

    assert Query.message.updated["text"] == (
        "🥣 Готовка\n\n"
        "Добавь продукты, которые обычно есть дома.\n\n"
        "Я буду подбирать простые рецепты из них и показывать, чего не хватает."
    )
    assert _labels(Query.message.updated["reply_markup"]) == [
        ["🧊 Заполнить холодильник"],
        ["#️⃣ Главная"],
    ]


def test_empty_fridge_check_reads_the_actual_fridge_store(monkeypatch):
    calls = []

    def get_list(key, cid):
        calls.append((key, cid))
        return []

    monkeypatch.setattr(menu.store, "get_list", get_list)

    assert menu.has_available_fridge("42") is False
    assert calls == [(menu.config.FRIDGE_KEY, "42")]


def test_onboarding_creates_a_level_only_for_the_selected_language(monkeypatch):
    selected_languages = []
    level_calls = []
    asked = []
    finished = []
    onboard._ob.clear()

    monkeypatch.setattr(store, "set_learning_language", lambda _cid, code: selected_languages.append(code))
    monkeypatch.setattr(settings, "set_", lambda *_args: None)
    monkeypatch.setattr(store, "ensure_level", lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected")))

    async def ask_level(_bot, _cid, _q, code):
        asked.append(code)

    monkeypatch.setattr(onboard, "_ask_level", ask_level)

    asyncio.run(onboard.handle_callback(object(), "42", object(), "ob_lang_nl"))

    assert selected_languages == ["nl"]
    assert asked == ["nl"]

    monkeypatch.setattr(store, "set_level", lambda *args: level_calls.append(args))

    async def finish(_bot, cid):
        finished.append(cid)

    monkeypatch.setattr(onboard, "_finish", finish)
    asyncio.run(onboard.handle_callback(object(), "42", object(), "ob_lvl_nl_simple"))

    assert level_calls == [("42", "нидерландский", "simple")]
    assert finished == ["42"]
    onboard._ob.clear()


def test_fill_wardrobe_returns_to_the_normal_home_after_saving(monkeypatch):
    opened = []

    async def parse(_text):
        return [{"name": "Белая футболка"}]

    async def send_home(_bot, cid):
        opened.append(cid)

    monkeypatch.setattr(wardrobe, "_parse_items", parse)
    monkeypatch.setattr(wardrobe.store, "add_wardrobe_items", lambda *_args: [{"name": "Белая футболка"}])
    monkeypatch.setattr(wardrobe, "send_home", send_home)

    asyncio.run(wardrobe.add_item(object(), "42", "Белая футболка", return_to_home=True))

    assert opened == ["42"]


def _prepare_pending_text_router(monkeypatch):
    async def no_async_match(*_args, **_kwargs):
        return False

    monkeypatch.setattr(bot_text.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_text.tracking, "touch", lambda _cid: None)
    monkeypatch.setattr(bot_text.assistant, "try_add_lifehack_from_chat", no_async_match)
    monkeypatch.setattr(bot_text.assistant, "try_edit_lifehack_from_chat", no_async_match)
    monkeypatch.setattr(bot_text.dictionary_import, "try_add_dict_from_chat", no_async_match)


def test_fill_wardrobe_text_input_opens_normal_home(monkeypatch):
    cid = "first-wardrobe-text"
    sent = []
    opened = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def parse(_text):
        return [{"name": "Белая футболка"}]

    async def send_home(_bot, routed_cid):
        opened.append(routed_cid)

    monkeypatch.setattr(wardrobe, "_parse_items", parse)
    monkeypatch.setattr(wardrobe.store, "add_wardrobe_items", lambda *_args: [{"name": "Белая футболка"}])
    monkeypatch.setattr(wardrobe, "send_home", send_home)
    _prepare_pending_text_router(monkeypatch)

    bot = Bot()
    asyncio.run(wardrobe.handle_callback(bot, cid, None, "w_fill"))
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=cid),
        message=SimpleNamespace(text="Белая футболка"),
    )
    asyncio.run(bot_text.handle(
        update,
        SimpleNamespace(bot=bot),
        lambda *_args: asyncio.sleep(0),
    ))

    assert opened == [cid]
    assert cid not in store.pending_input
    assert [message["text"] for message in sent] == [
        "Пришли список всей своей одежды одним сообщением — я сам разложу всё по шкафу.",
    ]


def test_first_fridge_fill_returns_to_normal_cooking_home(monkeypatch):
    """The first product list must finish on a recipe, not a fridge status screen."""

    saved = []
    opened = []
    sent = []

    monkeypatch.setattr(fridge.store, "get_list", lambda _key, _cid: list(saved))
    monkeypatch.setattr(
        fridge.store,
        "set_list",
        lambda _key, _cid, values: saved.__setitem__(slice(None), values),
    )

    async def send_food_home(_bot, cid, *, refresh=False, **_kwargs):
        opened.append((str(cid), refresh))

    async def unexpected_fridge_screen(*_args, **_kwargs):
        raise AssertionError("first fill must not end on the fridge screen")

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(menu, "send_food_menu", send_food_home)
    monkeypatch.setattr(fridge, "send_fridge", unexpected_fridge_screen)

    asyncio.run(fridge.fridge_add_done(Bot(), "42", "курица, рис"))

    assert opened == [("42", True)]
    assert sent == []


def test_first_fridge_fill_text_input_opens_normal_cooking_home(monkeypatch):
    cid = "first-fridge-text"
    saved = []
    sent = []
    opened = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(fridge.store, "get_list", lambda _key, _cid: list(saved))
    monkeypatch.setattr(
        fridge.store,
        "set_list",
        lambda _key, _cid, values: saved.__setitem__(slice(None), values),
    )

    async def send_food_home(_bot, routed_cid, *, refresh=False, **_kwargs):
        opened.append((str(routed_cid), refresh))

    monkeypatch.setattr(menu, "send_food_menu", send_food_home)
    _prepare_pending_text_router(monkeypatch)

    bot = Bot()
    asyncio.run(cooking.handle_callback(bot, cid, None, "as_fridge_add"))
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=cid),
        message=SimpleNamespace(text="курица, рис"),
    )
    asyncio.run(bot_text.handle(
        update,
        SimpleNamespace(bot=bot),
        lambda *_args: asyncio.sleep(0),
    ))

    assert opened == [(cid, True)]
    assert cid not in store.pending_input
    assert [message["text"] for message in sent] == [
        "✏️ Напиши продукты через запятую или с новой строки — добавлю в список.",
    ]


def test_first_fridge_fill_opens_cooking_after_category_choice(monkeypatch):
    cid = "first-fridge-category"
    saved = []
    opened = []

    class Bot:
        async def send_message(self, **_kwargs):
            return None

    monkeypatch.setattr(fridge.store, "get_list", lambda _key, _cid: list(saved))
    monkeypatch.setattr(
        fridge.store,
        "set_list",
        lambda _key, _cid, values: saved.__setitem__(slice(None), values),
    )

    async def send_food_home(_bot, routed_cid, *, refresh=False, **_kwargs):
        opened.append((str(routed_cid), refresh))

    monkeypatch.setattr(menu, "send_food_menu", send_food_home)
    fridge._pending_category_choices.clear()

    asyncio.run(fridge.fridge_add_done(Bot(), cid, "дуриан"))
    asyncio.run(fridge.fridge_assign_category(Bot(), cid, 1))

    assert opened == [(cid, True)]
