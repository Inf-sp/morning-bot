import pytest

from ui import weather


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _bold_texts(msg):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == "bold"]


@pytest.mark.unit
def test_weather_day_forecast_keeps_header_and_fact_verbatim():
    msg = weather.day_forecast(
        "Погода • Amsterdam <NL>",
        ["☀️ До +20°C"],
        fact_title="Метео-факт",
        fact="ветер <сильный>",
    )

    assert msg.text.startswith("Погода • Amsterdam <NL>")
    assert "Погода • Amsterdam <NL>" in _bold_texts(msg)
    assert "Метео-факт" in _bold_texts(msg)
    assert "ветер <сильный>" in msg.text


@pytest.mark.unit
def test_weather_week_forecast_builds_compact_message():
    msg = weather.week_forecast(
        "1–7 июля",
        "Amsterdam",
        "🇳🇱",
        [{"icon": "🌧️", "label": "Пн-Ср", "desc": "дождь утром", "temp": "+18…+20°C"}],
        "Будет влажно",
    )

    assert "Ближайшая неделя • 1–7 июля • Amsterdam 🇳🇱" in _bold_texts(msg)
    assert "🌧️ Пн-Ср — дождь утром, +18…+20°C" in msg.text
    assert "Метео-итог" in _bold_texts(msg)
    assert "Будет влажно." in msg.text


@pytest.mark.unit
def test_weather_city_and_storm_messages():
    alert = weather.storm_alert(["wind", "rain"], 16, is_nl=True)
    assert "⚠️ Штормовое предупреждение (Code Geel)" in alert.text
    assert any("Штормовое предупреждение" in b for b in _bold_texts(alert))
    assert "NS" in alert.text

    assert "Не нашёл город" in weather.city_not_found("X").text
    assert weather.city_changed("Амстердам", "Нидерланды").text.endswith("Амстердам, Нидерланды.")
    assert weather.location_changed("Амстердам").text == "Готово. Ты находишься в городе Амстердам."


@pytest.mark.unit
def test_storm_alert_html_matches_storm_alert_content():
    html = weather.storm_alert_html(["snow"], 5, is_nl=False)
    msg = weather.storm_alert(["snow"], 5, is_nl=False)

    assert "<b>" in html
    assert "Снег и гололёд" in msg.text
    assert "Снег и гололёд" in html
