import asyncio
import os
from datetime import date

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import monthly_rebuses


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
