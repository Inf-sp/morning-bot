import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import saved_items


class _Bot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


def _labels(message):
    return [
        button.text
        for row in message["reply_markup"].inline_keyboard
        for button in row
    ]


def test_leisure_category_screens_do_not_show_hidden_or_seen_lists():
    for sender in (
        saved_items.send_mydata_cinema,
        saved_items.send_mydata_books,
        saved_items.send_mydata_music,
    ):
        bot = _Bot()
        asyncio.run(sender(bot, "hidden-buttons"))

        labels = _labels(bot.messages[-1])
        assert all("скрыт" not in label.casefold() for label in labels)
        assert all(label not in {"Смотрел", "Прочитано", "Уже знаю"} for label in labels)


def test_favorite_categories_do_not_show_hidden_buttons(monkeypatch):
    monkeypatch.setattr(saved_items, "_love_items", lambda *_args: ["Пример"])

    for category in ("movies", "books", "artists", "countries"):
        bot = _Bot()
        asyncio.run(saved_items.send_love_section(bot, "hidden-buttons", category))

        labels = _labels(bot.messages[-1])
        assert all("скрыт" not in label.casefold() for label in labels)
        assert all("hidden" not in button.callback_data
                   for row in bot.messages[-1]["reply_markup"].inline_keyboard
                   for button in row)
