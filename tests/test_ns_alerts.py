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
        "start": "", "until": "2026-10-08T18:30:00+0200", "extra": "tot 30 minuten extra reistijd",
        "sections": [], "stations": [], "level": "", "extra_min": "", "alternative": "", "advice": "",
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

    assert text == ("🚆 Сбой NS · Alkmaar\n\nAlkmaar – Amsterdam Centraal\n\nМеньше поездов\n\n"
                    "Причина: Неисправный поезд.\n\nОжидается до 18:30 · до 30 минут дольше в пути")


def test_alert_card_skips_section_and_cause_already_in_the_text():
    text = transport_ui.ns_alert("Alkmaar", {
        "title": "Alkmaar - Hoorn", "until": "2026-10-12T16:00:00+0200",
        "situation": "Из-за повреждённого железнодорожного моста между Alkmaar и Heerhugowaard возникло нарушение.",
        "cause": "повреждённый железнодорожный мост"}).text

    assert text == ("🚆 Сбой NS · Alkmaar\n\nИз-за повреждённого железнодорожного моста между Alkmaar и "
                    "Heerhugowaard возникло нарушение.\n\nОжидается до 16:00")


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
    assert [b.text for b in bot.sent[0]["reply_markup"].inline_keyboard[0]] == ["🎚️ Настроить уведомления", "#️⃣ Главная"]

    feed["items"] = []
    asyncio.run(ns_alerts.check_user(bot, "42"))
    assert bot.sent[-1]["text"].startswith(
        "✅ Движение восстановлено\n\nAlkmaar – Amsterdam Centraal\n\nПоезда снова ходят.")
    assert "\n\nПричина: Defecte trein." in bot.sent[-1]["text"]
    assert [b.text for b in bot.sent[-1]["reply_markup"].inline_keyboard[0]] == ["🎚️ Настроить уведомления", "#️⃣ Главная"]
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


def test_one_incident_on_two_routes_is_sent_and_restored_once(monkeypatch):
    _user(monkeypatch)
    first = {"id": "a", "type": "DISRUPTION", "title": "Amsterdam - Alkmaar", "cause": "defecte trein",
             "situation": "tussen Zaandam en Alkmaar rijden er geen Intercity's", "until": "2026-10-10T18:30:00+0200",
             "extra": ""}
    second = {**first, "id": "b", "title": "Haarlem - Alkmaar",
              "situation": "tussen Uitgeest en Alkmaar rijden er minder treinen"}
    feed = {"items": [first, second]}
    monkeypatch.setattr(ns_alerts.ns_api, "active_disruptions", lambda _code: feed["items"])
    bot = Bot()

    asyncio.run(ns_alerts.check_user(bot, "42"))
    asyncio.run(ns_alerts.check_user(bot, "42"))
    # Станция уже есть в тексте NS («tussen Zaandam en Alkmaar») — участок в начале не повторяется.
    assert len(bot.sent) == 1 and bot.sent[0]["text"].split("\n")[2].startswith("tussen Zaandam")

    feed["items"] = [second]  # один маршрут уже восстановлен, инцидент ещё идёт
    asyncio.run(ns_alerts.check_user(bot, "42"))
    assert len(bot.sent) == 1

    feed["items"] = []
    asyncio.run(ns_alerts.check_user(bot, "42"))
    assert len(bot.sent) == 2 and bot.sent[-1]["text"].startswith("✅ Движение восстановлено")


def test_translation_without_cyrillic_is_rejected(monkeypatch):
    captured = {}

    def fake_llm_json(*_args, result_validator=None, **_kwargs):
        captured["validator"] = result_validator
        return {"cause": "неисправный поезд"}

    monkeypatch.setattr(ns_alerts.ai, "llm_json", fake_llm_json)
    item = ns_alerts._translate({"cause": "defecte trein"})
    assert item["cause"] == "неисправный поезд"
    assert captured["validator"]({"cause": "неисправный поезд"})
    assert not captured["validator"]({"cause": "defecte trein"})


