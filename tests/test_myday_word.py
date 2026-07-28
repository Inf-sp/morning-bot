import os
from datetime import datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import myday
from myday import _day_wind_text
from ui.myday import day_summary


def test_day_summary_keeps_only_compact_weather_block():
    message = day_summary(
        "Вс, 26 июля",
        "Алкмар",
        weather_icon="🌧️",
        weather_line="до +21°C · Дождь днём и вечером · Ветер до 10 м/с",
    )

    assert "🌧️ Погода: до +21°C · Дождь днём и вечером · Ветер до 10 м/с" in message.text
    assert "влажност" not in message.text.lower()
    assert "100%" not in message.text


def test_day_wind_text_marks_only_wind_above_ten_as_strong():
    assert _day_wind_text(10) == "Ветер до 10 м/с"
    assert _day_wind_text(11) == "Сильный ветер до 11 м/с"


def test_day_summary_keeps_capitalized_dictionary_translation_after_arrow():
    message = day_summary(
        "Ср, 15 июля",
        "Алкмар",
        flag="🇳🇱",
        word_line="Slim → Худой, умный.",
        word_lang="nl",
    )

    assert "🇳🇱 Нидерландский: Slim → Худой, умный." in message.text
    assert "→ Худой" in message.text


def test_yesterdays_day_cache_never_hides_todays_mood(monkeypatch):
    cid = "myday-new-mood"
    myday._day_cache.pop(cid, None)
    monkeypatch.setattr(myday.store, "get_profile", lambda _cid: {
        "myday_home_cache": {
            "date": "2026-07-27",
            "version": myday._DAY_CACHE_VERSION,
            "text": "⚡️ Настрой: Вчерашняя фраза.",
            "entities": [],
            "ts": 0,
        },
    })

    assert myday._load_day_cache(cid, "2026-07-28") is None


def test_daily_mood_never_repeats_when_hashes_collide(monkeypatch):
    class FixedDateTime:
        current = datetime(2026, 7, 27)

        @classmethod
        def now(cls, _tz):
            return cls.current

    class SameDigest:
        def digest(self):
            return b"\0" * 32

    monkeypatch.setattr("balance.datetime", FixedDateTime)
    monkeypatch.setattr("balance.hashlib.sha256", lambda _value: SameDigest())

    yesterday = __import__("balance").health_focus("42")["phrase"]
    FixedDateTime.current = datetime(2026, 7, 28)
    today = __import__("balance").health_focus("42")["phrase"]

    assert today != yesterday
