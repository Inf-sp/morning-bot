import asyncio
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_movies
import movie_discovery
from telegram.error import BadRequest


def _combined_items():
    return [{
        "id": 1,
        "title": "Новая премьера",
        "release_date": "2026-08-31",
        "poster": "https://images.test/unavailable.jpg",
        "overview": "Короткая завязка.",
        "premiere_kind": "movie",
    }]


def test_combined_premieres_screen_has_bound_loader(monkeypatch):
    sent = []

    class Bot:
        async def send_photo(self, **kwargs):
            sent.append(kwargs)

    async def movies(_cid):
        return _combined_items()

    async def series(_cid):
        return []

    monkeypatch.setattr(leisure_movies, "_movie_premieres_with_posters", movies)
    monkeypatch.setattr(movie_discovery, "_movie_premieres_with_posters", movies)
    monkeypatch.setattr(leisure_movies, "get_series_premieres", series)
    monkeypatch.setattr(movie_discovery, "get_series_premieres", series)

    asyncio.run(leisure_movies.send_combined_premieres(Bot(), "42"))

    assert sent and sent[0]["photo"] == "https://images.test/unavailable.jpg"


def test_combined_premieres_uses_text_when_telegram_rejects_poster(monkeypatch):
    replaced = []

    class Bot:
        async def send_photo(self, **_kwargs):
            raise BadRequest("Failed to get http url content")

    class Status:
        async def replace(self, text, **kwargs):
            replaced.append((text, kwargs))

    async def combined(_cid):
        return _combined_items()

    monkeypatch.setattr(leisure_movies, "_combined_premieres", combined, raising=False)
    monkeypatch.setattr(movie_discovery, "_combined_premieres", combined, raising=False)

    asyncio.run(leisure_movies.send_combined_premieres(
        Bot(), "42", status=Status(),
    ))

    assert replaced and "Новая премьера" in replaced[0][0]


def test_combined_premieres_keeps_card_when_next_poster_is_rejected(monkeypatch):
    captions = []

    class Query:
        async def edit_message_media(self, **_kwargs):
            raise BadRequest("Failed to get http url content")

        async def edit_message_caption(self, **kwargs):
            captions.append(kwargs)

    async def combined(_cid):
        return _combined_items()

    monkeypatch.setattr(leisure_movies, "_combined_premieres", combined)
    monkeypatch.setattr(movie_discovery, "_combined_premieres", combined)

    asyncio.run(leisure_movies.show_combined_premiere_page("42", Query(), 0))

    assert captions and "Новая премьера" in captions[0]["caption"]


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
    assert saved["NL"]["week"] == leisure_movies._now_playing_week_key()
    assert saved["NL"]["expires"] == (today + timedelta(days=7)).isoformat()


def test_movie_premieres_keep_five_most_popular_with_trailers(monkeypatch):
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
    monkeypatch.setattr(
        leisure_movies.tmdb, "english_poster",
        lambda movie_id, _kind, *_args: f"https://image.tmdb.org/en{movie_id}.jpg",
    )

    items = asyncio.run(leisure_movies.get_movie_premieres("42", refresh=True))

    assert [item["title"] for item in items] == [f"Фильм {i}" for i in range(9, 4, -1)]
    assert all(item["poster"] and item["trailer_url"] for item in items)


def test_series_premieres_prioritize_favorite_seasons_and_require_rating_above_seven(monkeypatch):
    today = datetime.now(leisure_movies.config.TZ).date()
    monkeypatch.setattr(
        leisure_movies.store, "get_list",
        lambda _key, _cid: ["Разделение (сериал, 2022)"],
    )
    monkeypatch.setattr(leisure_movies.tmdb, "search_id", lambda *_args: {
        "id": 10, "kind": "tv", "rating": 8.7,
    })
    monkeypatch.setattr(leisure_movies.tmdb, "upcoming_tv_seasons", lambda *_args: [{
        "id": 10, "name": "Разделение", "season_number": 3,
        "release_date": (today + timedelta(days=20)).isoformat(),
        "rating": 8.7, "poster": "favorite.jpg", "overview": "Новый сезон.",
    }])
    monkeypatch.setattr(leisure_movies.tmdb, "upcoming_tv_releases", lambda *_args: [{
        "id": 20, "name": "Новый хит", "release_date": (today + timedelta(days=5)).isoformat(),
        "rating": 7.8, "poster": "new.jpg", "overview": "Новая история.", "vote_count": 120,
    }, {
        "id": 21, "name": "Ровно семь", "release_date": (today + timedelta(days=2)).isoformat(),
        "rating": 7.0, "poster": "seven.jpg", "overview": "Не проходит фильтр.",
    }])
    monkeypatch.setattr(
        leisure_movies.tmdb, "english_poster",
        lambda tmdb_id, _kind, *_args: f"https://image.tmdb.org/en{tmdb_id}.jpg",
    )

    items = asyncio.run(leisure_movies.get_series_premieres("42"))

    assert [item["name"] for item in items] == ["Разделение", "Новый хит"]
    assert items[0]["favorite"] is True


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


