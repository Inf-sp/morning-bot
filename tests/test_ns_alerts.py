import asyncio
import os
from datetime import datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import ns_alerts
import ns_api
from ui import transport as transport_ui

RAW = {
    "id": "7001", "type": "DISRUPTION", "isActive": True, "title": "Alkmaar – Amsterdam Centraal",
    "timespans": [{"cause": {"label": "defecte trein"}, "situation": {"label": "minder treinen"},
                   "end": "2026-10-08T18:30:00+0200"}],
    "summaryAdditionalTravelTime": {"label": "tot 30 minuten extra reistijd"},
}


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


def test_parse_keeps_disruptions_and_calamities_but_not_maintenance():
    assert ns_api.parse_disruption(RAW) == {
        "id": "7001", "type": "DISRUPTION", "title": "Alkmaar – Amsterdam Centraal",
        "cause": "defecte trein", "situation": "minder treinen",
        "until": "2026-10-08T18:30:00+0200", "extra": "tot 30 minuten extra reistijd",
    }
    calamity = ns_api.parse_disruption({"id": "c1", "type": "CALAMITY", "title": "Brand", "description": "Geen treinen"})
    assert calamity["situation"] == "Geen treinen"
    assert ns_api.parse_disruption({**RAW, "type": "MAINTENANCE"}) is None
    assert ns_api.parse_disruption({**RAW, "isActive": False}) is None


def test_all_city_stations_are_watched(monkeypatch):
    monkeypatch.setattr(ns_api.util, "ttl_get", lambda *_a: None)
    monkeypatch.setattr(ns_api.util, "ttl_set", lambda *_a: None)
    monkeypatch.setattr(ns_api, "_get", lambda *_a, **_k: {"payload": [
        {"id": {"code": "AMR"}, "names": {"long": "Alkmaar", "medium": "Alkmaar"}},
        {"id": {"code": "AMRN"}, "names": {"long": "Alkmaar Noord"}},
        {"id": {"code": "HLM"}, "names": {"long": "Haarlem"}},
    ]})

    assert ns_api.station_codes("Alkmaar") == ["AMR", "AMRN"]


def test_alert_card_is_russian_with_section_cause_and_time():
    text = transport_ui.ns_alert("Alkmaar", {**ns_api.parse_disruption(RAW),
                                             "cause": "неисправный поезд", "situation": "меньше поездов",
                                             "extra": "до 30 минут дольше в пути"}).text

    assert text == ("🚆 Сбой NS · Alkmaar\n\nAlkmaar – Amsterdam Centraal: меньше поездов\n"
                    "Причина: неисправный поезд\nОжидается до 18:30 · до 30 минут дольше в пути")


def _user(monkeypatch, cc="NL"):
    profile = {}
    monkeypatch.setattr(ns_alerts.store, "get_settings", lambda _cid: {"city": "Alkmaar", "cc": cc})
    monkeypatch.setattr(ns_alerts.store, "get_profile", lambda _cid: dict(profile))

    def mutate(_cid, change):
        updated, _ = change(dict(profile))
        profile.clear()
        profile.update(updated)

    monkeypatch.setattr(ns_alerts.store, "mutate_profile", mutate)
    monkeypatch.setattr(ns_alerts.ns_api, "station_codes", lambda _city: ["AMR", "AMRN"])
    monkeypatch.setattr(ns_alerts, "_translate", lambda item: item)
    return profile


def test_each_disruption_is_sent_once_then_restoration(monkeypatch):
    profile = _user(monkeypatch)
    feed = {"items": [ns_api.parse_disruption(RAW)]}
    monkeypatch.setattr(ns_alerts.ns_api, "active_disruptions", lambda _code: feed["items"])
    bot = Bot()

    asyncio.run(ns_alerts.check_user(bot, "42"))
    asyncio.run(ns_alerts.check_user(bot, "42"))
    assert len(bot.sent) == 1 and bot.sent[0]["text"].startswith("🚆 Сбой NS · Alkmaar")
    assert [b.text for b in bot.sent[0]["reply_markup"].inline_keyboard[0]] == ["🎚️ Настроить", "#️⃣ Главная"]

    feed["items"] = []
    asyncio.run(ns_alerts.check_user(bot, "42"))
    assert bot.sent[-1]["text"] == "✅ Движение Alkmaar – Amsterdam Centraal восстановлено"
    assert profile["ns_alerts"] == {"active": {}}


def test_no_alerts_outside_netherlands_or_when_ns_is_down(monkeypatch):
    _user(monkeypatch, cc="DE")
    monkeypatch.setattr(ns_alerts.ns_api, "active_disruptions", lambda _code: [ns_api.parse_disruption(RAW)])
    bot = Bot()
    asyncio.run(ns_alerts.check_user(bot, "42"))
    assert bot.sent == []

    profile = _user(monkeypatch)
    monkeypatch.setattr(ns_alerts.ns_api, "active_disruptions", lambda _code: None)
    asyncio.run(ns_alerts.check_user(bot, "42"))
    assert bot.sent == [] and "ns_alerts" not in profile


