import asyncio
import os
from datetime import date

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import monthly_rebuses
import leisure_books
import leisure_games
import leisure_movies
import leisure_music
import travel


def test_monthly_rebuses_are_unique_for_every_day(monkeypatch):
    stored = {}
    monkeypatch.setattr(monthly_rebuses.store, "_load", lambda *_args: stored)

    def mutate(_key, change):
        updated, result = change(dict(stored))
        stored.clear()
        stored.update(updated)
        return result

    monkeypatch.setattr(monthly_rebuses.store, "mutate_kv", mutate)

    async def generate(*_args, **_kwargs):
        return {"items": [
            {"emoji": f"🎬 {index}", "answer": f"Ответ {index}", "fact": f"Факт номер {index}."}
            for index in range(1, 32)
        ]}

    monkeypatch.setattr(monthly_rebuses.ai, "allm_json", generate)

    first = asyncio.run(monthly_rebuses.for_day("movies", date(2026, 8, 1)))
    last = asyncio.run(monthly_rebuses.for_day("movies", date(2026, 8, 31)))

    assert first["answer"] == "Ответ 1"
    assert last["answer"] == "Ответ 31"
    assert len({item["answer"] for item in stored["movies"]["items"]}) == 31


def test_monthly_rebuses_clean_generated_markup_before_caching():
    items = monthly_rebuses._valid_items([{
        "emoji": "🎬",
        "answer": "**Матрица**",
        "fact": "��**&#x20;Интересно:** Актёры долго тренировались.",
    }], 1)

    assert items == [{
        "emoji": "🎬",
        "answer": "Матрица",
        "fact": "Актёры долго тренировались.",
    }]


def test_incomplete_generation_does_not_replace_local_fallback(monkeypatch):
    monkeypatch.setattr(monthly_rebuses.store, "_load", lambda *_args: {})
    monkeypatch.setattr(monthly_rebuses.store, "mutate_kv", lambda *_args: None)

    async def incomplete(*_args, **_kwargs):
        return {"items": [{"emoji": "🎬", "answer": "Один"}]}

    monkeypatch.setattr(monthly_rebuses.ai, "allm_json", incomplete)

    item = asyncio.run(monthly_rebuses.for_day(
        "movies", date(2026, 8, 2), ({"emoji": "🦈", "answer": "Челюсти"},),
    ))

    assert item["answer"] == "Челюсти"


def test_every_main_category_has_a_full_local_month_of_unique_rebuses():
    pools = {
        "Кино": leisure_movies._CINEMA_REBUSES,
        "Книги": leisure_books._BOOK_REBUSES,
        "Музыка": leisure_music._MUSIC_REBUSES,
        "Игры": leisure_games._GAME_DAILY_CONTENT,
        "Поездки": travel._TRAVEL_REBUSES,
    }

    for category, items in pools.items():
        answers = [str(item.get("answer") or "").strip().casefold() for item in items]
        assert len(items) >= 31, category
        assert len(set(answers)) >= 31, category


def test_editorial_months_have_no_daily_repeats_and_change_order():
    for category in ("movies", "books", "music", "games", "travel"):
        pool = monthly_rebuses.local_pool(category)
        august = monthly_rebuses._editorial_month_items(
            category, date(2026, 8, 1), pool,
        )
        september = monthly_rebuses._editorial_month_items(
            category, date(2026, 9, 1), pool,
        )

        assert len({item["answer"].casefold() for item in august}) == 31
        assert len({item["answer"].casefold() for item in september}) == 30
        assert [item["answer"] for item in august[:30]] != [
            item["answer"] for item in september
        ]


def test_full_editorial_pool_is_saved_without_ai(monkeypatch):
    stored = {"movies": {
        "month": "2026-08",
        "attempted": True,
        "source": "generated",
        "items": [
            {"emoji": f"🎲 {index}", "answer": f"Старый ответ {index}"}
            for index in range(1, 32)
        ],
    }}
    monkeypatch.setattr(monthly_rebuses.store, "_load", lambda *_args: stored)

    def mutate(_key, change):
        updated, result = change(dict(stored))
        stored.clear()
        stored.update(updated)
        return result

    monkeypatch.setattr(monthly_rebuses.store, "mutate_kv", mutate)

    async def unexpected_ai(*_args, **_kwargs):
        raise AssertionError("editorial pool must not call AI")

    monkeypatch.setattr(monthly_rebuses.ai, "allm_json", unexpected_ai)
    pool = tuple({"emoji": f"🎬 {index}", "answer": f"Карточка {index}"}
                 for index in range(1, 32))

    item = asyncio.run(monthly_rebuses.for_day("movies", date(2026, 8, 20), pool))

    assert item
    assert stored["movies"]["source"] == "editorial"
    assert len(stored["movies"]["items"]) == 31
