import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import yearly_tops


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_yearly_top_card_has_genre_and_short_description():
    message = yearly_tops.leisure_ui.yearly_top_screen("game", 2025, {
        "title": "Clair Obscur: Expedition 33",
        "genre": "RPG",
        "summary": "Экспедиция отправляется остановить Художницу.",
    })

    assert message.text.startswith("🏆 Топ-5 · Игры 2025")
    assert "RPG" in message.text
    assert "Экспедиция отправляется" in message.text


def test_movie_top_uses_previous_year_and_english_posters(monkeypatch):
    calls = []

    def discover(*args):
        calls.append(args)
        return [{
            "id": index, "name": f"Movie {index}", "year": "2025",
            "rating": 8.5, "vote_count": 1000 + index, "genres": "драма",
            "overview": "Короткое описание.", "kind": "movie",
        } for index in range(1, 7)]

    monkeypatch.setattr(yearly_tops, "previous_year", lambda: 2025)
    monkeypatch.setattr(yearly_tops.tmdb, "discover", discover)
    monkeypatch.setattr(
        yearly_tops.tmdb, "english_poster",
        lambda movie_id, _kind: f"https://image/{movie_id}.jpg",
    )

    items = asyncio.run(yearly_tops.get_items("movie"))

    assert len(items) == 5
    assert all(item["poster"].startswith("https://image/") for item in items)
    assert calls[0][3] == 2025
    assert calls[0][4] == 2025


def test_yearly_top_carousel_uses_one_of_five_counter():
    items = [{"title": f"Книга {index}"} for index in range(5)]

    _message, markup, page = yearly_tops._view("book", items, page=1)

    assert page == 1
    assert _labels(markup)[0] == ["◀️", "2/5", "▶️"]
