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
    assert assistant._detect_love_add("Добавить сериал Шитс крик") == ("movies", "Кино", "Шитс крик")
    assert assistant._detect_love_add("люблю фильмы про космос") is None


def test_question_about_a_series_goes_to_assistant_not_recommendation():
    assert assistant._detect_intent("Что за сериал шитс крик") is None
    assert assistant._detect_intent("кто такой Дени Вильнёв") is None
    assert assistant._detect_intent("посоветуй сериал") == "movie"


def test_chat_add_saves_verified_title_or_asks_to_clarify(monkeypatch):
    saved = []
    monkeypatch.setattr(assistant.store, "get_list", lambda *_a: [])
    monkeypatch.setattr(assistant.store, "add_to_list", lambda _key, _cid, value: saved.append(value))
    monkeypatch.setattr(leisure_collection, "_resolve_movie_label",
                        lambda title, allow_ai=False: {"name": "Шиттс Крик", "kind": "tv", "year": "2015"}
                        if allow_ai else None)
    bot = Bot()

    assert asyncio.run(assistant.try_add_love_from_chat(bot, "42", "Добавить сериал Шитс крик"))
    assert saved == ["Шиттс Крик (сериал, 2015)"]

    monkeypatch.setattr(leisure_collection, "_resolve_movie_label", lambda *_a, **_k: None)
    assert asyncio.run(assistant.try_add_love_from_chat(bot, "42", "Добавь сериал Абвгд"))
    assert saved == ["Шиттс Крик (сериал, 2015)"] and bot.sent[-1].startswith("Не нашёл «Абвгд»")


def test_do_not_add_button_is_red():
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Не добавлять", callback_data="a_dictbatch_cancel")]])
    button = telegram_runtime._enhance_markup(markup)["inline_keyboard"][0][0]
    assert (button["text"], button["style"]) == ("Не добавлять", "danger")
