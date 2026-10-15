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
from fakes import RecordingBot


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
    assert "Завтра будет часто идти дождь" in message.text


def test_full_forecast_at_23_keeps_the_rest_of_the_evening():
    parts = weather._full_forecast_parts(datetime(2026, 8, 25, 23, 0, tzinfo=weather.TZ))

    assert parts == [("Вечером", 18, 24)]


def test_full_forecast_in_the_morning_starts_with_the_current_morning():
    parts = weather._full_forecast_parts(datetime(2026, 8, 25, 9, 0, tzinfo=weather.TZ))

    assert parts == [("Утром", 8, 12), ("Днём", 12, 18), ("Вечером", 18, 24)]


def test_full_forecast_at_noon_starts_with_the_daytime_block():
    parts = weather._full_forecast_parts(datetime(2026, 8, 25, 12, 0, tzinfo=weather.TZ))

    assert parts == [("Днём", 12, 18), ("Вечером", 18, 24)]


def test_full_forecast_after_midnight_starts_with_the_coming_morning():
    parts = weather._full_forecast_parts(datetime(2026, 8, 26, 0, 15, tzinfo=weather.TZ))

    assert parts == [
        ("Утром", 8, 12),
        ("Днём", 12, 18),
        ("Вечером", 18, 24),
    ]


def test_daily_rain_code_without_real_rain_gives_no_rain_icon():
    # OWM ставит суточный код «дождь», даже если капнет только ночью.
    assert weather.weather_icon(61, 19, 0, 5, 0) == "☁️"
    assert weather.weather_icon(95, 19, 20, 5, 0) == "☁️"
    assert weather.weather_icon(61, 19, 0, 5, 0, clouds=10) == "☀️"
    assert weather.weather_icon(61, 19, 0, 5, 0, clouds=50) == "🌤️"
    assert weather.weather_icon(61, 19, 80, 5, 2) == "🌧️"
    assert weather._week_icon(95, 19, 20, 5, 0) == "☁️"


def test_period_icon_follows_period_rain_and_clouds():
    assert weather._period_weather_icon("Днём", 61, 20, 0, 6, 0) == "☁️"
    assert weather._period_weather_icon("Ночью", 0, 15, 0, 2, 0) == "🌙"
    assert weather._period_weather_icon("Ночью", 61, 15, 90, 2, 4) == "🌧️"


def test_sun_clock_keeps_provider_local_time():
    assert weather._clock("2026-10-14T08:03") == "08:03"
    assert weather._clock("2026-10-14T06:03:00+00:00") == "08:03"


def _day_data(hours, temps, probs, mm, winds, clouds):
    return {
        "daily": {"time": ["2026-10-14", "2026-10-15"], "weathercode": [61, 3]},
        "hourly": {
            "time": [f"2026-10-14T{h:02d}:00" for h in hours] + ["2026-10-15T03:00"],
            "temperature_2m": temps + [12],
            "precipitation_probability": probs + [90],
            "precipitation": mm + [3],
            "windspeed_10m": winds + [9],
            "cloudcover": clouds + [100],
        },
    }


def test_today_outlook_matches_full_forecast_periods_and_skips_past_hours():
    data = _day_data(
        [7, 10, 13, 19], [19, 15, 16, 14], [80, 0, 10, 0], [2, 0, 0, 0], [9, 4, 5, 3], [100, 90, 90, 80],
    )
    now = datetime(2026, 10, 14, 10, 30, tzinfo=weather.TZ)

    labels = [p["label"] for p in weather.forecast_periods(data, now)]
    outlook = weather.today_outlook(data, now)

    assert labels == ["Утром", "Днём", "Вечером", "Ночью"]
    # 07:00 уже прошёл, ночь следующего дня в сводку дня не входит; «до» — максимум оставшегося дня.
    assert outlook["tmax"] == 16
    # Ветер и небо — текущего периода («Утром» 10–12), как его блок в «Полном прогнозе».
    assert outlook["wind_ms"] == 4
    assert weather.current_period_label(now) == "Утром"
    assert outlook["rain_periods"] == []
    assert weather.weather_icon(61, outlook["tmax"], outlook["rain"], outlook["wind_ms"],
                                outlook["rain_mm"], outlook["clouds"]) == "☁️"
    assert weather.sky_word(outlook["clouds"], 61) == "облачно"


