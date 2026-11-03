import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import bot
import settings


def _settings(monkeypatch, saved):
    monkeypatch.setattr(settings, "get", lambda _cid, key, default=None: saved.get(key, default))
    monkeypatch.setattr(settings, "set_", lambda _cid, key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(settings.store, "get_settings", lambda _cid: {"cc": "NL"})


def test_notification_time_defaults_and_only_known_slots(monkeypatch):
    saved = {"notif_time_daily_words": "08:00", "notif_time_evening_weather": "03:00"}
    _settings(monkeypatch, saved)

    assert settings.notif_time("42", "daily_words") == "08:00"
    assert settings.notif_time("42", "evening_weather") == "20:00"  # неизвестное значение — по умолчанию
    assert settings.user_notif_label("42", "weekend_events") == "Концерты недели · пт 10:00"
    assert settings.user_notif_label("42", "ns_disruptions") == "Поезда NS · при сбое"


def test_job_slot_sends_only_to_users_who_chose_it(monkeypatch):
    saved = {"notif_time_daily_words": "08:00"}
    _settings(monkeypatch, saved)
    context = SimpleNamespace(job=SimpleNamespace(data="08:00"))

    assert bot._slot_due(context, "42", "daily_words")
    assert not bot._slot_due(SimpleNamespace(job=SimpleNamespace(data="11:00")), "42", "daily_words")
    assert bot._slot_due(SimpleNamespace(job=None), "42", "daily_words")  # ручной запуск — без фильтра


def test_every_time_option_has_a_scheduled_job(monkeypatch):
    monkeypatch.setattr(bot.config, "TELEGRAM_TOKEN", "123456:TESTTOKEN")
    names = {job.name for job in bot._build_application().job_queue.jobs()}

    for kind, (_default, options) in settings.NOTIF_TIMES.items():
        if kind == "trip_day":  # по местному времени поездки — одна задача каждые 15 минут
            assert "trip_day_repeating" in names
            continue
        for slot in options:
            assert f"{kind}_{slot.replace(':', '')}" in names


def test_notification_screen_toggles_and_picks_time(monkeypatch):
    saved = {}
    _settings(monkeypatch, saved)
    shown = []

    async def show(_bot, _cid, msg, reply_markup=None, query=None):
        shown.append((msg.text, [[b.text for b in row] for row in reply_markup.inline_keyboard],
                      [[b.callback_data for b in row] for row in reply_markup.inline_keyboard]))

    monkeypatch.setattr(settings.rich_delivery, "show", show)

    asyncio.run(settings.handle_callback(None, "42", "set_notiftime_evening_weather_1800"))
    asyncio.run(settings.handle_callback(None, "42", "set_notiftgl_evening_weather"))

    assert saved["notif_time_evening_weather"] == "18:00" and saved["notif_evening_weather"] is True
    text, labels, callbacks = shown[-1]
    assert text.startswith("🔔 Погода на завтра · 18:00")
    assert labels[:4] == [["✅ Присылать"], ["✅ 18:00"], ["□ 20:00"], ["□ 21:00"]]
    assert callbacks[-1] == ["set_notif", "m_menu"]


def test_next_notification_line(monkeypatch):
    from datetime import datetime
    saved = {"notif_evening_weather": True, "notif_daily_words": False,
             "notif_weekend_events": True}
    _settings(monkeypatch, saved)
    thursday_evening = datetime(2026, 10, 8, 20, 0, tzinfo=settings.config.TZ)
    thursday_noon = datetime(2026, 10, 8, 12, 0, tzinfo=settings.config.TZ)

    assert settings.next_notification("42", thursday_noon) == "Следующая: Погода на завтра сегодня в 20:00"
    assert settings.next_notification("42", thursday_evening) == "Следующая: Концерты недели завтра в 10:00"
    saved.update(notif_evening_weather=False, notif_weekend_events=False)
    assert settings.next_notification("42", thursday_noon) == ""


def test_settings_summary_line(monkeypatch):
    saved = {"notif_evening_weather": True, "notif_daily_words": True, "notif_weather_warn": False,
             "notif_ns_disruptions": False, "notif_weekend_events": False,
             "cuisines": ["italian", "georgian"]}
    _settings(monkeypatch, saved)

    # + «Праздники и концерты» и «Праздники и мероприятия» по умолчанию
    assert settings.settings_summary("42").startswith("включено 4 · следующая: ")
    saved["notif_holidays"] = False
    assert settings.settings_summary("42").startswith("включено 3 · следующая: ")


def test_myday_blocks_toggle_and_reset_day_cache(monkeypatch):
    import myday
    saved, resets = {}, []
    _settings(monkeypatch, saved)
    monkeypatch.setattr(myday, "reset_day_cache", resets.append)

    async def show(*_a, **_k):
        return None

    monkeypatch.setattr(settings.rich_delivery, "show", show)

    asyncio.run(settings.handle_callback(None, "42", "set_mydaytgl_quote"))
    assert not settings.myday_block_on("42", "quote") and settings.myday_block_on("42", "weather")
    asyncio.run(settings.handle_callback(None, "42", "set_mydaytgl_quote"))
    assert settings.myday_block_on("42", "quote") and resets == ["42", "42"]


def test_myday_skips_disabled_blocks_without_preparing_them(monkeypatch):
    import myday
    import weather

    monkeypatch.setattr(settings, "myday_block_on", lambda _cid, key: key not in ("weather", "quote"))
    monkeypatch.setattr(myday.store, "get_settings", lambda _cid: {"city": "Alkmaar", "lat": 1, "lon": 2})
    monkeypatch.setattr(weather, "fetch_weather", lambda *_a: (_ for _ in ()).throw(AssertionError("weather off")))
    monkeypatch.setattr(myday, "_daily_literary_quote", lambda _cid: (_ for _ in ()).throw(AssertionError("off")))
    monkeypatch.setattr(myday.store, "learning_is_enabled", lambda _cid: False)
    monkeypatch.setattr(myday, "_movie_rebus_of_day", lambda _day: {})
    monkeypatch.setattr(myday, "_rail_works", lambda _cid: [])
    monkeypatch.setattr(myday, "_holidays", lambda _s, _cid=None: [])
    import wardrobe
    monkeypatch.setattr(wardrobe, "get_cached_outfit_summary", lambda _cid: {"items": [], "emoji": ""})

    text, _entities = myday._build_day_text("42")

    assert "Погода" not in text and "©" not in text


def test_holidays_eve_sends_tomorrow_holiday_and_local_events_only_when_any(monkeypatch):
    import asyncio
    import local_events
    import public_holidays
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(settings.store, "get_settings", lambda _cid: {"city": "Амстердам", "cc": "NL"})
    import leisure_concerts
    monkeypatch.setattr(leisure_concerts, "favorite_concerts_on", lambda _cid, _day: [])
    monkeypatch.setattr(public_holidays, "holiday_lines", lambda _cc, _day: ["Сегодня: A", "Завтра: Koningsdag — День короля"])
    monkeypatch.setattr(local_events, "starting_on", lambda _cid, _day, fetch=True: ["Amsterdam Pride — прайд-фестиваль"])
    asyncio.run(settings._send_holidays_eve(Bot(), "42"))
    assert sent[0]["text"] == "🎉 Завтра · Амстердам\n\nKoningsdag — День короля\nAmsterdam Pride — прайд-фестиваль"

    sent.clear()
    monkeypatch.setattr(public_holidays, "holiday_lines", lambda *_a: [])
    monkeypatch.setattr(local_events, "starting_on", lambda *_a, **_k: [])
    asyncio.run(settings._send_holidays_eve(Bot(), "42"))
    assert sent == []  # нечего сказать — без сообщения


def test_concert_eve_comes_for_favorite_artist_tomorrow_even_without_holidays(monkeypatch):
    import asyncio
    from datetime import date
    import leisure_concerts
    import local_events
    import public_holidays
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    tomorrow = date(2026, 11, 4)
    event = {"name": "Muse: Will of the People Tour", "url": "https://tickets.example/muse",
             "dates": {"start": {"localDate": tomorrow.isoformat(), "localTime": "20:00:00"}},
             "classifications": [{"genre": {"name": "Rock"}}],
             "priceRanges": [{"min": 65, "currency": "EUR"}],
             "_embedded": {"venues": [{"name": "Ziggo Dome", "address": {"line1": "De Passage 100"},
                                       "city": {"name": "Amsterdam"}}]}}
    later = {**event, "name": "Placebo", "dates": {"start": {"localDate": "2026-11-09"}}}
    monkeypatch.setattr(leisure_concerts.store, "get_settings", lambda _cid: {"city": "Alkmaar", "cc": "NL"})
    monkeypatch.setattr(leisure_concerts, "_concerts_cache_get", lambda _cid, _cc: [event, later])
    assert [c["artist"] for c in leisure_concerts.favorite_concerts_on("42", tomorrow)] == ["Muse"]

    monkeypatch.setattr(settings.store, "get_settings", lambda _cid: {"city": "Alkmaar", "cc": "NL"})
    monkeypatch.setattr(public_holidays, "holiday_lines", lambda *_a: [])
    monkeypatch.setattr(local_events, "starting_on", lambda *_a, **_k: [])
    async def notes(names):
        return {leisure_concerts._concert_identity_text("Muse"): {"about": "Стадионный рок с оркестром.", "fame": 9}}
    monkeypatch.setattr(leisure_concerts, "artist_notes", notes)
    asyncio.run(settings._send_concerts_eve(Bot(), "42", tomorrow))
    text = sent[0]["text"]
    assert text.startswith("🎫 Завтра концерт · среда, 4 ноября\n\nMuse — Стадионный рок с оркестром.\n"
                           "• Начало: 20:00\n• Где: Ziggo Dome, De Passage 100, Amsterdam\n")
    assert "• Билеты: от 65 EUR" in text and "• Тур: Muse: Will of the People Tour" in text
    assert [e.url for e in sent[0]["entities"] if e.type == "text_link"] == ["https://tickets.example/muse"]
    sent.clear()
    asyncio.run(settings._send_concerts_eve(Bot(), "42", date(2026, 11, 5)))
    assert sent == []


def test_trip_day_comes_at_8_local_time_once_a_day(monkeypatch):
    import asyncio
    from datetime import datetime as real_datetime
    from zoneinfo import ZoneInfo
    import vacation

    saved = {}
    _settings(monkeypatch, saved)
    monkeypatch.setattr(bot.access, "get_allowed_cids", lambda: ["42"])
    monkeypatch.setattr(settings, "notification_available", lambda _cid, _kind: True)
    monkeypatch.setattr(vacation, "local_tz", lambda _cid: ZoneInfo("Africa/Johannesburg"))
    sent = []

    async def send(_bot, cid, kind):
        sent.append((cid, kind))

    monkeypatch.setattr(settings, "send_scheduled_notification", send)
    clock = {"now": real_datetime(2026, 10, 28, 6, 0, tzinfo=ZoneInfo("UTC"))}  # 08:00 в Кейптауне, 07:00 в Амстердаме

    class FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].astimezone(tz) if tz else clock["now"]

    monkeypatch.setattr(bot, "datetime", FakeDatetime)
    context = type("C", (), {"bot": object()})()
    asyncio.run(bot.job_trip_day(context))
    asyncio.run(bot.job_trip_day(context))  # следующая проверка через 15 минут — без повтора
    assert sent == [("42", "trip_day")]
    clock["now"] = real_datetime(2026, 10, 29, 5, 0, tzinfo=ZoneInfo("UTC"))  # 07:00 в Кейптауне — рано
    asyncio.run(bot.job_trip_day(context))
    assert len(sent) == 1


