import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import dictionary_seed
import leisure_home
import menu
import onboard
import settings
import store
import wardrobe
import balance
from ui import menu as menu_ui


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_learning_empty_state_has_one_clear_next_step():
    message = menu_ui.learning_menu({"has_material": False, "lang_code": "nl"})

    assert message.text == (
        "📚 Обучение\n\n"
        "Добавляй сюда слова и фразы, которые хочешь запомнить.\n\n"
        "Можно просто написать мне в чате:\n"
        "«добавь в словарь wennen aan»\n\n"
        "Я буду использовать их в тренажёре и повторении."
    )
    assert _labels(message.reply_markup) == [
        ["🆕 Добавить слова"],
        ["✨ Подобрать слова"],
    ]


def test_seed_intro_uses_the_same_learning_empty_state_copy(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(dictionary_seed, "_seed_language", lambda *_args: ("nl", "нидерландский", "simple"))

    asyncio.run(dictionary_seed.send_seed_intro(Bot(), "42"))

    assert sent[0]["text"].startswith("📚 Обучение\n\nДобавляй сюда слова и фразы")
    assert _labels(sent[0]["reply_markup"]) == [
        ["🆕 Добавить слова"], ["✨ Подобрать слова"],
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


def test_first_leisure_screen_needs_no_preferences(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def no_movies(*_args, **_kwargs):
        return []

    monkeypatch.setattr(leisure_home.store, "get_settings", lambda _cid: {"city": "Алкмар", "cc": "NL"})
    monkeypatch.setattr(leisure_home.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_home.leisure_concerts, "_concerts_cache_get", lambda *_args: [])
    monkeypatch.setattr(leisure_home.leisure_music, "_cached_artist", lambda *_args: None)
    monkeypatch.setattr(leisure_home.leisure_books, "_cached_book", lambda *_args: None)
    monkeypatch.setattr(leisure_home.leisure_movies, "get_local_now_playing", no_movies)

    asyncio.run(leisure_home.send_home(Bot(), "42"))

    assert sent[0]["text"] == (
        "🍿 Развлечения на сегодня · Алкмар\n\n"
        "Выбери кино, музыку или книгу — подберу что-то на сегодня.\n\n"
        "💡 Добавь любимые фильмы, артистов и книги в Предпочтениях — рекомендации станут точнее."
    )
    assert _labels(sent[0]["reply_markup"]) == [
        ["🎬 Кино", "🎧 Музыка", "📖 Книги"],
        ["💾 Сохранения", "🎚️ Предпочтения"],
        ["#️⃣ Главная"],
    ]


def test_health_home_opens_without_first_use_data(monkeypatch):
    monkeypatch.setattr(balance, "health_focus", lambda _cid: {
        "phrase": "Сделай короткую паузу.",
        "steps": ["Выпей воды."],
        "tip": "Не планируй всё сразу.",
    })

    text, _entities, markup = menu.menu_screen("m_balance", "42")

    assert text.startswith("⚡️ Фокус на сегодня · Здоровье\n\nСделай короткую паузу.")
    assert _labels(markup) == [
        ["👩🏻‍⚕️ Врач"],
        ["😮‍💨 Мысли"],
        ["🎚️ Предпочтения"],
        ["#️⃣ Главная"],
    ]


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
