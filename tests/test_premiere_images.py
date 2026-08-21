import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_books
import leisure_games
import leisure_movies


class Bot:
    def __init__(self):
        self.sent = []

    async def send_media_group(self, **kwargs):
        self.sent.append(("gallery", kwargs))

    async def send_photo(self, **kwargs):
        self.sent.append(("photo", kwargs))

    async def send_message(self, **kwargs):
        self.sent.append(("message", kwargs))


def test_movie_premiere_without_poster_is_not_shown(monkeypatch):
    items = [{
        "title": "Без постера",
        "date": "2026-08-15",
        "genres": "драма",
        "overview": "Описание.",
        "trailer_url": "https://youtube.test/missing",
    }, *[{
        "title": f"С постером {index}",
        "date": "2026-08-15",
        "genres": "драма",
        "overview": "Описание.",
        "poster": f"https://images.test/movie{index}.jpg",
        "trailer_url": f"https://youtube.test/movie{index}",
    } for index in range(2)]]
    monkeypatch.setattr(leisure_movies.store, "get_settings", lambda _cid: {
        "country": "Нидерланды", "cc": "NL",
    })
    monkeypatch.setattr(
        leisure_movies, "get_movie_premieres", lambda _cid: asyncio.sleep(0, result=items),
    )
    bot = Bot()

    asyncio.run(leisure_movies.send_movie_premieres(bot, "42"))

    assert [kind for kind, _kwargs in bot.sent] == ["photo"]
    assert "Без постера" not in bot.sent[0][1]["caption"]
    assert bot.sent[0][1]["photo"] == "https://images.test/movie0.jpg"


def test_game_premiere_without_poster_is_not_shown(monkeypatch):
    items = [{
        "title": "Без постера",
        "date_label": "15 сентября 2026",
        "platform_label": "💻 ПК",
        "genre": "RPG",
        "url": "https://example.test/missing",
    }, *[{
        "title": f"С постером {index}",
        "date_label": "15 сентября 2026",
        "platform_label": "💻 ПК",
        "genre": "RPG",
        "poster": f"https://images.test/game{index}.jpg",
        "trailer_url": f"https://youtube.test/game{index}",
    } for index in range(2)]]
    monkeypatch.setattr(
        leisure_games, "get_game_premieres",
        lambda _cid, **_kwargs: asyncio.sleep(0, result=items),
    )
    bot = Bot()

    asyncio.run(leisure_games.send_game_premieres(bot, "42"))

    assert [kind for kind, _kwargs in bot.sent] == ["gallery"]
    assert "Без постера" not in bot.sent[0][1]["caption"]
    assert len(bot.sent[0][1]["media"]) == 2


def test_book_premiere_without_cover_is_not_shown(monkeypatch):
    items = [{
        "title": "Без обложки",
        "author": "Автор",
        "published_date": "2026-08-15",
        "categories": ["Fiction"],
        "summary": "Описание.",
        "url": "https://books.test/missing",
    }, {
        "title": "С обложкой",
        "author": "Автор",
        "published_date": "2026-08-15",
        "categories": ["Fiction"],
        "summary": "Описание.",
        "url": "https://books.test/covered",
        "cover_url": "https://images.test/book.jpg",
    }]
    monkeypatch.setattr(
        leisure_books, "get_book_premieres",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=items),
    )
    bot = Bot()

    asyncio.run(leisure_books.send_book_premieres(bot, "42"))

    assert [kind for kind, _kwargs in bot.sent] == ["photo"]
    assert bot.sent[0][1]["photo"] == "https://images.test/book.jpg"
    assert "Без обложки" not in bot.sent[0][1]["caption"]