def test_week_holidays_digest_has_holidays_with_about_and_city_events(monkeypatch):
    import asyncio
    from datetime import date, datetime
    import local_events
    import public_holidays

    holidays = public_holidays.week_holidays.__wrapped__ if hasattr(public_holidays.week_holidays, "__wrapped__") \
        else public_holidays.week_holidays
    monkeypatch.setattr(public_holidays, "holidays_between", lambda *_a: [])
    found = holidays("NL", date(2026, 11, 9), date(2026, 11, 15))
    assert found == [{"date": "2026-11-11", "name": "Sint-Maarten",
                      "about": "дети с фонариками ходят по домам, поют и получают сладости"}]
    assert holidays("DE", date(2026, 11, 9), date(2026, 11, 15)) == []  # традиции — только для Нидерландов
    assert public_holidays._nth_weekday(2026, 9, 1, 3) == date(2026, 9, 15)  # Prinsjesdag — 3-й вторник сентября

    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    class Monday(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 9, 10, tzinfo=tz)

    import datetime as dt_module
    monkeypatch.setattr(dt_module, "datetime", Monday)
    monkeypatch.setattr(settings.store, "get_settings", lambda _cid: {"city": "Alkmaar", "cc": "NL"})
    monkeypatch.setattr(local_events, "week_events", lambda _cid, _s, _e: [
        {"name": "Sinterklaasintocht Alkmaar", "about": "прибытие Синтерклааса", "start": "2026-11-14",
         "end": "2026-11-14", "url": "https://inalkmaar.nl/sint"},
        {"name": "Filmfestival Alkmaar", "about": "", "start": "2026-11-03", "end": "2026-11-10", "url": ""}])
    asyncio.run(settings.send_week_holidays(Bot(), "42"))
    assert sent[0]["text"] == (
        "🎉 Праздники и мероприятия · Alkmaar · 9–15 ноября\n\n"
        "Праздники:\n• 11 ноября — Sint-Maarten: дети с фонариками ходят по домам, поют и получают сладости.\n\n"
        "В городе:\n• 14 ноября — Sinterklaasintocht Alkmaar — прибытие Синтерклааса\n"
        "• 3–10 ноября — Filmfestival Alkmaar")
    sent.clear()
    monkeypatch.setattr(local_events, "week_events", lambda *_a: [])
    monkeypatch.setattr(public_holidays, "week_holidays", lambda *_a: [])
    asyncio.run(settings.send_week_holidays(Bot(), "42"))
    assert sent == []  # пустая неделя — без сообщения


def test_week_events_merge_city_events_and_home_afisha(monkeypatch):
    from datetime import date
    import local_events
    fest = {"name": "Popronde Alkmaar", "about": "", "start": "2026-11-13", "end": "2026-11-13", "url": ""}
    later = {**fest, "name": "Winterfair", "start": "2026-11-28", "end": "2026-11-28"}
    monkeypatch.setattr(local_events, "events", lambda _cid, fetch=True: [fest, later])
    calls = []
    monkeypatch.setattr(local_events, "trip_afisha", lambda _cid, fetch=True, home=False: calls.append(home) or [fest])
    found = local_events.week_events("42", date(2026, 11, 9), date(2026, 11, 15))
    assert [event["name"] for event in found] == ["Popronde Alkmaar"] and calls == [True]