def test_tomorrow_forecast_is_split_into_morning_day_and_evening():
    data = {
        "daily": {"time": ["2026-10-15", "2026-10-16"], "weathercode": [3, 61]},
        "hourly": {
            "time": ["2026-10-15T20:00", "2026-10-16T09:00", "2026-10-16T10:00", "2026-10-16T14:00",
                     "2026-10-16T20:00"],
            "temperature_2m": [11, 12, 14, 18, 15],
            "precipitation_probability": [0, 10, 5, 70, 0],
            "precipitation": [0, 0, 0, 2.5, 0],
            "windspeed_10m": [2, 3, 5, 11, 4],
            "cloudcover": [20, 30, 40, 100, 60],
        },
    }
    late_evening = datetime(2026, 10, 15, 21, 0, tzinfo=weather.TZ)  # рассылка накануне вечером

    lines = [weather._period_line(p) for p in weather.forecast_periods(data, late_evening, day=1)]

    assert [line.split(":")[0].split(" ", 1)[1] for line in lines] == ["Утром", "Днём", "Вечером"]  # весь день
    assert lines[0].endswith("Утром: до +14°C · Без дождя · Ветер 3–5 м/с")
    assert lines[1].endswith("Днём: до +18°C · Дождь 70% · Сильный ветер 11 м/с")
    assert lines[2].endswith("Вечером: до +15°C · Без дождя · Ветер 4 м/с")
    assert weather.forecast_periods(data, late_evening, day=2) == []  # нет данных на этот день


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


def test_qualitative_outlook_describes_weather_without_numbers():
    days = [
        {"code": 61, "tmax": 19, "wind": 9, "rain_real": True},
        {"code": 3, "tmax": 18, "wind": 5, "rain_real": False},
    ]

    outlook = weather_weekly.qualitative_outlook(days)

    assert outlook.startswith("На следующей неделе")
    assert "дожд" in outlook
    assert not any(character.isdigit() for character in outlook)


def test_week_advice_has_no_useful_label():
    message = weather_ui.week_forecast(
        "1–7 сен", "Alkmaar", "Переменно", [],
        "На следующей неделе будет переменчиво.", country="NL", country_code="nl",
    )

    assert "Полезно" not in message.text
    assert message.text.endswith("На следующей неделе будет переменчиво.")


def test_weather_warning_defaults_to_eight(monkeypatch):
    monkeypatch.setattr(settings, "get", lambda *_a: None)
    assert settings.notif_time("42", "weather_warn") == "08:00"


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

    asyncio.run(settings._send_scheduled_notification(RecordingBot(sent), "42", "weather_warn"))

    keyboard = sent[0]["reply_markup"].inline_keyboard
    assert [[(button.text, button.callback_data) for button in row] for row in keyboard] == [
        [("🔕 Отключить уведомления", "set_notifpush_weather_warn")],
        [("#️⃣ Главная", "m_menu")],
    ]


def test_evening_weather_is_at_twenty_and_has_settings_and_home_in_one_row(monkeypatch):
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
        [("🎚️ Настроить рассылку", "set_notif_new"), ("#️⃣ Главная", "m_menu")],
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


def test_full_and_week_forecast_share_cache_with_my_day():
    import weather_provider
    # «Мой день» (2 дня) и полный прогноз (9 дней) берут один ответ API.
    assert weather_provider._weather_cache_key(52.63, 4.75, 2) == weather_provider._weather_cache_key(52.63, 4.75, 9)


def test_forecast_shows_clear_message_when_weather_service_fails(monkeypatch):
    import asyncio
    import weather
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(weather.store, "get_settings", lambda _cid: {"city": "Alkmaar", "lat": 52.63, "lon": 4.75})
    monkeypatch.setattr(weather, "fetch_weather", lambda *_a: (_ for _ in ()).throw(RuntimeError("401")))
    for mode in ("full", "week"):
        asyncio.run(weather.send_weather(Bot(), "42", mode))

    assert [m["text"].splitlines()[0] for m in sent] == ["🌦️ Прогноз сейчас недоступен — сервис погоды не ответил."] * 2
    assert "Что-то пошло не так" not in sent[0]["text"]


