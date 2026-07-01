import pytest

import weather


@pytest.mark.unit
def test_weather_main_lines_puts_strong_wind_after_blank_line():
    lines = weather._weather_main_lines("🌧️", 20, 96, None, " (утром, днём)", 8)

    assert lines == [
        "🌧️ До +20°C • Дождь (утром, днём) 96%",
        "",
        "⚠️ Сильный ветер 8 м/с",
    ]