def test_checks_run_only_from_six_to_twenty_three():
    assert not ns_alerts.is_active_time(datetime(2026, 10, 8, 5, 59))
    assert ns_alerts.is_active_time(datetime(2026, 10, 8, 6, 0))
    assert ns_alerts.is_active_time(datetime(2026, 10, 8, 22, 59))
    assert not ns_alerts.is_active_time(datetime(2026, 10, 8, 23, 0))


from datetime import date as _date

from ui import myday as myday_ui

WORKS = {"id": "w1", "type": "MAINTENANCE", "title": "Alkmaar – Den Helder",
         "timespans": [{"start": "2026-10-10T01:00:00+0200", "end": "2026-10-12T05:00:00+0200"}]}


def test_planned_works_only_on_their_dates():
    assert ns_api.parse_works(WORKS, _date(2026, 10, 9)) is None
    assert ns_api.parse_works(WORKS, _date(2026, 10, 10))["end"] == _date(2026, 10, 12)
    assert ns_api.parse_works(WORKS, _date(2026, 10, 13)) is None
    assert ns_api.parse_works(RAW, _date(2026, 10, 10)) is None  # сбой — не плановые работы


def test_day_summary_shows_works_period_under_weather():
    msg = myday_ui.day_summary("Сб, 10 окт", "Alkmaar", weather_line="до +15°C", rail_works=[
        {"title": "Alkmaar – Den Helder", "start": _date(2026, 10, 10), "end": _date(2026, 10, 12)},
        {"title": "Alkmaar – Uitgeest", "start": _date(2026, 9, 30), "end": _date(2026, 10, 2)},
    ])

    assert "Погода: до +15°C\n\n🚧 Работы на ЖД: от Alkmaar до Den Helder · с 10 по 12 октября" in msg.text
    assert "🚧 Работы на ЖД: от Alkmaar до Uitgeest · с 30 сентября по 2 октября" in msg.text
    one_day = myday_ui.day_summary("Сб", "Alkmaar", rail_works=[
        {"title": "Station Alkmaar", "start": _date(2026, 10, 10), "end": _date(2026, 10, 10)}])
    assert "🚧 Работы на ЖД: Station Alkmaar · 10 октября" in one_day.text


def test_todays_works_cover_all_city_stations_once(monkeypatch):
    monkeypatch.setattr(ns_alerts.config, "NS_API_KEY", "key")
    monkeypatch.setattr(ns_alerts.store, "get_settings", lambda _cid: {"city": "Alkmaar", "cc": "NL"})
    monkeypatch.setattr(ns_alerts.ns_api, "station_codes", lambda _city: ["AMR", "AMRN"])
    item = ns_api.parse_works(WORKS, _date(2026, 10, 11))
    monkeypatch.setattr(ns_alerts.ns_api, "planned_works", lambda _code, _today: [item])

    assert ns_alerts.todays_works("42", today=_date(2026, 10, 11)) == [item]

    monkeypatch.setattr(ns_alerts.store, "get_settings", lambda _cid: {"city": "Berlin", "cc": "DE"})
    assert ns_alerts.todays_works("42", today=_date(2026, 10, 11)) == []


def test_ns_failure_gives_no_works_line(monkeypatch):
    monkeypatch.setattr(ns_api.util, "ttl_get", lambda *_a: None)
    monkeypatch.setattr(ns_api, "_get", lambda *_a, **_k: None)

    assert ns_api.planned_works("AMR", _date(2026, 10, 11)) == []


def test_section_title_has_no_trailing_dot():
    works = ns_api.parse_works({**WORKS, "title": "Alkmaar - Hoorn."}, _date(2026, 10, 11))
    alert = ns_api.parse_disruption({**RAW, "title": "Alkmaar - Hoorn."})

    assert works["title"] == alert["title"] == "Alkmaar - Hoorn"


def test_ns_toggle_is_shown_only_for_the_netherlands(monkeypatch):
    import asyncio
    import settings

    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(settings, "notif_on", lambda *_a: True)
    for cc in ("NL", "DE"):
        monkeypatch.setattr(settings.store, "get_settings", lambda _cid, cc=cc: {"cc": cc, "city": "Alkmaar"})
        asyncio.run(settings.send_notif(Bot(), "42"))

    nl, de = ([b.text for row in message["reply_markup"].inline_keyboard for b in row] for message in sent)
    assert any("Поезда NS" in label for label in nl)
    assert not any("Поезда NS" in label for label in de)
