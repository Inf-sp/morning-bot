import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_books
import leisure_movies
import leisure_music


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


def test_category_preferences_do_not_show_hidden_or_seen_lists():
    for sender in (
        leisure_movies.send_movie_prefs,
        leisure_books.send_book_preferences,
        leisure_music.send_music_preferences,
    ):
        bot = _Bot()
        asyncio.run(sender(bot, "hidden-buttons"))

        labels = _labels(bot.messages[-1])
        assert all("скрыт" not in label.casefold() for label in labels)
        assert all(label not in {"Смотрел", "Прочитано", "Уже знаю"} for label in labels)
