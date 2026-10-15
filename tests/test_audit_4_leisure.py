import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")


import cleanup
import config
import leisure_movies
import movie_engine
import personal_collections
import tmdb
import yearly_tops


def _lists(monkeypatch, module, data):
    monkeypatch.setattr(module.store, "get_list", lambda key, _cid: list(data.get(key, [])))


def test_favorite_with_id_and_canonical_suffix_is_excluded_from_movie_recommendations(monkeypatch):
    _lists(monkeypatch, movie_engine, {
        config.FAVORITE_MOVIES_KEY: [
            {"id": "a1", "value": "Укрытие (сериал, 2023)"},
            "Патерсон (фильм, 2016)",
        ],
    })
    monkeypatch.setattr(movie_engine.recommendation_stoplist, "values", lambda *_args: [])

    excluded = movie_engine._excluded_norms("42", include_shown=False)

    assert movie_engine._norm("Укрытие") in excluded
    assert movie_engine._norm("Патерсон") in excluded


def test_movie_used_reads_items_stored_with_ids(monkeypatch):
    _lists(monkeypatch, leisure_movies, {
        config.FAVORITE_MOVIES_KEY: [{"id": "a1", "value": "Медведь (сериал, 2022)"}],
    })
    monkeypatch.setattr(leisure_movies.recommendation_stoplist, "values", lambda *_args: [])

    used = leisure_movies._movie_used("42")

    assert "медведь" in used
    assert [item["title"] for item in leisure_movies._fallback_movie_items("42")] == [
        "Решение уйти", "Пылающий", "Разделение", "Патерсон",
    ]


def test_tmdb_error_body_is_not_returned_as_data(monkeypatch):
    monkeypatch.setattr(tmdb.config, "TMDB_API_KEY", "key")
    monkeypatch.setattr(tmdb.api_usage, "record_request", lambda *_args, **_kwargs: None)
    response = SimpleNamespace(
        status_code=429, headers={},
        json=lambda: {"status_code": 25, "status_message": "rate limit"},
    )
    monkeypatch.setattr(tmdb.requests, "get", lambda *_args, **_kwargs: response)

    assert tmdb._get("/movie/1", {}) is None


def test_legacy_games_collection_callback_no_longer_opens_games(monkeypatch):
    # Раздела игр больше нет: старая кнопка ведёт в Настройки, а не в список игр.
    calls = []

    async def open_collection(*_args, **_kwargs):
        calls.append("collection")

    async def send_home(_bot, _cid, *_args, **_kwargs):
        calls.append("settings")

    import settings

    monkeypatch.setattr(cleanup, "open_collection", open_collection)
    monkeypatch.setattr(settings, "send_home", send_home)

    asyncio.run(personal_collections.handle_collection_callback(object(), "42", None, "as_love_games"))

    assert calls == ["settings"]


def test_yearly_top_counter_matches_the_number_of_found_items():
    _msg, markup, _page = yearly_tops._view("movie", [{"title": "A"}, {"title": "B"}])

    assert markup.inline_keyboard[0][1].text == "1/2"


