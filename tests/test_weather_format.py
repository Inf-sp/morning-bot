import os
from datetime import datetime, timedelta

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import weather
from ui import weather as weather_ui


def test_tomorrow_forecast_uses_compact_period_weather_line():
    line = weather._compact_forecast_line(
        "🌧️", 20, 52, 3.2, ["днём", "вечером"], 9,
    )

    assert line == "🌧️ Погода: до +20°C · Дождь днём и вечером · Ветер до 9 м/с"
    assert "52%" not in line
    assert "•" not in line
    assert "сильный" not in line.casefold()


def test_tomorrow_forecast_marks_wind_above_ten_as_strong():
    line = weather._compact_forecast_line("🌧️", 20, 0, None, [], 11)

    assert line == "🌧️ Погода: до +20°C · Сильный ветер до 11 м/с"


def test_city_change_confirmation_includes_country_flag():
    message = weather_ui.city_changed("Лилль", "FR", "fr")

    assert message.text == "✅ Готово. Город переключён на Лилль, FR 🇫🇷."


def test_week_forecast_header_shows_country_and_flag():
    message = weather_ui.week_forecast(
        "5–11 августа", "Лилль", "Тепло", [], "Возьми воду", country="FR", country_code="fr",
    )

    assert message.text.startswith("Неделя с 5–11 августа · Лилль, FR 🇫🇷")


def test_week_forecast_marks_extreme_heat_as_a_reason_to_change_plans():
    days = [
        {"name": "вторник", "tmax": 33, "tmin": 20, "wind": 4, "rain_real": False},
        {"name": "среда", "tmax": 37, "tmin": 22, "wind": 4, "rain_real": False},
        {"name": "четверг", "tmax": 40, "tmin": 24, "wind": 4, "rain_real": False},
        {"name": "пятница", "tmax": 34, "tmin": 21, "wind": 4, "rain_real": False},
        {"name": "суббота", "tmax": 31, "tmin": 19, "wind": 4, "rain_real": False},
        {"name": "воскресенье", "tmax": 30, "tmin": 18, "wind": 4, "rain_real": False},
        {"name": "понедельник", "tmax": 30, "tmin": 18, "wind": 4, "rain_real": False},
    ]

    advice = weather._week_advice(days)
    message = weather_ui.week_forecast("11–17 августа", "Руан", "Жарко", [], advice, country="FR")

    assert advice.startswith("В четверг до +40°C")
    assert "избегай долгих прогулок и велосипеда днём" in advice
    assert "💡 Полезно: В четверг до +40°C" in message.text


def test_month_forecast_groups_days_into_compact_weekly_periods():
    start = datetime(2026, 8, 14, tzinfo=weather.TZ)
    records = [
        {
            "dt": int((start + timedelta(days=index)).timestamp()),
            "temp": {"min": 13 + index % 2, "max": 21 + index % 3},
            "weather": [{"id": 500 if index in (1, 5) else 801}],
            "pop": 0.7 if index in (1, 5) else 0.1,
            "rain": 1 if index in (1, 5) else 0,
            "wind_speed": 4,
        }
        for index in range(14)
    ]

    periods = weather._month_periods(records)
    message = weather_ui.month_forecast(
        "14–27 августа", "Алкмар", periods, "Проверь дождь ближе к дате", country="NL", days=14,
    )

    assert len(periods) == 2
    assert periods[0]["rain"] == "дождь 2 дн."
    assert "Ближайшие 14 дней · 14–27 августа · Алкмар, NL 🇳🇱" in message.text
    assert "14–20 августа" in message.text
    assert "Чем дальше дата, тем прогноз ориентировочнее." in message.text
