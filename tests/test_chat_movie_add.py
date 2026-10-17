import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import assistant
import leisure_collection
import telegram_runtime


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs["text"])


def test_add_series_without_word_favorites_is_an_add_not_a_recommendation():
    assert assistant._detect_love_add("Добавить сериал Шитс крик") == ("movies", "Фильмы и сериалы", "Шитс крик")
    assert assistant._detect_love_add("люблю фильмы про космос") is None


def test_question_about_a_series_goes_to_assistant_not_recommendation():
    assert assistant._detect_intent("Что за сериал шитс крик") is None
    assert assistant._detect_intent("кто такой Дени Вильнёв") is None
    assert assistant._detect_intent("посоветуй сериал") == "tv"  # карточка сериала, не фильма


def test_chat_add_saves_verified_title_or_asks_to_clarify(monkeypatch):
    saved = []
    monkeypatch.setattr(assistant.store, "get_list", lambda *_a: [])
    monkeypatch.setattr(assistant.store, "add_to_list", lambda _key, _cid, value: saved.append(value))
    monkeypatch.setattr(leisure_collection, "_resolve_movie_label",
                        lambda title, allow_ai=False: {"name": "Шиттс Крик", "kind": "tv", "year": "2015"}
                        if allow_ai else None)
    bot = Bot()

    opened = []

    async def open_card(_bot, cid, title):
        opened.append((cid, title))
        return True

    import leisure_movies
    monkeypatch.setattr(leisure_movies, "open_favorite_movie", open_card)
    assert asyncio.run(assistant.try_add_love_from_chat(bot, "42", "Добавить сериал Шитс крик"))
    assert saved == ["Шиттс Крик (сериал, 2015)"]
    # Один фильм — сразу после подтверждения открывается его карточка в «Моём кино».
    assert opened == [("42", "Шиттс Крик (сериал, 2015)")]

    monkeypatch.setattr(leisure_collection, "_resolve_movie_label", lambda *_a, **_k: None)
    assert asyncio.run(assistant.try_add_love_from_chat(bot, "42", "Добавь сериал Абвгд"))
    assert saved == ["Шиттс Крик (сериал, 2015)"] and bot.sent[-1].startswith("Не нашёл «Абвгд»")
    assert len(opened) == 1  # не нашли — карточки нет


def test_library_add_commands_reach_the_right_tab():
    detect = assistant._detect_love_add
    assert detect("Добавить музыку Radiohead") == ("artists", "Музыка", "Radiohead")
    assert detect("добавить фильмы Дюна") == ("movies", "Фильмы и сериалы", "Дюна")
    assert detect("Добавить фильмы или сериалы Матрица") == ("movies", "Фильмы и сериалы", "Матрица")
    assert detect("Добавить музыку") == ("artists", "Музыка", "")  # без названия — открыть добавление
    assert detect("добавь фильм в словарь") is None  # это словарь
    assert detect("Добавить книги Мастер и Маргарита") is None  # книг в библиотеке больше нет
    assert detect("люблю фильмы про космос") is None


def test_library_add_saves_a_list_and_reports_once(monkeypatch):
    saved = []
    monkeypatch.setattr(assistant.store, "get_list", lambda *_a: ["Placebo"])
    monkeypatch.setattr(assistant.store, "add_to_list", lambda _key, _cid, value: saved.append(value))
    bot = Bot()

    assert asyncio.run(assistant.try_add_love_from_chat(bot, "42", "добавить музыку: Muse, Placebo\nRadiohead"))
    assert saved == ["Muse", "Radiohead"]
    assert bot.sent == ["❤️ Добавил в библиотеку · Музыка: «Muse», «Radiohead».\nУже в библиотеке: «Placebo»."]


def test_library_add_without_title_opens_the_tab_add_flow(monkeypatch):
    import personal_collections
    started = []

    async def start(_bot, cid, key, origin="base"):
        started.append((cid, key, origin))

    monkeypatch.setattr(personal_collections, "love_add_start", start)
    assert asyncio.run(assistant.try_add_love_from_chat(Bot(), "42", "Добавить музыку"))
    assert started == [("42", "artists", "leisure")]


def test_library_command_is_not_taken_by_dictionary(monkeypatch):
    import bot_text
    from types import SimpleNamespace
    handled = []

    async def love(_bot, _cid, text):
        handled.append(text)
        return True

    async def dictionary(*_args):
        raise AssertionError("dictionary must not see library commands")

    monkeypatch.setattr(bot_text.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_text.tracking, "touch", lambda _cid: None)
    monkeypatch.setattr(bot_text.assistant, "try_add_love_from_chat", love)
    monkeypatch.setattr(bot_text.dictionary_import, "try_add_dict_from_chat", dictionary)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id="42"), message=SimpleNamespace(text="Добавить музыку Radiohead"))

    async def nothing(*_args):
        return None

    asyncio.run(bot_text.handle(update, SimpleNamespace(bot=Bot()), nothing))
    assert handled == ["Добавить музыку Radiohead"]


def test_do_not_add_button_is_red():
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Не добавлять", callback_data="a_dictbatch_cancel")]])
    button = telegram_runtime._enhance_markup(markup)["inline_keyboard"][0][0]
    assert (button["text"], button["style"]) == ("Не добавлять", "danger")