def test_movie_premieres_put_tmdb_weekly_trend_first(monkeypatch):
    saved = {}
    today = datetime.now(leisure_movies.config.TZ).date()

    def movie(index):
        return SimpleNamespace(id=index, title=f"Фильм {index}", release_date=today, genres=["Драма"],
                               overview="Завязка.", poster_url="p.jpg", popularity=float(index), vote_count=10)

    monkeypatch.setattr(leisure_movies.store, "get_settings", lambda _cid: {"cc": "NL"})
    monkeypatch.setattr(leisure_movies.store, "_load", lambda _key: saved)
    monkeypatch.setattr(leisure_movies.store, "mutate_kv", lambda _key, fn: saved.update(fn(saved)[0]))
    monkeypatch.setattr(leisure_movies.tmdb, "get_now_playing", lambda *_args: [movie(i) for i in range(1, 10)])
    monkeypatch.setattr(leisure_movies.tmdb, "get_upcoming_theatrical_releases", lambda *_args: [])
    monkeypatch.setattr(leisure_movies.tmdb, "trending", lambda kind: [{"id": 3}, {"id": 1}] if kind == "movie" else [])
    monkeypatch.setattr(leisure_movies.tmdb, "trailer_url", lambda *_args: "")
    monkeypatch.setattr(leisure_movies.tmdb, "english_poster", lambda *_args: "en.jpg")

    items = asyncio.run(leisure_movies.get_movie_premieres("42", refresh=True))

    # Тренд недели — первым (даже с низкой популярностью среди премьер страны), дальше — по популярности.
    assert [item["id"] for item in items] == [3, 1, 9, 8, 7]


def test_series_premieres_add_trending_new_seasons_and_skip_obscure(monkeypatch):
    today = datetime.now(leisure_movies.config.TZ).date()
    soon = (today + timedelta(days=3)).isoformat()
    monkeypatch.setattr(leisure_movies.store, "get_list", lambda _key, _cid: [])
    monkeypatch.setattr(leisure_movies.tmdb, "trending", lambda kind: [
        {"id": 30, "name": "Медведь", "release_date": "2022-06-23"},  # давний сериал — ищем новый сезон
    ] if kind == "tv" else [])
    monkeypatch.setattr(leisure_movies.tmdb, "upcoming_tv_seasons", lambda tmdb_id, *_args: [{
        "id": 30, "name": "Медведь", "season_number": 5, "release_date": soon, "rating": 8.2,
        "poster": "bear.jpg", "overview": "Новый сезон.", "vote_count": 900, "popularity": 50,
    }] if tmdb_id == 30 else [])
    monkeypatch.setattr(leisure_movies.tmdb, "upcoming_tv_releases", lambda *_args: [
        {"id": 40, "name": "Популярный", "release_date": soon, "rating": 7.9, "poster": "a.jpg",
         "overview": "История.", "vote_count": 400, "popularity": 300},
        {"id": 41, "name": "Безвестный", "release_date": soon, "rating": 9.5, "poster": "b.jpg",
         "overview": "История.", "vote_count": 5, "popularity": 999},
    ])
    monkeypatch.setattr(leisure_movies.tmdb, "english_poster", lambda *_args: "en.jpg")
    monkeypatch.setattr(leisure_movies.tmdb, "trailer_url", lambda *_args: "")

    items = asyncio.run(leisure_movies.get_series_premieres("42"))

    # Новый сезон трендового сериала — первым, затем по популярности; меньше 20 голосов — не новинка.
    assert [(item["name"], item.get("season_number")) for item in items] == [("Медведь", 5), ("Популярный", None)]
