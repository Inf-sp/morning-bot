import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import inclusive_recommendations
import leisure_books
import leisure_movies
from ui import leisure as leisure_ui


def test_rotation_requires_inclusive_attempt_on_every_fifth_recommendation(monkeypatch):
    profile = {}
    monkeypatch.setattr(inclusive_recommendations.store, "get_profile", lambda _cid: profile)

    def mutate(_cid, change):
        nonlocal profile
        profile = change(profile)[0]

    monkeypatch.setattr(inclusive_recommendations.store, "mutate_profile", mutate)

    for _ in range(4):
        assert not inclusive_recommendations.is_due("42", "movie")
        inclusive_recommendations.record("42", "movie", False)
    assert inclusive_recommendations.is_due("42", "movie")

    inclusive_recommendations.record("42", "movie", True)
    assert not inclusive_recommendations.is_due("42", "movie")


def test_only_verified_titles_receive_lgbt_marker():
    assert inclusive_recommendations.is_inclusive("movie", "Nimona")
    assert inclusive_recommendations.is_inclusive("book", "Песнь Ахилла")
    assert inclusive_recommendations.is_inclusive("game", "Hades")
    assert not inclusive_recommendations.is_inclusive("movie", "Неизвестный фильм")


def test_cards_show_lgbt_marker_only_when_confirmed():
    _title, movie = leisure_ui.movie_card(
        {"title": "Нимона"},
        {"name": "Нимона", "kind": "movie", "genres": "анимация", "lgbt": True},
    )
    book = leisure_ui.book_text({"title": "Песнь Ахилла", "lgbt": True})
    game = leisure_ui.game_card({"name": "Hades", "lgbt": True})

    assert "🏳️‍🌈 ЛГБТ" in movie.text
    assert "🏳️‍🌈 ЛГБТ" not in book.text
    assert "🏳️‍🌈 ЛГБТ" in game.text
    assert "🏳️‍🌈 ЛГБТ" not in leisure_ui.game_card({"name": "Другая игра"}).text


def test_due_movie_pick_respects_selected_content_type(monkeypatch):
    found = {
        "Moonlight": {"name": "Лунный свет", "name_en": "Moonlight", "kind": "movie", "rating": 7.4},
        "Heartstopper": {"name": "Трепет сердца", "name_en": "Heartstopper", "kind": "tv", "rating": 8.5},
    }
    monkeypatch.setattr(leisure_movies.movie_engine, "_excluded_norms", lambda _cid: set())
    monkeypatch.setattr(leisure_movies.tmdb, "lookup_title", lambda title: found.get(title))

    item, tm = asyncio.run(leisure_movies._inclusive_movie_pick(
        "42", {"type_pref": "tv", "min_rating": 7},
    ))

    assert item["title"] == "Трепет сердца"
    assert tm["lgbt"] is True


def test_due_book_pick_uses_curated_item_and_skips_seen(monkeypatch):
    monkeypatch.setattr(leisure_books, "_book_used", lambda _cid: {"песнь ахилла"})
    monkeypatch.setattr(leisure_books.google_books, "enrich_book", lambda item: dict(item))

    item = asyncio.run(leisure_books._inclusive_book_pick("42"))

    assert item["title"] == "Комната Джованни"
    assert item["lgbt"] is True
