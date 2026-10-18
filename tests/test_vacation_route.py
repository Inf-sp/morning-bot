"""Отпуск и чемодан из нескольких городов: разбор маршрута, переезд, погода и образы по городам, вид чемодана."""
import asyncio
from datetime import date, datetime, timedelta

import config
import settings
import store
import vacation
import wardrobe_suitcase
from test_vacation import HOME, Bot, _env
from ui import wardrobe as wardrobe_ui
from util import _MONTHS

TODAY = date(2026, 10, 17)
CAPE_LEG = {"city": "Кейптаун", "cc": "ZA", "country": "ЮАР", "lat": -33.9, "lon": 18.4}
WINDHOEK_LEG = {"city": "Виндхук", "cc": "NA", "country": "Намибия", "lat": -22.6, "lon": 17.1}


def _day(value) -> str:
    return f"{value.day} {_MONTHS[value.month - 1]}"


def _names(monkeypatch):
    import countries
    monkeypatch.setattr(countries, "name_ru", lambda cc: {"ZA": "ЮАР", "NA": "Намибия"}.get(str(cc).upper(), ""))


def test_route_dates_put_the_moving_day_in_the_next_city():
    route = vacation.parse_route("Кейптаун 19–27 октября, далее Йоханнесбург до 3 ноября", TODAY)

    assert [(leg["city"], leg["start"], leg["end"]) for leg in route["legs"]] == [
        ("Кейптаун", date(2026, 10, 19), date(2026, 10, 26)),  # 27-го — уже Йоханнесбург
        ("Йоханнесбург", date(2026, 10, 27), date(2026, 11, 3))]  # чемодан: последний день поездки включается
    assert vacation.parse_route("Кейптаун 19–27 октября, Йоханнесбург до 3 ноября", TODAY) == route  # запятая
    assert vacation.parse_route("Кейптаун, 20–27 октября", TODAY) is None  # один город — обычный разбор
    assert vacation.parse_route("Кейптаун до 27 октября, далее Йоханнесбург", TODAY) is None  # без даты — не понял
    assert vacation.looks_like_route("Кейптаун до 27 октября, далее Йоханнесбург", TODAY)  # маршрут, но без даты
    assert not vacation.looks_like_route("Cape Town до 20 октября, с друзьями", TODAY)  # один город и пояснение

    trip = vacation.parse_route("Кейптаун до 27 октября → Йоханнесбург до 30 октября → Виндхук до 3 ноября", TODAY,
                                until_mode=True)
    assert [(leg["city"], leg["start"], leg["end"]) for leg in trip["legs"]] == [
        ("Кейптаун", TODAY, date(2026, 10, 26)), ("Йоханнесбург", date(2026, 10, 27), date(2026, 10, 29)),
        ("Виндхук", date(2026, 10, 30), date(2026, 11, 2))]
    assert trip["until"] == date(2026, 11, 3)  # отпуск: 3 ноября — уже дома


def test_vacation_route_starts_in_the_first_city_and_moves_on_the_moving_day(monkeypatch):
    import access
    import myday
    saved, places = _env(monkeypatch)
    _names(monkeypatch)
    coords = {"Кейптаун": CAPE_LEG, "Виндхук": WINDHOEK_LEG}

    async def geocode_legs(legs):
        return [{**leg, **coords[leg["city"]]} for leg in legs], ""

    async def plany(*_a, **_k):
        return None

    monkeypatch.setattr(vacation, "geocode_legs", geocode_legs)
    monkeypatch.setattr(vacation, "guide", lambda _cid: {})
    monkeypatch.setattr(myday, "send_plany", plany)
    monkeypatch.setattr(access, "get_allowed_cids", lambda: ["42"])
    today = datetime.now(config.TZ).date()
    moving, back = today + timedelta(days=5), today + timedelta(days=9)
    bot = Bot()

    asyncio.run(vacation.ask_city(bot, "42"))
    asyncio.run(vacation.city_entered(bot, "42", f"Кейптаун до {_day(moving)}, далее Виндхук до {_day(back)}"))

    trip = vacation.state("42")
    assert places["now"]["city"] == "Кейптаун" and trip["cc"] == "ZA" and trip["until"] == back.isoformat()
    assert [leg["city"] for leg in trip["legs"]] == ["Кейптаун", "Виндхук"]
    assert any("Кейптаун → Виндхук" in message["text"] for message in bot.sent)  # «✈️ Хорошего отпуска · …»
    assert "Кейптаун → Виндхук, ЮАР и Намибия" in settings._trip_place_line("42", places["now"])
    assert vacation.countries_label(vacation.trip_countries("42")) == "ЮАР и Намибия"

    assert vacation.city_until("42") == moving  # афиша Кейптауна — до переезда, а не до конца отпуска
    asyncio.run(vacation.finish_due(Bot(), today=moving - timedelta(days=1)))
    assert places["now"]["city"] == "Кейптаун"
    asyncio.run(vacation.finish_due(Bot(), today=moving))  # 00:01 дня переезда — следующий город, его страна
    assert places["now"]["city"] == "Виндхук" and vacation.state("42")["cc"] == "NA"
    assert vacation.active("42")
    asyncio.run(vacation.finish_due(Bot(), today=back))
    assert not vacation.active("42") and places["now"]["city"] == HOME["city"]


