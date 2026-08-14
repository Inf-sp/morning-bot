import os

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


def test_daytime_temperature_uses_only_hours_from_eight_to_twenty():
    data = {
        "hourly": {
            "time": [
                "2026-08-14T07:00", "2026-08-14T08:00", "2026-08-14T19:00",
                "2026-08-14T20:00", "2026-08-14T23:00",
            ],
            "temperature_2m": [8, 12, 21, 15, 9],
        },
    }

    assert weather._daytime_temperature_range(data, "2026-08-14", 7, 25) == (12, 21)