def test_week_day_ignores_night_rain_when_daytime_hours_are_dry():
    hours = list(range(24))
    data = {
        "daily": {
            "time": ["2026-10-15"], "weathercode": [61], "temperature_2m_max": [17],
            "temperature_2m_min": [9], "precipitation_probability_max": [90],
            "precipitation_sum": [4], "windspeed_10m_max": [12],
        },
        "hourly": {
            "time": [f"2026-10-15T{h:02d}:00" for h in hours],
            "temperature_2m": [10 if h < 8 else 15 for h in hours],
            # Дождь и ветер только ночью, днём сухо и малооблачно.
            "precipitation_probability": [90 if h < 6 else 0 for h in hours],
            "precipitation": [1 if h < 6 else 0 for h in hours],
            "windspeed_10m": [12 if h < 6 else 4 for h in hours],
            "cloudcover": [100 if h < 6 else 40 for h in hours],
        },
    }

    day = weather.day_conditions(data, 0)

    assert (day["tmax"], day["wind"], day["rain_real"], day["sky"]) == (15, 4, False, "partly")
    assert weather._week_icon(day["code"], day["tmax"], day["rain"], day["wind"], day["rain_mm"], day["clouds"]) == "🌤️"


def test_week_day_without_full_hourly_window_uses_daily_aggregate():
    data = {
        "daily": {
            "time": ["2026-10-20"], "weathercode": [61], "temperature_2m_max": [14],
            "temperature_2m_min": [8], "precipitation_probability_max": [80],
            "precipitation_sum": [3], "windspeed_10m_max": [7],
        },
        "hourly": {"time": ["2026-10-20T08:00"], "temperature_2m": [30]},
    }

    day = weather.day_conditions(data, 0)

    assert (day["tmax"], day["wind"], day["rain_real"]) == (14, 7, True)


def test_week_overview_needs_real_precipitation_for_snow():
    days = [{"code": 71, "rain_real": False, "wind": 3, "sky": "cloudy"} for _ in range(7)]

    assert weather_weekly.week_overview(days) == "В основном облачно…"
    assert "снег" not in weather_weekly.qualitative_outlook(days)


def test_today_outlook_sky_follows_current_period_not_day_average():
    # Днём малооблачно, вечером пасмурно: строка «Моего дня» в 13:00 — как блок «Днём».
    data = _day_data([13, 14, 19, 20], [17, 17, 16, 15], [0, 0, 0, 0], [0, 0, 0, 0], [4, 4, 3, 4], [40, 50, 95, 95])
    outlook = weather.today_outlook(data, datetime(2026, 10, 14, 13, 5, tzinfo=weather.TZ))

    assert (outlook["tmax"], round(outlook["wind_ms"])) == (17, 4)
    assert weather.sky_word(outlook["clouds"], 61) == "малооблачно"
    assert weather.weather_icon(61, outlook["tmax"], outlook["rain"], outlook["wind_ms"],
                                outlook["rain_mm"], outlook["clouds"]) == "🌤️"


def test_tomorrow_notification_has_city_country_flag_and_three_periods(monkeypatch):
    hours = ["2026-10-16T09:00", "2026-10-16T14:00", "2026-10-16T20:00"]
    data = {
        "daily": {"time": ["2026-10-15", "2026-10-16"], "weathercode": [3, 3], "temperature_2m_min": [8, 10],
                  "temperature_2m_max": [15, 18], "windspeed_10m_max": [4, 5],
                  "precipitation_probability_max": [0, 0], "precipitation_sum": [0, 0]},
        "hourly": {"time": hours, "temperature_2m": [12, 18, 15], "precipitation_probability": [0, 0, 0],
                   "precipitation": [0, 0, 0], "windspeed_10m": [3, 5, 4], "cloudcover": [60, 60, 60]},
    }
    sent = []

    monkeypatch.setattr(weather.store, "get_settings",
                        lambda _cid: {"lat": 52.6, "lon": 4.7, "city": "Alkmaar", "cc": "nl"})
    monkeypatch.setattr(weather, "fetch_weather", lambda *_args: data)
    monkeypatch.setattr(weather.ai, "allm", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no AI")))

    asyncio.run(weather.send_weather(RecordingBot(sent), "42", "tomorrow_plain"))

    lines = sent[0]["text"].split("\n")
    assert lines[0].endswith(" · Alkmaar, NL 🇳🇱") and "📍" not in lines[0]
    assert [line.split(":")[0].split(" ", 1)[1] for line in lines[2:5]] == ["Утром", "Днём", "Вечером"]
    assert len(lines) == 5 and "Метео-итог" not in sent[0]["text"]  # только три части суток, без AI-итога
