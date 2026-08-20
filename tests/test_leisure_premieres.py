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

    def get_now_playing(_country, language, _limit):
        calls.append(("now", language))
        return [movie("В прокате", today - timedelta(days=3), 1), movie("Старый", today.replace(year=today.year - 1), 2)]

    def get_upcoming(_country, _start, _end, language):
        calls.append(("upcoming", language))
        return [movie("Скоро", today + timedelta(days=7), 3)]

    monkeypatch.setattr(leisure_movies.store, "get_settings", lambda _cid: {"cc": "NL", "country": "Нидерланды"})
    monkeypatch.setattr(leisure_movies.store, "_load", lambda _key: saved)
    monkeypatch.setattr(leisure_movies.store, "mutate_kv", lambda _key, fn: saved.update(fn(saved)[0]))
    monkeypatch.setattr(leisure_movies.tmdb, "get_now_playing", get_now_playing)
    monkeypatch.setattr(leisure_movies.tmdb, "get_upcoming_theatrical_releases", get_upcoming)

    first = asyncio.run(leisure_movies.get_movie_premieres("42", refresh=True))
    second = asyncio.run(leisure_movies.get_movie_premieres("42", refresh=True))

    assert [item["title"] for item in first] == ["В прокате", "Скоро"]
    assert second == first
    assert calls == [("now", "ru-RU"), ("upcoming", "ru-RU")]
    assert saved["NL"]["expires"] == (today + timedelta(days=7)).isoformat()


def test_movie_premieres_keep_seven_most_popular_with_trailers(monkeypatch):
    saved = {}
    today = datetime.now(leisure_movies.config.TZ).date()

    def movie(index):
        return SimpleNamespace(
            id=index,
            title=f"Фильм {index}",
            release_date=today + timedelta(days=index % 5),
            genres=["Драма"],
            overview=f"Короткая завязка {index}.",
            poster_url=f"https://image.tmdb.org/poster{index}.jpg",
            popularity=float(index),
            vote_count=index * 10,
        )

    monkeypatch.setattr(leisure_movies.store, "get_settings", lambda _cid: {"cc": "NL"})
    monkeypatch.setattr(leisure_movies.store, "_load", lambda _key: saved)
    monkeypatch.setattr(leisure_movies.store, "mutate_kv", lambda _key, fn: saved.update(fn(saved)[0]))
    monkeypatch.setattr(leisure_movies.tmdb, "get_now_playing", lambda *_args: [movie(i) for i in range(10)])
    monkeypatch.setattr(leisure_movies.tmdb, "get_upcoming_theatrical_releases", lambda *_args: [])
    monkeypatch.setattr(
        leisure_movies.tmdb,
        "trailer_url",
        lambda movie_id, _kind: f"https://www.youtube.com/watch?v=trailer{movie_id}",
    )

    items = asyncio.run(leisure_movies.get_movie_premieres("42", refresh=True))

    assert [item["title"] for item in items] == [f"Фильм {i}" for i in range(9, 2, -1)]
    assert all(item["poster"] and item["trailer_url"] for item in items)


def test_book_premieres_are_current_month_diverse_and_cached(monkeypatch):
    saved = {}
    calls = []
    today = datetime.now(leisure_books.config.TZ).date()
    current_month = today.strftime("%Y-%m")
    old_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    candidates = [
        {"title": "Роман", "author": "Автор", "published_date": f"{current_month}-10", "categories": ["Fiction"], "description": "История о возвращении домой.", "cover_url": "https://images.test/novel.jpg"},
        {"title": "Биография", "author": "Автор", "published_date": f"{current_month}-09", "categories": ["Biography"], "description": "История о смелом выборе.", "cover_url": "https://images.test/biography.jpg"},
        {"title": "Старая", "author": "Автор", "published_date": f"{old_month}-10", "categories": ["History"]},
    ]

    def search(_limit):
        calls.append("search")
        return candidates

    monkeypatch.setattr(leisure_books.store, "_load", lambda _key: saved)
    monkeypatch.setattr(leisure_books.store, "_save", lambda _key, value: saved.update(value))
    monkeypatch.setattr(leisure_books.google_books, "search_new_releases", search)

    first = asyncio.run(leisure_books.get_book_premieres(refresh=True))
    second = asyncio.run(leisure_books.get_book_premieres(refresh=True))

    assert [item["title"] for item in first] == ["Роман", "Биография"]
    assert second == first
    assert calls == ["search"]
    assert saved["expires"] == (today + timedelta(days=7)).isoformat()


def test_movie_premieres_use_stale_cache_without_daytime_tmdb_request(monkeypatch):
    today = datetime.now(leisure_movies.config.TZ).date()
    saved = {
        "NL": {
            "version": leisure_movies._MOVIE_PREMIERES_CACHE_VERSION,
            "expires": (today - timedelta(days=1)).isoformat(),
            "items": [{"title": "Вчерашняя витрина"}],
        },
    }
    monkeypatch.setattr(leisure_movies.store, "get_settings", lambda _cid: {"cc": "NL"})
    monkeypatch.setattr(leisure_movies.store, "_load", lambda _key: saved)
    monkeypatch.setattr(
        leisure_movies.tmdb, "get_now_playing",
        lambda *_args: (_ for _ in ()).throw(AssertionError("daytime request")),
    )
    monkeypatch.setattr(
        leisure_movies.tmdb, "get_upcoming_theatrical_releases",
        lambda *_args: (_ for _ in ()).throw(AssertionError("daytime request")),
    )

    assert asyncio.run(leisure_movies.get_movie_premieres("42")) == [{"title": "Вчерашняя витрина"}]


