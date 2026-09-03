import asyncio
import os
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import weather
import weather_weekly
import weather_provider
import weather_warn
import settings
import bot
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


def test_full_forecast_uses_morning_periods_sun_and_practical_advice():
    message = weather_ui.full_forecast(
        "Полный прогноз • Пт, 21 августа · Alkmaar, NL 🇳🇱",
        None,
        [
            {"title": "☀️ Утром", "lines": ["Температура до +18°C", "Ветер 4 м/с"]},
            {"title": "🌧️ Днём", "lines": ["Температура до +20°C", "Дождь 70%"]},
            {"title": "🌧️ Ночью", "lines": ["Температура до +15°C", "Дождь 60%"]},
        ],
        "Восход 06:25 → Закат 21:02",
        "",
        "Завтра будет часто идти дождь, но возможны короткие сухие окна.",
    )

    assert message.text.startswith(
        "Полный прогноз • Пт, 21 августа · Alkmaar, NL 🇳🇱\n\n"
        "Восход 06:25 → Закат 21:02\n\n"
        "☀️ Утром"
    )
    assert "Сейчас" not in message.text
    assert "• Ветер 4 м/с" in message.text
    assert "🌧️ Днём" in message.text
    assert "🌧️ Ночью" in message.text
    assert "08:00–12:00" not in message.text
    assert "12:00–18:00" not in message.text
    assert "• Температура до +20°C" in message.text
    assert "• Дождь 70%" in message.text
    assert "Облачность" not in message.text
    assert "порывы" not in message.text
    assert "Вероятность" not in message.text
    assert "Осадки до" not in message.text
    assert "Восход 06:25 → Закат 21:02" in message.text
    assert message.text.index("Закат 21:02") < message.text.index("🌧️ Днём")
    assert "☀️ Солнце" not in message.text
    assert "💡 Полезно: Завтра будет часто идти дождь" in message.text


def test_full_forecast_at_23_has_no_remaining_daytime_parts():
    parts = weather._full_forecast_parts(datetime(2026, 8, 25, 23, 0, tzinfo=weather.TZ))

    assert parts == []


def test_full_forecast_in_the_morning_starts_with_the_daytime_block():
    parts = weather._full_forecast_parts(datetime(2026, 8, 25, 9, 0, tzinfo=weather.TZ))

    assert parts == [("Днём", 12, 18), ("Вечером", 18, 24)]


def test_full_forecast_at_noon_starts_with_the_evening_block():
    parts = weather._full_forecast_parts(datetime(2026, 8, 25, 12, 0, tzinfo=weather.TZ))

    assert parts == [("Вечером", 18, 24)]


def test_full_forecast_in_the_evening_skips_the_daytime_block():
    parts = weather._full_forecast_parts(datetime(2026, 8, 25, 17, 43, tzinfo=weather.TZ))

    assert parts == [("Вечером", 18, 24)]


def test_full_forecast_after_midnight_starts_with_the_coming_morning():
    parts = weather._full_forecast_parts(datetime(2026, 8, 26, 0, 15, tzinfo=weather.TZ))

    assert parts == [
        ("Утром", 8, 12),
        ("Днём", 12, 18),
        ("Вечером", 18, 24),
    ]


def test_period_with_zero_rain_probability_uses_cloud_without_rain():
    assert weather._period_weather_icon("Днём", 61, 20, 0, 6, 0) == "☁️"
    assert weather._period_weather_icon("Ночью", 0, 15, 0, 2, 0) == "☁️"
    assert weather._period_weather_icon("Ночью", 61, 15, 90, 2, 4) == "🌧️"


def test_weather_warning_lists_rain_periods_with_probabilities():
    data = {
        "hourly": {
            "time": [
                "2026-08-14T07:00", "2026-08-14T08:00", "2026-08-14T09:00",
                "2026-08-14T10:00", "2026-08-14T11:00", "2026-08-14T12:00",
                "2026-08-14T13:00",
            ],
            "precipitation_probability": [0, 60, 75, 0, 70, 70, 0],
        },
    }

    assert weather_warn._rain_when(data, "2026-08-14") == (
        "08:00–10:00 · дождь 60–75%\n11:00–13:00 · дождь 70%"
    )


def test_weather_adapter_keeps_sunset_from_current_conditions():
    payload = weather_provider._adapt_openweather(
        {"data": [{"dt": 1787284800, "sunrise": 1787276700, "sunset": 1787338920}]},
        {"data": []},
        {"data": [{"dt": 1787284800, "temp": {}, "weather": []}]},
    )

    assert payload["daily"]["sunrise"][0]
    assert payload["daily"]["sunset"][0]
    assert payload["daily"]["sunset"][0].endswith("21:02")


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


def test_qualitative_outlook_describes_weather_without_numbers():
    days = [
        {"code": 61, "tmax": 19, "wind": 9, "rain_real": True},
        {"code": 3, "tmax": 18, "wind": 5, "rain_real": False},
    ]

    outlook = weather_weekly.qualitative_outlook(days)

    assert outlook.startswith("На следующей неделе")
    assert "дожд" in outlook
    assert not any(character.isdigit() for character in outlook)


