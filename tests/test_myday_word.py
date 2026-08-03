import os
from datetime import datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import myday
import config
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


def test_quote_fallback_uses_fresh_author_after_favorite_book_was_shown(monkeypatch):
    def get_list(key, _cid):
        if key == config.FAVORITE_BOOKS_KEY:
            return ["Маленький принц"]
        if key == config.QUOTE_AUTHORS_KEY:
            return ["Антуан де Сент-Экзюпери"]
        return []

    monkeypatch.setattr(myday.store, "get_list", get_list)

    quote = myday._book_quote_fallback("quote-fresh-author")

    assert quote["src"] != "Антуан де Сент-Экзюпери"


def test_quote_uses_a_favorite_artist_when_the_list_is_not_empty(monkeypatch):
    saved_profiles = []
    prompt = []

    def get_list(key, _cid):
        if key == config.FAVORITE_ARTISTS_KEY:
            return ["Romy"]
        return []

    monkeypatch.setattr(myday.store, "get_list", get_list)
    monkeypatch.setattr(myday.store, "get_profile", lambda _cid: {})
    monkeypatch.setattr(myday.store, "set_list", lambda *_args: None)
    monkeypatch.setattr(myday.store, "set_profile", lambda *_args: saved_profiles.append(_args))
    monkeypatch.setattr(myday.ai, "llm_json", lambda text, *_args, **_kwargs: (
        prompt.append(text) or {"quote": "Музыка помогает чувствовать связь.", "src": "Romy"}
    ))

    quote = myday._fetch_quote("artist-quote")

    assert quote["src"] == "Romy"
    assert "только одного исполнителя из списка" in prompt[0]
    assert saved_profiles


def test_quote_does_not_substitute_another_author_when_favorite_artist_exists(monkeypatch):
    def get_list(key, _cid):
        if key == config.FAVORITE_ARTISTS_KEY:
            return ["Romy"]
        return []

    monkeypatch.setattr(myday.store, "get_list", get_list)
    monkeypatch.setattr(myday.store, "get_profile", lambda _cid: {})
    monkeypatch.setattr(myday.store, "set_list", lambda *_args: None)
    monkeypatch.setattr(myday.store, "set_profile", lambda *_args: None)
    monkeypatch.setattr(myday.ai, "llm_json", lambda *_args, **_kwargs: {
        "quote": "Музыка помогает чувствовать связь.", "src": "Дэвид Боуи",
    })

    assert myday._fetch_quote("artist-quote-reject") == {}