def test_restored_card_has_blocks_with_bold_route_and_cause():
    message = transport_ui.ns_restored("Alkmaar - Den Helder", since="2026-10-14T08:29", until="2026-10-14T11:47",
                                       cause="неисправный железнодорожный мост")
    bold = [message.text.encode("utf-16-le")[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le")
            for e in message.entities if e.type == "bold"]

    assert message.text == ("✅ Движение восстановлено\n\nAlkmaar - Den Helder\n\nПоезда снова ходят.\n\n"
                            "Причина: Неисправный железнодорожный мост.\n\nСбой длился с 08:29 до 11:47")
    assert bold == ["✅ Движение восстановлено", "Alkmaar - Den Helder", "Причина:"]


PRACTICAL = {
    "id": "w1", "type": "MAINTENANCE", "title": "Alkmaar - Hoorn",
    "timespans": [{"start": "2026-10-10T00:00:00+0200", "end": "2026-10-12T23:59:00+0200",
                   "alternativeTransport": {"label": "Bussen tussen Alkmaar en Hoorn"},
                   "advices": ["Reis via Zaandam"],
                   "additionalTravelTime": {"minimumDurationInMinutes": 15, "maximumDurationInMinutes": 30}}],
    "publicationSections": [
        {"section": {"stations": [{"name": "Alkmaar"}, {"name": "Heerhugowaard"}, {"name": "Hoorn"}]},
         "consequence": {"level": "LESS_TRAINS"}},
        {"section": {"stations": [{"name": "Alkmaar"}, {"name": "Hoorn"}]},
         "consequence": {"level": "NO_OR_MUCH_LESS_TRAINS"}},
    ],
}


def test_practical_info_sections_level_alternative_extra_and_advice():
    info = ns_api.practical(PRACTICAL)
    assert info == {"sections": ["Alkmaar – Hoorn"], "stations": ["Alkmaar", "Heerhugowaard", "Hoorn"],
                    "level": "поездов нет или почти нет", "extra_min": "+15–30 мин",
                    "alternative": "Bussen tussen Alkmaar en Hoorn", "advice": "Reis via Zaandam"}
    works = {**ns_api.parse_works(PRACTICAL, _date(2026, 10, 11)),
             "alternative": "автобусы между Alkmaar и Hoorn", "advice": "езжай через Zaandam"}
    text = myday_ui.day_summary("Вс", "Alkmaar", rail_works=[works]).text
    assert "🚧 Работы на ЖД: от Alkmaar до Hoorn · с 10 по 12 октября · Поездов нет или почти нет." in text
    alert = transport_ui.ns_alert("Alkmaar", {**ns_api.parse_disruption({**RAW, **{
        k: PRACTICAL[k] for k in ("publicationSections",)}}), "situation": "меньше поездов",
        "cause": "неисправный поезд", "alternative": "автобусы", "advice": "езжай через Zaandam"}).text
    assert "• Затронуты: Alkmaar – Hoorn.\n• Вместо поездов: автобусы.\n• Совет: езжай через Zaandam." in alert


def test_foreign_or_ending_disruption_is_not_sent_and_dutch_is_never_shown(monkeypatch):
    profile = {}
    monkeypatch.setattr(ns_alerts.store, "get_settings", lambda _cid: {"city": "Alkmaar", "cc": "NL"})
    monkeypatch.setattr(ns_alerts.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(ns_alerts.store, "mutate_profile", lambda _cid, fn: fn(profile))
    monkeypatch.setattr(ns_alerts.ns_api, "station_codes", lambda _city: ["AMR"])
    foreign = {"id": "f1", "type": "DISRUPTION", "title": "Amsterdam Centraal - Hoofddorp", "cause": "defect spoor",
               "situation": "Er rijden minder treinen", "start": "", "until": "", "extra": "", "sections": []}
    ending = {**foreign, "id": "e1", "title": "Alkmaar - Amsterdam Centraal",
              "situation": "Tussen Alkmaar en Amsterdam Centraal zijn de beperkingen voorbij."}
    monkeypatch.setattr(ns_alerts.ns_api, "active_disruptions", lambda _code: [foreign, ending])
    bot = Bot()
    asyncio.run(ns_alerts.check_user(bot, "42"))
    assert bot.sent == [] and not (profile.get("ns_alerts") or {}).get("active")

    def no_ai(*_a, **_k):
        raise RuntimeError("down")
    monkeypatch.setattr(ns_alerts.ai, "llm_json", no_ai)
    item = ns_alerts._translate({"cause": "Defect spoor", "situation": "Er rijden minder treinen tussen A en B",
                                 "extra": "De vertraging neemt af.", "advice": "Plan je reis opnieuw"})
    assert (item["cause"], item["situation"], item["extra"], item["advice"]) == (
        "неисправность пути", "Сбой в движении поездов", "задержки уменьшаются", "")


def test_city_in_the_middle_of_a_section_counts_and_repair_in_progress_is_not_ending():
    raw = {**RAW, "title": "Amsterdam Centraal - Den Helder", "publicationSections": [
        {"section": {"stations": [{"name": "Amsterdam Centraal"}, {"name": "Alkmaar"}, {"name": "Den Helder"}]},
         "consequence": {"level": "LESS_TRAINS"}}]}
    item = ns_api.parse_disruption(raw)
    assert item["sections"] == ["Amsterdam Centraal – Den Helder"]
    assert ns_alerts.concerns_city(item, "Alkmaar")  # Alkmaar — промежуточная станция
    assert not ns_alerts.concerns_city({"title": "Amsterdam Centraal - Hoofddorp", "stations": []}, "Alkmaar")
    assert not ns_alerts.is_ending({"situation": "De storing wordt hersteld."})  # ремонтируют — сбой идёт
    assert ns_alerts.is_ending({"situation": "De storing is opgelost."})


def test_one_untranslated_field_does_not_reject_the_whole_translation(monkeypatch):
    captured = {}

    def fake_llm_json(*_args, result_validator=None, **_kwargs):
        captured["validator"] = result_validator
        return {"cause": "неисправность пути", "situation": "меньше поездов", "advice": "Via Zaandam"}

    monkeypatch.setattr(ns_alerts.ai, "llm_json", fake_llm_json)
    item = ns_alerts._translate({"cause": "defect spoor", "situation": "minder treinen", "advice": "Via Zaandam"})
    assert captured["validator"]({"cause": "неисправность пути", "situation": "minder treinen", "advice": "Via Zaandam"})
    assert not captured["validator"]({"cause": "defect spoor", "situation": "minder treinen"})
    assert (item["cause"], item["situation"], item["advice"]) == ("неисправность пути", "меньше поездов", "")


def test_last_raw_ns_answer_is_kept_with_our_parse_for_admin(monkeypatch):
    saved = {}
    import store
    monkeypatch.setattr(store, "mutate_kv", lambda _key, fn: saved.update(fn(dict(saved))[0]))
    monkeypatch.setattr(store, "_load", lambda _key: saved)
    ns_api._remember_raw("/disruptions/v3/station/AMR", {"payload": []})
    assert saved == {}  # пустой ответ не затирает последний полезный
    ns_api._remember_raw("/disruptions/v3/station/AMR", {"payload": [RAW, PRACTICAL]})
    data = ns_api.last_raw()
    assert data["path"].endswith("/AMR") and [item["raw"]["id"] for item in data["items"]] == ["7001", "w1"]
    assert data["items"][0]["parsed"]["cause"] == "defecte trein"  # рядом — как понял бот
