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

    assert msg.text == "Погода • Amsterdam <NL>\n\n☀️ До +20°C\n\n🌡️ Метео-факт\nветер <сильный>"
    assert msg.text.startswith("Погода • Amsterdam <NL>")
    assert _bold_texts(msg) == ["Погода • Amsterdam <NL>", "Метео-факт"]
    assert "ветер <сильный>" in msg.text


@pytest.mark.unit
def test_weather_day_forecast_fact_without_title_gets_blank_line_before_it():
    msg = weather.day_forecast("Погода • Amsterdam", ["☀️ До +20°C"], fact="просто факт")

    assert msg.text == "Погода • Amsterdam\n\n☀️ До +20°C\n\nпросто факт"
    assert _bold_texts(msg) == ["Погода • Amsterdam"]


@pytest.mark.unit
def test_weather_day_forecast_embeds_alert_html_and_ignores_fact():
    alert_html = weather.storm_alert_html(["wind"], 12, is_nl=False)
    msg = weather.day_forecast(
        "Погода • Amsterdam",
        ["☀️ До +20°C"],
        alert=alert_html,
        fact_title="Метео-итог",
        fact="этот факт должен быть проигнорирован",
    )

    assert msg.text.startswith("Погода • Amsterdam\n\n☀️ До +20°C\n\n⚠️ Штормовое предупреждение")
    assert "этот факт должен быть проигнорирован" not in msg.text
    assert _bold_texts(msg) == ["Погода • Amsterdam", "Штормовое предупреждение"]


@pytest.mark.unit
def test_weather_full_forecast_builds_periods_with_optional_joke():
    periods = [
        {"label": "Утро", "line": "☀️ +15°C"},
        {"label": "День", "line": "⛅ +20°C"},
    ]
    msg = weather.full_forecast("Прогноз • Amsterdam", periods, joke="Погода шепчет")

    assert msg.text == "Прогноз • Amsterdam\n\nУтро:\n☀️ +15°C\n\nДень:\n⛅ +20°C\n\nПогода шепчет"
    assert _bold_texts(msg) == ["Прогноз • Amsterdam", "Утро:", "День:"]


@pytest.mark.unit
def test_weather_full_forecast_without_joke_has_no_trailing_blank_line():
    msg = weather.full_forecast("Прогноз • Amsterdam", [{"label": "Утро", "line": "☀️ +15°C"}])

    assert msg.text == "Прогноз • Amsterdam\n\nУтро:\n☀️ +15°C"


@pytest.mark.unit
def test_weather_week_forecast_builds_compact_message():
    msg = weather.week_forecast(
        "1–7 июля",
        "Amsterdam",
        "🇳🇱",
        [{"icon": "🌧️", "label": "Пн-Ср", "desc": "дождь утром", "temp": "+18…+20°C"}],
        "Будет влажно",
    )

    assert msg.text == (
        "Ближайшая неделя • 1–7 июля • Amsterdam 🇳🇱\n\n"
        "🌧️ Пн-Ср — дождь утром, +18…+20°C\n\n"
        "🌡️ Метео-итог\nБудет влажно."
    )
    assert _bold_texts(msg) == ["Ближайшая неделя • 1–7 июля • Amsterdam 🇳🇱", "Метео-итог"]


@pytest.mark.unit
def test_weather_week_forecast_without_summary_has_no_trailing_blank_line():
    msg = weather.week_forecast(
        "1–7 июля",
        "Amsterdam",
        "🇳🇱",
        [{"icon": "🌧️", "label": "Пн-Ср", "desc": "дождь утром", "temp": "+18…+20°C"}],
    )

    assert msg.text == "Ближайшая неделя • 1–7 июля • Amsterdam 🇳🇱\n\n🌧️ Пн-Ср — дождь утром, +18…+20°C"


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
