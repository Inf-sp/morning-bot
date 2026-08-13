import asyncio
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_books
import leisure_movies


def test_movie_premieres_keep_only_current_year_and_cache_by_country(monkeypatch):
    saved = {}
    calls = []
    today = datetime.now(leisure_movies.config.TZ).date()

    def movie(title, release, ident):
        return SimpleNamespace(
            id=ident, title=title, release_date=release, genres=["Драма"],
            overview=f"Коротко о фильме {title}",
        )

    def get_now_playing(*_args):
        calls.append("now")
        return [movie("В прокате", today - timedelta(days=3), 1), movie("Старый", today.replace(year=today.year - 1), 2)]

    def get_upcoming(*_args):
        calls.append("upcoming")
        return [movie("Скоро", today + timedelta(days=7), 3)]

    monkeypatch.setattr(leisure_movies.store, "get_settings", lambda _cid: {"cc": "NL", "country": "Нидерланды"})
    monkeypatch.setattr(leisure_movies.store, "_load", lambda _key: saved)
    monkeypatch.setattr(leisure_movies.store, "mutate_kv", lambda _key, fn: saved.update(fn(saved)[0]))
    monkeypatch.setattr(leisure_movies.tmdb, "get_now_playing", get_now_playing)
    monkeypatch.setattr(leisure_movies.tmdb, "get_upcoming_theatrical_releases", get_upcoming)

    first = asyncio.run(leisure_movies.get_movie_premieres("42"))
    second = asyncio.run(leisure_movies.get_movie_premieres("42"))

    assert [item["title"] for item in first] == ["В прокате", "Скоро"]
    assert second == first
    assert calls == ["now", "upcoming"]


def test_book_premieres_are_current_month_diverse_and_cached(monkeypatch):
    saved = {}
    calls = []
    today = datetime.now(leisure_books.config.TZ).date()
    current_month = today.strftime("%Y-%m")
    old_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    candidates = [
        {"title": "Роман", "author": "Автор", "published_date": f"{current_month}-10", "categories": ["Fiction"], "description": "История о возвращении домой."},
        {"title": "Биография", "author": "Автор", "published_date": f"{current_month}-09", "categories": ["Biography"], "description": "История о смелом выборе."},
        {"title": "Старая", "author": "Автор", "published_date": f"{old_month}-10", "categories": ["History"]},
    ]

    def search(_limit):
        calls.append("search")
        return candidates

    monkeypatch.setattr(leisure_books.store, "_load", lambda _key: saved)
    monkeypatch.setattr(leisure_books.store, "_save", lambda _key, value: saved.update(value))
    monkeypatch.setattr(leisure_books.google_books, "search_new_releases", search)

    first = asyncio.run(leisure_books.get_book_premieres())
    second = asyncio.run(leisure_books.get_book_premieres())

    assert [item["title"] for item in first] == ["Роман", "Биография"]
    assert second == first
    assert calls == ["search"]