def test_new_country_of_the_route_gets_its_own_guide(monkeypatch):
    saved, _places = _env(monkeypatch)
    saved["vacation"] = {"home": HOME, "cc": "NA", "until": "", "guide": {"v": vacation._GUIDE_VERSION, "cc": "ZA",
                                                                       "situations": [{"title": "в кафе"}]}}
    asked = []
    monkeypatch.setattr(vacation, "_cached", lambda *_a, **_k: {})
    monkeypatch.setattr(vacation.ai, "llm_json", lambda prompt, *_a, **_k: asked.append(prompt) or {
        "language_ru": "Английский", "cuisine_ru": "Намибийская",
        "situations": [{"title": "в кафе", "phrases": [{"text": "Thanks", "ru": "Спасибо"}]}]})

    found = vacation.guide("42")

    assert asked and found["cc"] == "NA" and found["cuisine_ru"] == "Намибийская"
    saved["vacation"] = {**saved["vacation"], "guide": {"v": vacation._GUIDE_VERSION, "language_ru": "Африкаанс"}}
    assert vacation.guide("42")["language_ru"] == "Африкаанс" and len(asked) == 1  # гид без страны — для этой


def _period(lat, _lon, start, end, _today):
    days = (end - start).days + 1
    south = lat < -30
    return {"tmin": 16 if south else 22, "tmax": 24 if south else 34, "rainy": 1, "days": days, "wind": 8,
            "typical": False,
            "daily": [{"date": (start + timedelta(days=index)).isoformat(), "tmax": 20, "tmin": 12, "rain": False,
                       "wind": 3, "code": 1, "kind": "forecast"} for index in range(days)]}


ROUTE_TRIP = {"city": "Кейптаун", "cc": "ZA", "lat": -33.9, "lon": 18.4, "start": "2026-10-19", "end": "2026-11-03",
              "legs": [{**CAPE_LEG, "start": "2026-10-19", "end": "2026-10-26"},
                       {"city": "Йоханнесбург", "cc": "ZA", "lat": -26.2, "lon": 28.0, "start": "2026-10-27",
                        "end": "2026-11-03"}]}