def test_movie_premieres_rebuild_an_empty_cache_on_first_open(monkeypatch):
    saved = {}
    calls = []
    today = datetime.now(leisure_movies.config.TZ).date()
    premiere = SimpleNamespace(
        id=7,
        title="Новая премьера",
        release_date=today,
        genres=["Драма"],
        overview="Героиня возвращается домой.",
        poster_url="https://image.tmdb.org/poster7.jpg",
        popularity=70,
        vote_count=700,
    )
    monkeypatch.setattr(leisure_movies.store, "get_settings", lambda _cid: {"cc": "NL"})
    monkeypatch.setattr(leisure_movies.store, "_load", lambda _key: saved)
    monkeypatch.setattr(leisure_movies.store, "mutate_kv", lambda _key, fn: saved.update(fn(saved)[0]))
    monkeypatch.setattr(
        leisure_movies.tmdb,
        "get_now_playing",
        lambda *_args: calls.append("now") or [premiere],
    )
    monkeypatch.setattr(
        leisure_movies.tmdb,
        "get_upcoming_theatrical_releases",
        lambda *_args: calls.append("upcoming") or [],
    )
    monkeypatch.setattr(
        leisure_movies.tmdb,
        "trailer_url",
        lambda *_args: "https://www.youtube.com/watch?v=premiere7",
    )

    first = asyncio.run(leisure_movies.get_movie_premieres("42"))
    second = asyncio.run(leisure_movies.get_movie_premieres("42"))

    assert [item["title"] for item in first] == ["Новая премьера"]
    assert second == first
    assert calls == ["now", "upcoming"]


def test_book_premieres_use_stale_cache_without_daytime_google_request(monkeypatch):
    today = datetime.now(leisure_books.config.TZ).date()
    saved = {
        "version": leisure_books._BOOK_PREMIERES_CACHE_VERSION,
        "month": today.strftime("%Y-%m"),
        "expires": (today - timedelta(days=1)).isoformat(),
        "items": [{"title": "Вчерашняя витрина"}],
    }
    monkeypatch.setattr(leisure_books.store, "_load", lambda _key: saved)
    monkeypatch.setattr(
        leisure_books.google_books, "search_new_releases",
        lambda *_args: (_ for _ in ()).throw(AssertionError("daytime request")),
    )

    assert asyncio.run(leisure_books.get_book_premieres()) == [{"title": "Вчерашняя витрина"}]


def test_book_premieres_screen_recovers_when_cache_is_empty(monkeypatch):
    today = datetime.now(leisure_books.config.TZ).date()
    saved = {}
    calls = []

    def search(_limit):
        calls.append("search")
        return [{
            "title": "Новая книга",
            "author": "Автор",
                "published_date": today.isoformat(),
                "categories": ["Fiction"],
                "description": "Героиня возвращается домой и раскрывает семейную тайну.",
                "cover_url": "https://images.test/new-book.jpg",
            }]

    class Bot:
        async def send_photo(self, **kwargs):
            calls.append(("photo", kwargs))

    monkeypatch.setattr(leisure_books.store, "_load", lambda _key: saved)
    monkeypatch.setattr(leisure_books.store, "_save", lambda _key, value: saved.update(value))
    monkeypatch.setattr(leisure_books.google_books, "search_new_releases", search)

    asyncio.run(leisure_books.send_book_premieres(Bot(), "42"))

    assert calls[0] == "search"
    assert calls[1][0] == "photo"
    assert "Новая книга" in calls[1][1]["caption"]


def test_book_premieres_fall_back_to_recent_google_books_results(monkeypatch):
    today = datetime.now(leisure_books.config.TZ).date()
    recent = today - timedelta(days=35)
    saved = {}
    google_result = {
        "title": "Недавняя новинка",
        "author": "Автор",
        "published_date": recent.isoformat(),
        "categories": ["Fiction"],
        "description": "Героиня начинает новую жизнь в незнакомом городе.",
        "cover_url": "https://images.test/recent-book.jpg",
        "info_link": "https://books.google.com/books?id=recent",
    }

    monkeypatch.setattr(leisure_books.store, "_load", lambda _key: saved)
    monkeypatch.setattr(leisure_books.store, "_save", lambda _key, value: saved.update(value))
    monkeypatch.setattr(
        leisure_books.google_books,
        "search_new_releases",
        lambda _limit: [google_result],
    )

    items = asyncio.run(leisure_books.get_book_premieres(refresh=True))

    assert [item["title"] for item in items] == ["Недавняя новинка"]


def test_book_premieres_refresh_bypasses_empty_cache(monkeypatch):
    today = datetime.now(leisure_books.config.TZ).date()
    saved = {
        "version": leisure_books._BOOK_PREMIERES_CACHE_VERSION,
        "month": today.strftime("%Y-%m"),
        "expires": (today + timedelta(days=2)).isoformat(),
        "items": [],
    }
    calls = []
    google_result = {
        "title": "Новая книга",
        "published_date": today.isoformat(),
        "cover_url": "https://images.test/new-book.jpg",
    }

    monkeypatch.setattr(leisure_books.store, "_load", lambda _key: saved)
    monkeypatch.setattr(leisure_books.store, "_save", lambda _key, value: saved.update(value))
    monkeypatch.setattr(
        leisure_books.google_books,
        "search_new_releases",
        lambda _limit: calls.append("search") or [google_result],
    )

    items = asyncio.run(leisure_books.get_book_premieres(refresh=True))

    assert calls == ["search"]
    assert [item["title"] for item in items] == ["Новая книга"]
