import os
from datetime import date

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import monthly_rebuses
import leisure_movies


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


def test_cinema_has_a_full_local_month_of_unique_rebuses():
    answers = [str(item.get("answer") or "").strip().casefold()
               for item in leisure_movies._CINEMA_REBUSES]

    assert len(answers) >= 31
    assert len(set(answers)) >= 31


def test_editorial_months_have_no_daily_repeats_and_change_order():
    pool = monthly_rebuses.local_pool("movies")
    august = monthly_rebuses._editorial_month_items("movies", date(2026, 8, 1), pool)
    september = monthly_rebuses._editorial_month_items("movies", date(2026, 9, 1), pool)

    assert len({item["answer"].casefold() for item in august}) == 31
    assert len({item["answer"].casefold() for item in september}) == 30
    assert [item["answer"] for item in august[:30]] != [
        item["answer"] for item in september
    ]


def test_short_pool_falls_back_to_seed_rotation():
    item = monthly_rebuses.cached_for_day(
        "movies", date(2026, 8, 2), ({"emoji": "🦈", "answer": "Челюсти"},),
    )

    assert item["answer"] == "Челюсти"
