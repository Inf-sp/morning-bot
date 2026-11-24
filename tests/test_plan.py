from datetime import date

import plan
from ui import myday as myday_ui


def test_plan_has_weather_and_everything_for_tomorrow():
    msg = myday_ui.tomorrow_plan({
        "day": date(2026, 11, 25), "city": "Alkmaar", "cc": "NL",
        "weather": [{"title": "❄️ Утром", "temp": "до -1°C", "rain": "без дождя", "wind": "ветер 4 м/с"},
                    {"title": "🌤️ Днём", "temp": "до +3°C", "rain": "без дождя", "wind": "ветер 4–5 м/с"}],
        "official": ["Код жёлтый KNMI — ветер, завтра с 10:00 до 18:00"],
        "holidays": [{"date": "2026-11-25", "name": "Sinterklaasavond", "about": "вечер подарков"}],
        "events": [{"kind": "city", "name": "Ice rink on Waagplein", "date": "2026-11-20", "end": "2027-01-03",
                    "place": "Waagplein", "free": True}],
        "premieres": [{"kind": "premiere", "premiere": "movie", "name": "Дюна", "date": "2026-11-25",
                       "meta": ["фантастика"]}],
        "works": []})
    assert msg.text == (
        "🗓️ План на завтра · ср, 25 ноября\n\n"
        "Город: Alkmaar\n\n"
        "Погода:\n❄️ Утром: до -1°C · без дождя · ветер 4 м/с\n🌤️ Днём: до +3°C · без дождя · ветер 4–5 м/с\n\n"
        "⚠️ Предупреждение: Код жёлтый KNMI — ветер, завтра с 10:00 до 18:00.\n"
        "🎉 Праздник: Sinterklaasavond — вечер подарков.\n"
        "🏙️ Городское событие: Ice rink on Waagplein · Waagplein · бесплатно\n"
        "🎬 Премьера: Дюна · фантастика")


def test_empty_tomorrow_is_honest():
    text = myday_ui.tomorrow_plan({"day": date(2026, 11, 25), "city": "Alkmaar", "weather": []}).text
    assert text.endswith("Завтра без особых событий — день можно планировать свободно.")


def test_collect_takes_tomorrows_events_and_premieres(monkeypatch):
    import leisure_events
    from datetime import datetime
    import config
    monkeypatch.setattr(plan.store, "get_settings", lambda _cid: {"city": "Alkmaar", "cc": "DE"})
    monkeypatch.setattr(plan, "_weather_periods", lambda *_a: [])
    monkeypatch.setattr(leisure_events, "feed", lambda _cid: [
        {"kind": "city", "name": "Rink", "date": "2026-11-20", "end": "2027-01-03"},
        {"kind": "concert", "name": "Today", "date": "2026-11-24", "end": "2026-11-24"},
        {"kind": "premiere", "name": "Дюна", "date": "2026-11-25", "end": "2026-11-25"}])
    found = plan.collect("42", now=datetime(2026, 11, 24, 20, tzinfo=config.TZ))
    assert [card["name"] for card in found["events"]] == ["Rink"]  # сегодняшний концерт — не в плане на завтра
    assert [card["name"] for card in found["premieres"]] == ["Дюна"]