def test_suitcase_route_has_weather_by_city_and_each_day_knows_its_city(monkeypatch):
    import weather_provider
    _names(monkeypatch)
    calls = []
    monkeypatch.setattr(weather_provider, "period_weather",
                        lambda *args: calls.append(args[2:4]) or _period(*args))

    weather = wardrobe_suitcase._weather(ROUTE_TRIP)

    assert calls == [(date(2026, 10, 19), date(2026, 10, 26)), (date(2026, 10, 27), date(2026, 11, 3))]
    assert (weather["tmin"], weather["tmax"], weather["rainy"], weather["days"]) == (16, 34, 2, 16)
    assert {day["city"] for day in weather["daily"] if day["date"] == "2026-10-27"} == {"Йоханнесбург"}

    result = {"place": "Кейптаун", "cc": "ZA", "countries": ["ZA"], "start": "2026-10-19", "end": "2026-11-03",
              "days": 16, "groups": [("Верх", ["Белая футболка"])], "daily": weather["daily"],
              "wx": {key: weather[key] for key in wardrobe_suitcase._WX_KEYS}, "legs_wx": weather["legs"]}
    text = wardrobe_ui.suitcase_screen(wardrobe_suitcase._view(result)).text
    assert text.startswith(
        "🧳 Чемодан · ЮАР · 19 октября – 3 ноября · 16 дней\n\n"
        "Кейптаун, 19–26 октября: днём от +16° до +24°, 1 дождливый день, ветер до 8 м/с.\n"
        "Йоханнесбург, 27 октября – 3 ноября: днём от +22° до +34°, 1 дождливый день, ветер до 8 м/с.\n"
        "Днём до +34° — лёгкие вещи и защита от солнца, утром и вечером до +12° и ветрено — кофта или ветровка "
        "под рукой.\n\nФутболки и рубашки (1 вещь): белая футболка")

    closet = {"zones": {zone: {"Все": [{"id": name, "name": name, "zone": zone, "temp_range": [5, 35]}]}
                        for zone, name in (("Верх", "Белая футболка"), ("Низ", "Джинсы"), ("Обувь", "Белые кеды"))}}
    looks = wardrobe_suitcase.daily_looks(closet, {"Белая футболка", "Джинсы", "Белые кеды"}, ROUTE_TRIP,
                                          weather["daily"])
    assert [looks[7]["city"], looks[8]["city"]] == ["Кейптаун", "Йоханнесбург"]  # 27-е — уже новый город


def test_old_saved_suitcase_shows_rainy_days_and_a_code_tip_without_rebuild(monkeypatch):
    _names(monkeypatch)
    forecast = [{"date": f"2026-10-{19 + index}", "tmax": 30, "tmin": 12, "rain": index < 3, "wind": 8,
                 "kind": "forecast"} for index in range(13)]
    old = {"place": "Кейптаун", "cc": "ZA", "start": "2026-10-19", "end": "2026-11-04", "days": 17,
           "weather": "днём от +16° до +34°, дождь в 3 из 13 дней, ветер до 8 м/с",
           "tip": "В Кейптауне в октябре-ноябре тепло, но возможны дожди, поэтому возьмите легкую верхнюю одежду.",
           "wx": {"tmin": 16, "tmax": 34, "rainy": 3},
           "daily": forecast + [{"date": "2026-11-01", "tmax": 25, "tmin": 14, "kind": "typical"}],
           "groups": [("Обувь", ["Белые кеды", "Бежевые кеды"])]}

    text = wardrobe_ui.suitcase_screen(wardrobe_suitcase._view(old), saved=True).text

    assert text.startswith("🧳 Чемодан · ЮАР · 19 октября – 4 ноября · 17 дней\n\n"
                           "Днём от +16° до +34°, 3 дождливых дня, ветер до 8 м/с. Днём до +34° — лёгкие вещи")
    assert "из 13" not in text and "возьмите" not in text and "непромокаем" not in text  # 3 дня дождя — не повод
    assert "Обувь (2 пары): белые кеды, бежевые кеды" in text


def test_suitcase_route_typed_at_home_is_built_with_every_city(monkeypatch):
    built = []

    async def geocode_legs(legs):
        return [{**leg, "cc": "ZA", "country": "ЮАР", "lat": -30.0, "lon": 25.0} for leg in legs], ""

    async def build(_bot, _cid, trip, *_a, **_k):
        built.append(trip)

    monkeypatch.setattr(vacation, "geocode_legs", geocode_legs)
    monkeypatch.setattr(wardrobe_suitcase, "_build_and_show", build)
    today = datetime.now(config.TZ).date()
    first, moving, end = today + timedelta(days=2), today + timedelta(days=6), today + timedelta(days=10)

    asyncio.run(wardrobe_suitcase.trip_entered(
        Bot(), "42", f"Кейптаун с {_day(first)} по {_day(moving)}, далее Йоханнесбург до {_day(end)}"))

    trip = built[0]
    assert (trip["city"], trip["start"], trip["end"]) == ("Кейптаун", first.isoformat(), end.isoformat())
    assert [(leg["city"], leg["start"], leg["end"]) for leg in trip["legs"]] == [
        ("Кейптаун", first.isoformat(), (moving - timedelta(days=1)).isoformat()),
        ("Йоханнесбург", moving.isoformat(), end.isoformat())]
    assert "42" not in store.pending_input
