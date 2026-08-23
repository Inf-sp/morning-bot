import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import myday
import config
from myday import _day_wind_text
from ui.myday import day_summary


def test_myday_inline_open_builds_a_missing_daily_cache(monkeypatch):
    class Status:
        replaced = None

        async def replace(self, text, **kwargs):
            self.replaced = (text, kwargs)

    monkeypatch.setattr(myday, "_load_day_cache", lambda *_args: None)
    monkeypatch.setattr(myday, "_build_day_text", lambda *_args, **_kwargs: ("Сводка готова", []))
    monkeypatch.setattr(
        myday,
        "_save_day_cache",
        lambda *_args: {"text": "Сводка готова", "entities": []},
    )
    status = Status()

    asyncio.run(myday.send_plany(object(), "42", status=status))

    assert status.replaced[0] == "Сводка готова"


def test_myday_menu_offers_detailed_day_and_week_weather():
    keyboard = myday._day_menu_kb().inline_keyboard
    callbacks = [button.callback_data for row in keyboard for button in row]

    assert callbacks == ["a_w_full", "a_w_week", "m_menu"]
    assert keyboard[0][0].text == "🗓️ Полный прогноз на сегодня"
    assert keyboard[1][0].text == "🗓️ Погода на неделю"


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


def test_myday_skips_language_material_when_learning_is_disabled(monkeypatch):
    monkeypatch.setattr(myday.store, "learning_is_enabled", lambda _cid: False)
    monkeypatch.setattr(myday.learning, "select_daily_material", lambda _cid: (_ for _ in ()).throw(AssertionError("no lookup")))

    assert myday._word_of_day("42") == ("", "")


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


def test_yesterdays_day_cache_never_hides_todays_summary(monkeypatch):
    cid = "myday-new-summary"
    myday._day_cache.pop(cid, None)
    monkeypatch.setattr(myday.store, "get_profile", lambda _cid: {
        "myday_home_cache": {
            "date": "2026-07-27",
            "version": myday._DAY_CACHE_VERSION,
            "text": "Вчерашняя сводка.",
            "entities": [],
            "ts": 0,
        },
    })

    assert myday._load_day_cache(cid, "2026-07-28") is None


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


def test_daily_literary_quote_is_cached_as_a_book_quote(monkeypatch):
    saved = []
    monkeypatch.setattr(myday.store, "get_profile", lambda _cid: {})
    monkeypatch.setattr(myday.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(
        myday.store, "mutate_profile",
        lambda _cid, change: saved.append(change({})[0]),
    )
    monkeypatch.setattr(myday, "_motivating_book_quote", lambda *_args: ({
        "id": "старик и море",
        "quote": "Человека можно уничтожить, но нельзя победить.",
        "src": "Эрнест Хемингуэй",
    }, []))

    quote = myday._daily_literary_quote("42")

    assert quote["quote"] == "Человека можно уничтожить, но нельзя победить."
    assert saved[0]["myday_quote_cache"]["kind"] == "book"
    assert saved[0]["myday_quote_history"] == ["старик и море"]


def test_motivating_quotes_do_not_repeat_before_pool_is_exhausted(monkeypatch):
    monkeypatch.setattr(myday.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(myday.random, "choice", lambda values: values[0])
    first, history = myday._motivating_book_quote("42", [])
    second, _history = myday._motivating_book_quote("42", [first["id"]])

    assert first["id"] != second["id"]
    assert first["id"] not in history


def test_unseen_quote_from_favorite_book_has_priority(monkeypatch):
    monkeypatch.setattr(
        myday.store, "get_list",
        lambda key, _cid: ["Атомные привычки"] if key == config.FAVORITE_BOOKS_KEY else [],
    )
    quote, _history = myday._motivating_book_quote("42", [])

    assert quote["id"] == "атомные привычки"


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
    monkeypatch.setattr(
        myday.store, "mutate_profile",
        lambda cid, change: saved_profiles.append((cid, change({})[0])),
    )
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
    monkeypatch.setattr(myday.store, "mutate_profile", lambda _cid, change: change({})[1])
    monkeypatch.setattr(myday.ai, "llm_json", lambda *_args, **_kwargs: {
        "quote": "Музыка помогает чувствовать связь.", "src": "Дэвид Боуи",
    })

    assert myday._fetch_quote("artist-quote-reject") == {}
