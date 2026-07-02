import pytest

from ui import weather


@pytest.mark.unit
def test_weather_day_forecast_escapes_header_and_fact():
    msg = weather.day_forecast(
        "Погода • Amsterdam <NL>",
        ["☀️ До +20°C"],
        fact_title="Метео-факт",
        fact="ветер <сильный>",
    )

    assert msg.parse_mode == "HTML"
    assert msg.text.startswith("<b>Погода • Amsterdam &lt;NL&gt;</b>")
    assert "🌡️ <b>Метео-факт</b>" in msg.text
    assert "ветер &lt;сильный&gt;" in msg.text


@pytest.mark.unit
def test_weather_week_forecast_builds_compact_html():
    msg = weather.week_forecast(
        "1–7 июля",
        "Amsterdam",
        "🇳🇱",
        [{"icon": "🌧️", "label": "Пн-Ср", "desc": "дождь утром", "temp": "+18…+20°C"}],
        "Будет влажно",
    )

    assert msg.parse_mode == "HTML"
    assert "<b>Ближайшая неделя • 1–7 июля • Amsterdam 🇳🇱</b>" in msg.text
    assert "🌧️ Пн-Ср — дождь утром, +18…+20°C" in msg.text
    assert "🌡️ <b>Метео-итог</b>" in msg.text
    assert "Будет влажно." in msg.text
