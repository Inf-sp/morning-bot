import os
import asyncio
from datetime import date

from telegram import MessageEntity

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_music


def test_recent_artist_history_is_unique_and_limited(monkeypatch):
    profile = {"music_recent_artists": ["The xx", "Bicep", "the xx"]}
    saved = []

    def set_profile(_cid, value):
        profile.clear()
        profile.update(value)
        saved.append(dict(value))

    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_music.store, "set_profile", set_profile)

    leisure_music._remember_artist("42", "BICEP")
    leisure_music._remember_artist("42", "FKA twigs")

    assert saved[-1]["music_recent_artists"] == ["The xx", "BICEP", "FKA twigs"]


def test_music_module_has_no_learning_language_priority():
    assert not hasattr(leisure_music, "_language_music_context")
    assert not hasattr(leisure_music, "_learning_language_code")


def test_music_shows_a_local_artist_when_the_ai_chain_is_unavailable(monkeypatch):
    calls = []
    profile = {}

    class Status:
        async def replace(self, text, **kwargs):
            calls.append((text, kwargs))

    async def unavailable(*_args, **_kwargs):
        raise Exception("AI cooldown")

    monkeypatch.setattr(leisure_music.ai, "allm_json", unavailable)
    monkeypatch.setattr(leisure_music.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_music.store, "set_profile", lambda _cid, value: profile.update(value))
    monkeypatch.setattr(leisure_music.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_music.store, "mutate_kv", lambda _key, change: change({})[1])
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "values", lambda *_args: [])

    asyncio.run(leisure_music.send_listen(object(), "42", force=True, status=Status()))

    assert len(calls) == 1
    assert "FKA twigs" in calls[0][0]
    assert "Не удалось подобрать" not in calls[0][0]


def test_music_home_shows_daily_vibe_and_nearest_concert_without_ai(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def concerts(_cid):
        return [{"artist": "Romy", "date": "21 августа", "place": "Алкмар"}]

    async def daily_content():
        return {
            "vibe": {"track": "Introvert", "artist": "Little Simz", "tag": "Для собранного фокуса"},
            "rebus": {"emoji": "👑 🐝 🎤", "answer": "Beyoncé", "fact": "Факт."},
            "legend": {"name": "Луи Армстронг", "detail": "трубач и певец"},
        }

    monkeypatch.setattr(leisure_music, "_weekly_concerts", concerts)
    monkeypatch.setattr(leisure_music, "_daily_music_content", daily_content)
    monkeypatch.setattr(leisure_music.store, "get_settings", lambda _cid: {"cc": "NL", "city": "Алкмар"})
    monkeypatch.setattr(leisure_music.ai, "allm_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("AI called")))

    asyncio.run(leisure_music.send_music_home(Bot(), "42"))

    assert len(sent) == 1
    assert "🎧 Музыка этой недели · Алкмар" in sent[0]["text"]
    assert "Вайб дня: Introvert — Little Simz (Для собранного фокуса)" in sent[0]["text"]
    assert "Музыкальный ребус: 👑 🐝 🎤 → Beyoncé" in sent[0]["text"]
    assert "Легенда дня: Луи Армстронг — трубач и певец." in sent[0]["text"]
    assert "Концерты рядом: Romy · 21 августа · Алкмар" in sent[0]["text"]
    assert "Новые альбомы" not in sent[0]["text"]
    assert any(entity.type == MessageEntity.SPOILER for entity in sent[0]["entities"])


def test_music_task_returns_a_usable_track(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(leisure_music, "_task_for_today", lambda _key: {
        "title": "Тренировка", "track": "Gorilla", "artist": "Little Simz",
        "tag": "Уверенный грув.", "note": "Когда нужен темп.",
    })

    asyncio.run(leisure_music.send_music_task(Bot(), "42", "workout"))

    assert "Gorilla — Little Simz" in sent[0]["text"]
    assert [(button.text, button.callback_data) for button in sent[0]["reply_markup"].inline_keyboard[0]] == [
        ("⬅️ Назад", "m_music"), ("#️⃣ Главная", "m_menu"),
    ]


def test_music_legend_does_not_retry_an_empty_daily_cache(monkeypatch):
    today = date(2026, 8, 5)
    cache = {today.isoformat(): {"legend": {}}}
    monkeypatch.setattr(leisure_music.store, "_load", lambda _key: cache)
    monkeypatch.setattr(leisure_music.requests, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))

    assert leisure_music._load_music_legend(today) == {}
