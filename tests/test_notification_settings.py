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
    saved = {"notif_time_daily_words": "08:00", "notif_time_news_digest": "03:00"}
    _settings(monkeypatch, saved)

    assert settings.notif_time("42", "daily_words") == "08:00"
    assert settings.notif_time("42", "news_digest") == "19:00"  # неизвестное значение — по умолчанию
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

    asyncio.run(settings.handle_callback(None, "42", "set_notiftime_news_digest_0800"))
    asyncio.run(settings.handle_callback(None, "42", "set_notiftgl_news_digest"))

    assert saved["notif_time_news_digest"] == "08:00" and saved["notif_news_digest"] is True
    text, labels, callbacks = shown[-1]
    assert text.startswith("🔔 Главные новости · 08:00")
    assert labels[:5] == [["✅ Присылать"], ["✅ 08:00"], ["□ 12:00"], ["□ 19:00"], ["□ 21:00"]]
    assert callbacks[-1] == ["set_notif", "m_menu"]


def test_next_notification_line(monkeypatch):
    from datetime import datetime
    saved = {"notif_news_digest": True, "notif_daily_words": False, "notif_evening_weather": False,
             "notif_weekend_events": True}
    _settings(monkeypatch, saved)
    thursday_evening = datetime(2026, 10, 8, 20, 0, tzinfo=settings.config.TZ)
    thursday_noon = datetime(2026, 10, 8, 12, 0, tzinfo=settings.config.TZ)

    assert settings.next_notification("42", thursday_noon) == "Следующая: Главные новости сегодня в 19:00"
    assert settings.next_notification("42", thursday_evening) == "Следующая: Концерты недели завтра в 10:00"
    saved.update(notif_news_digest=False, notif_weekend_events=False)
    assert settings.next_notification("42", thursday_noon) == ""


def test_settings_summary_line(monkeypatch):
    saved = {"notif_news_digest": True, "notif_daily_words": True, "notif_weather_warn": False,
             "notif_ns_disruptions": False, "notif_evening_weather": False, "notif_weekend_events": False,
             "cuisines": ["italian", "georgian"]}
    _settings(monkeypatch, saved)
    monkeypatch.setattr(settings.store, "load_wardrobe", lambda _cid: {"zones": {
        "Верх": {"Футболки": [{"id": "1"}, {"id": "2"}]}, "Обувь": {"Кеды": [{"id": "3"}]}}})

    assert settings.settings_summary("42") == "Уведомлений включено: 2 · Кухонь: 2 · Вещей в шкафу: 3"


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

    monkeypatch.setattr(settings, "myday_block_on", lambda _cid, key: key not in ("weather", "lifehack", "quote"))
    monkeypatch.setattr(myday.store, "get_settings", lambda _cid: {"city": "Alkmaar", "lat": 1, "lon": 2})
    monkeypatch.setattr(weather, "fetch_weather", lambda *_a: (_ for _ in ()).throw(AssertionError("weather off")))
    monkeypatch.setattr(myday, "daily_lifehack", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("off")))
    monkeypatch.setattr(myday, "_daily_literary_quote", lambda _cid: (_ for _ in ()).throw(AssertionError("off")))
    monkeypatch.setattr(myday.store, "learning_is_enabled", lambda _cid: False)
    monkeypatch.setattr(myday, "_movie_rebus_of_day", lambda _day: {})
    monkeypatch.setattr(myday, "_rail_works", lambda _cid: [])
    monkeypatch.setattr(myday, "_holidays", lambda _s: [])
    import wardrobe
    monkeypatch.setattr(wardrobe, "get_cached_outfit_summary", lambda _cid: {"items": [], "emoji": ""})

    text, _entities = myday._build_day_text("42")

    assert "Погода" not in text and "Лайфхак" not in text and "©" not in text


def test_news_topics_and_count_from_settings(monkeypatch):
    import news_digest
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 8, 19, tzinfo=settings.config.TZ)

    def item(title, hours=1, **extra):
        return {"source": "NOS", "title": title, "link": f"https://x/{abs(hash(title))}",
                "published": now - timedelta(hours=hours), "summary": "", **extra}

    items = [item("Ajax wint van PSV"), item("Nieuwe film van Verhoeven"), item("Kabinet valt"),
             item("Planeet ontdekt", science=True), item("Fossiel gevonden", science=True),
             item("Robot leert lopen", science=True)]

    titles = [i["title"] for i in news_digest.select(items, "Alkmaar", now=now, topics_off=("sport", "politics"), limit=3)]

    assert len(titles) == 3
    assert "Ajax wint van PSV" not in titles and "Kabinet valt" not in titles
    assert news_digest.topic(items[1]) == "culture" and news_digest.topic(items[0]) == "sport"

    saved = {}
    _settings(monkeypatch, saved)
    assert settings.news_topics_off("42") == ["politics"] and settings.news_count("42") == 5
