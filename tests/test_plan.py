from datetime import date

import plan
from ui import myday as myday_ui


def test_plan_has_weather_and_everything_for_tomorrow():
    msg = myday_ui.tomorrow_plan({
        "day": date(2026, 11, 25), "city": "Alkmaar", "cc": "NL", "flag": "🇳🇱",
        "weather": {"icon": "❄️", "line": "облачно, от -1° до +3° · ветер до 5 м/с · наледь утром",
                    "take": ["шапку", "перчатки"]},
        "light": "08:21–16:35",
        "official": ["Код жёлтый KNMI — ветер, завтра с 10:00 до 18:00"],
        "holidays": [{"date": "2026-11-25", "name": "Sinterklaasavond", "about": "вечер подарков"}],
        "events": [{"kind": "city", "name": "Ice rink on Waagplein", "date": "2026-11-20", "end": "2027-01-03",
                    "place": "Waagplein", "free": True}],
        "premieres": [{"kind": "premiere", "premiere": "movie", "name": "Дюна", "date": "2026-11-25",
                       "meta": ["фантастика"]}],
        "works": []})
    assert msg.text == (
        "План на завтра · Ср, 25 ноября · Alkmaar, NL 🇳🇱\n\n"
        "❄️ Погода: облачно, от -1° до +3° · ветер до 5 м/с · наледь утром\n🌅 Светло: 08:21–16:35\n"
        "🧤 Возьми: шапку и перчатки.\n\n"
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
    monkeypatch.setattr(plan, "daylight", lambda *_a: "")
    monkeypatch.setattr(leisure_events, "feed", lambda _cid: [
        {"kind": "city", "name": "Rink", "date": "2026-11-20", "end": "2027-01-03"},
        {"kind": "concert", "name": "Today", "date": "2026-11-24", "end": "2026-11-24"},
        {"kind": "premiere", "name": "Дюна", "date": "2026-11-25", "end": "2026-11-25"}])
    found = plan.collect("42", now=datetime(2026, 11, 24, 20, tzinfo=config.TZ))
    assert [card["name"] for card in found["events"]] == ["Rink"]  # сегодняшний концерт — не в плане на завтра
    assert [card["name"] for card in found["premieres"]] == ["Дюна"]


def _period(label, tmin, tmax, rain=0, rain_mm=0.0, icon="☁️"):
    return {"label": label, "tmin": tmin, "tmax": tmax, "rain": rain, "rain_mm": rain_mm, "winds": [3, 6],
            "clouds": 90, "code": 3, "icon": icon}


def test_tomorrow_weather_is_one_line_without_minus_zero():
    day = plan.weather_day([_period("Утром", -0.4, 1), _period("Днём", 1, 2), _period("Вечером", -0.3, -0.2)])
    # В плюс днём без снега — не снежинка, а знак неба; наледь — словами.
    assert day == {"icon": "☁️", "line": "облачно, от 0° до +2° · ветер до 6 м/с · наледь утром и вечером",
                   "take": ["шапку", "перчатки"]}
    rainy = plan.weather_day([_period("Утром", 6, 8), _period("Днём", 8, 11, rain=80, rain_mm=3, icon="🌧️"),
                              _period("Вечером", 7, 9, rain=70, rain_mm=1, icon="🌧️")])
    assert rainy == {"icon": "🌧️", "line": "дождь днём и вечером, от +6° до +11° · ветер до 6 м/с",
                     "take": ["шарф", "зонт"]}
    assert plan.weather_day([]) == {}


def test_daylight_from_coordinates_without_network():
    light = plan.daylight({"lat": 52.63, "lon": 4.75, "_cid": "42"}, date(2026, 11, 26))
    assert light.startswith("08:") and "–16:" in light


def _hourly(day, rain_from=None, windy_from=None):
    hours = [f"{day}T{hour:02d}:00" for hour in range(24)]
    return {"time": hours,
            "precipitation_probability": [80 if rain_from is not None and hour >= rain_from else 0 for hour in range(24)],
            "precipitation": [1.0 if rain_from is not None and hour >= rain_from else 0 for hour in range(24)],
            "windspeed_10m": [12 if windy_from is not None and hour >= windy_from else 4 for hour in range(24)]}


def test_bike_window_is_the_longest_dry_and_calm_stretch():
    day = date(2026, 11, 26)
    assert plan.bike_window(_hourly("2026-11-26", rain_from=13), day) == "сухо 07:00–13:00, потом дождь"
    assert plan.bike_window(_hourly("2026-11-26", windy_from=16), day) == "тихо 07:00–16:00, потом сильный ветер"
    assert plan.bike_window(_hourly("2026-11-26"), day) == ""  # весь день хорошо — и так видно по погоде
    assert plan.bike_window(_hourly("2026-11-26", rain_from=0), day) == ""  # весь день дождь — окна нет


def test_waste_address_and_tomorrows_collection(monkeypatch):
    import waste
    assert waste.parse_address("1811 ab, 12a") == {"postcode": "1811AB", "number": "12", "suffix": "a"}
    assert waste.parse_address("Laat 12") is None
    assert waste.label({"postcode": "1811AB", "number": "12", "suffix": ""}) == "1811 AB 12"
    monkeypatch.setattr(waste, "_get", lambda path: [
        {"title": "Papier en karton", "ophaaldatum": "2026-11-26"}, {"title": "GFT", "ophaaldatum": "2026-11-26"},
        {"title": "Restafval", "ophaaldatum": "2026-12-01"}, {"title": "Glas", "ophaaldatum": None}])
    monkeypatch.setattr(waste.util, "ttl_get", lambda *_a: None)
    monkeypatch.setattr(waste.util, "ttl_set", lambda _ns, _key, value: value)
    assert waste.on_day({"bagid": "b1"}, date(2026, 11, 26)) == ["бумага", "органика (GFT)"]
    assert waste.on_day({}, date(2026, 11, 26)) == []  # адреса нет — строки нет


def test_plan_shows_bike_waste_concert_soon_release_and_works_end():
    text = myday_ui.tomorrow_plan({
        "day": date(2026, 11, 26), "city": "Alkmaar", "cc": "NL", "flag": "🇳🇱",
        "weather": {"icon": "🌧️", "line": "дождь днём, от +6° до +9° · ветер до 7 м/с", "take": ["зонт"]},
        "light": "08:21–16:35", "bike": "сухо 07:00–12:00, потом дождь",
        "waste": ["бумага", "органика (GFT)"],
        "concerts_soon": [{"artist": "Muse", "date": "2026-11-28", "venue": "Ziggo Dome", "city": "Amsterdam"}],
        "releases": [{"artist": "Muse", "title": "Unravelling", "genre_label": "сингл"}],
        "works_end": [{"title": "Haarlem – Alkmaar"}]}).text
    assert "🚲 Велосипед: сухо 07:00–12:00, потом дождь\n🧤 Возьми: зонт." in text
    assert "♻️ Вывоз мусора: бумага и органика (GFT)" in text
    assert "🎫 Через 3 дня: Muse · Ziggo Dome, Amsterdam" in text
    assert "🎧 Новый сингл: Muse — «Unravelling»" in text
    assert "🚆 Поезда как обычно: Haarlem - Alkmaar — работы закончились" in text
