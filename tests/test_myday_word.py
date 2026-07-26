import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

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