def test_week_useful_label_is_bold():
    message = weather_ui.week_forecast(
        "1–7 сен", "Alkmaar", "Переменно", [],
        "На следующей неделе будет переменчиво.", country="NL", country_code="nl",
    )

    useful = [entity for entity in message.entities if entity.type == "bold"][-1]
    assert useful.length == len("💡 Полезно:".encode("utf-16-le")) // 2


def test_weather_warning_is_scheduled_for_eight():
    assert bot._WEATHER_WARNING_TIME == "08:00"


def test_morning_myday_notification_is_removed():
    assert "morning_brief" not in dict(settings.NOTIF_TYPES)
    assert "morning_brief" not in settings._ADMIN_NOTIFICATION_META
    assert not hasattr(bot, "job_morning_brief")


def test_weather_warning_is_a_notification_option_enabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "get", lambda *_args: None)

    option = next(item for item in settings.get_notification_options() if item.key == "weather_warn")

    assert settings.notif_on("42", "weather_warn") is True
    assert option.button_label == "Погодное предупреждение · 08:00, если есть повод"


def test_weather_warning_can_be_disabled(monkeypatch):
    monkeypatch.setattr(
        settings,
        "get",
        lambda _cid, key, default=None: False if key == "notif_weather_warn" else default,
    )

    assert settings.notif_on("42", "weather_warn") is False


def test_weather_warning_job_skips_disabled_users(monkeypatch):
    sent = []

    async def send_notification(*args):
        sent.append(args)

    monkeypatch.setattr(bot.access, "get_allowed_cids", lambda: ["42"])
    monkeypatch.setattr(settings, "notif_on", lambda *_args: False)
    monkeypatch.setattr(settings, "send_scheduled_notification", send_notification)

    asyncio.run(bot.job_weather_warn(SimpleNamespace(bot=object())))

    assert sent == []


def test_weather_warning_notification_links_only_to_home(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(settings.store, "get_settings", lambda _cid: {"lat": 52.6, "lon": 4.7})
    monkeypatch.setattr(weather, "fetch_weather", lambda *_args: {"daily": {}})
    monkeypatch.setattr(
        weather_warn,
        "build_warning",
        lambda *_args: weather_ui.weather_warning(
            ["🌧️ Ожидается сильный дождь."],
            "08:00–11:00",
            ["Возьми дождевик."],
        ),
    )

    asyncio.run(settings._send_scheduled_notification(Bot(), "42", "weather_warn"))

    keyboard = sent[0]["reply_markup"].inline_keyboard
    assert [[(button.text, button.callback_data) for button in row] for row in keyboard] == [
        [("🔕 Отключить уведомления", "set_notifpush_weather_warn")],
        [("#️⃣ Главная", "m_menu")],
    ]


def test_evening_weather_is_at_twenty_and_links_to_week_and_home(monkeypatch):
    calls = []
    notification_bot = object()

    async def send_weather(target_bot, cid, mode, status=None, reply_markup=None):
        calls.append((target_bot, cid, mode, status, reply_markup))

    monkeypatch.setattr(weather, "send_weather", send_weather)

    asyncio.run(settings._send_scheduled_notification(
        notification_bot, "42", "evening_weather",
    ))

    option = next(
        item for item in settings.get_notification_options()
        if item.key == "evening_weather"
    )
    keyboard = calls[0][4].inline_keyboard
    assert settings.EVENING_WEATHER_TIME == "20:00"
    assert option.button_label == "Погода на завтра · 20:00"
    assert calls[0][:4] == (notification_bot, "42", "tomorrow_plain", None)
    assert [[(button.text, button.callback_data) for button in row] for row in keyboard] == [
        [("🔕 Отключить уведомления", "set_notifpush_evening_weather")],
        [("🗓️ Погода на неделю", "a_w_week")],
        [("#️⃣ Главная", "m_menu")],
    ]


def test_notification_can_be_disabled_in_place_and_enabled_again(monkeypatch):
    saved = []
    edited = []
    markup = settings.notification_markup("daily_words", [[
        settings.InlineKeyboardButton("🧠 Обучение", callback_data="notify_learning"),
        settings.InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ]])

    class Query:
        message = SimpleNamespace(reply_markup=markup)

        async def edit_message_reply_markup(self, **kwargs):
            edited.append(kwargs["reply_markup"])

    monkeypatch.setattr(settings, "notif_on", lambda *_args: True)
    monkeypatch.setattr(settings, "set_", lambda cid, key, value: saved.append((cid, key, value)))

    asyncio.run(settings.handle_callback(
        object(), "42", "set_notifpush_daily_words", Query(),
    ))

    keyboard = edited[0].inline_keyboard
    assert saved == [("42", "notif_daily_words", False)]
    assert [[(button.text, button.callback_data) for button in row] for row in keyboard] == [
        [("✅ Включить уведомления", "set_notifpush_daily_words")],
        [("🧠 Обучение", "notify_learning")],
        [("#️⃣ Главная", "m_menu")],
    ]


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
