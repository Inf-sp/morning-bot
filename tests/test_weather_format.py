import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import weather


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
