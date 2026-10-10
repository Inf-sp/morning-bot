import os
from datetime import date, datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import ai
import research
import store
import wardrobe_rules as rules
import wardrobe_stylist
from ui.wardrobe import render_wardrobe_message
from wardrobe_outfit import build_sock_recommendation, pick_best_outfit, top_outfits
from wardrobe_weather import wear_window

NOW = datetime(2026, 10, 9, 9, 30)


def _hourly(temp, feels, *, rain_from=None, gust=4, day="2026-10-09"):
    """Почасовой прогноз 00–23; feels — число или функция часа."""
    hours = range(24)
    feel = feels if callable(feels) else (lambda _h: feels)
    return {"hourly": {
        "time": [f"{day}T{h:02d}:00" for h in hours],
        "temperature_2m": [temp] * 24,
        "apparent_temperature": [feel(h) for h in hours],
        "precipitation_probability": [80 if rain_from is not None and h >= rain_from else 0 for h in hours],
        "precipitation": [0] * 24,
        "windspeed_10m": [min(gust, 6)] * 24,
        "windgusts_10m": [gust] * 24,
    }}


def _it(item_id, zone, name, color, **extra):
    return {"id": item_id, "zone": zone, "name": name, "colors": [color], **extra}


def _wardrobe(*items):
    zones = {}
    for item in items:
        zones.setdefault(item["zone"], {}).setdefault("Вещи", []).append(item)
    return {"zones": zones}


TEE = _it("tee", "Верх", "Белая футболка", "белый", warmth="лёгкие")
SHORTS = _it("shorts", "Низ", "Оливковые шорты", "оливковый", warmth="лёгкие")
JEANS = _it("jeans", "Низ", "Тёмно-синие джинсы", "тёмно-синий", material="деним")
SANDALS = _it("sandals", "Обувь", "Коричневые сандалии", "коричневый", warmth="лёгкие")
SUEDE = _it("suede", "Обувь", "Бежевые замшевые лоферы", "бежевый", material="замша")
SNEAKERS = _it("sneakers", "Обувь", "Белые кожаные кеды", "белый", material="кожа")
RAIN_JACKET = _it("rain", "Верхняя одежда", "Тёмно-синяя куртка", "тёмно-синий", rain_ok=True, wind_ok=True)
DENIM_JACKET = _it("denim", "Верхняя одежда", "Голубая джинсовая куртка", "голубой", material="деним")


def _ids(outfit):
    return {item["id"] for item in outfit}


def test_plus_18_rain_and_wind_never_gives_shorts():
    ctx = wear_window(_hourly(18, 15, rain_from=13, gust=12), NOW)
    assert ctx["window"] == (9, 22) and ctx["rain_from"] == 13 and ctx["gust_max"] == 12
    w = _wardrobe(TEE, SHORTS, JEANS, SANDALS, SUEDE, SNEAKERS, RAIN_JACKET, DENIM_JACKET)

    outfits = top_outfits(w, ctx, [], "", selected_styles=[])

    assert outfits
    for outfit in outfits:
        assert not _ids(outfit) & {"shorts", "sandals", "suede", "denim"}
        assert "rain" in _ids(outfit)
    reason = rules.weather_reason(ctx, outfits[0])
    assert reason == ("Дождь с 13:00 и порывы ветра до 12 м/с, лучше выбрать непромокаемую верхнюю одежду "
                      "и закрытую обувь.")


def test_plus_28_dry_allows_shorts():
    ctx = wear_window(_hourly(28, lambda h: 21 if h < 11 else 28), NOW)
    w = _wardrobe(TEE, SHORTS, JEANS, SANDALS, SNEAKERS, RAIN_JACKET)

    outfit = pick_best_outfit(w, ctx, [], "", selected_styles=[])

    assert "shorts" in _ids(outfit)
    assert "rain" not in _ids(outfit)
    assert rules.weather_reason(ctx, outfit).startswith("Тепло до +28° и сухо")


def test_cold_morning_warm_day_adds_removable_layer():
    ctx = wear_window(_hourly(17, lambda h: 8 if h < 11 else 19), NOW)
    assert ctx["layering"] and ctx["feels_min"] == 8 and ctx["feels_max"] == 19
    w = _wardrobe(TEE, JEANS, SNEAKERS, DENIM_JACKET)

    outfit = pick_best_outfit(w, ctx, [], "", selected_styles=[])

    assert "denim" in _ids(outfit)
    assert "слой, который можно снять" in rules.weather_reason(ctx, outfit)


def test_storm_requires_windproof_outerwear():
    ctx = wear_window(_hourly(13, 10, gust=18), NOW)
    coat = _it("coat", "Верхняя одежда", "Серое шерстяное пальто", "серый", material="шерсть")
    windbreaker = _it("wind", "Верхняя одежда", "Оливковая ветровка", "оливковый", wind_ok=True)
    w = _wardrobe(TEE, JEANS, SNEAKERS, coat, windbreaker)

    for outfit in top_outfits(w, ctx, [], "", selected_styles=[]):
        assert "wind" in _ids(outfit) and "coat" not in _ids(outfit)


def test_window_after_22_moves_to_tomorrow():
    data = _hourly(15, 15, day="2026-10-10")
    ctx = wear_window(data, datetime(2026, 10, 9, 22, 40))
    assert ctx["window"] == (8, 22)
    assert wear_window({"hourly": {}}, NOW) is None


def test_colour_harmony_prefers_classic_pairs():
    def look(*colors):
        return [{"zone": zone, "colors": [color]} for zone, color in zip(("Верх", "Низ", "Обувь"), colors)]

    navy_brown = rules.harmony_score(look("белый", "тёмно-синий", "коричневый"))
    red_pink = rules.harmony_score(look("красный", "розовый", "белый"))
    two_brights = rules.harmony_score(look("жёлтый", "оранжевый", "чёрный"))
    assert navy_brown > red_pink and navy_brown > two_brights
    assert rules.harmony_score(look("белый", "оливковый", "бежевый")) > red_pink


def test_socks_follow_trousers_when_top_is_the_accent():
    items = [_it("t", "Верх", "Бордовый свитер", "бордовый"), JEANS, SNEAKERS]
    assert build_sock_recommendation(items) == "Серые носки"
    assert build_sock_recommendation(items, "Бордовые носки") == "Бордовые носки"


OUTFITS = [[TEE, JEANS, SNEAKERS], [TEE, JEANS, SUEDE]]


def _stylist(monkeypatch, answer):
    def fake(*_args, **_kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(ai, "llm_json", fake)
    return wardrobe_stylist.choose(OUTFITS, {"tmax": 18}, ["Городской"], "", wardrobe_stylist.BASE_TRENDS)


def test_stylist_answer_is_validated(monkeypatch):
    good = {"choice": 2, "socks": "бордовые"}
    assert _stylist(monkeypatch, good) == {"index": 1, "socks": "Бордовые носки"}
    assert _stylist(monkeypatch, {**good, "choice": 7}) is None
    assert _stylist(monkeypatch, {**good, "socks": "тёмно-синие"}) is None
    assert _stylist(monkeypatch, {**good, "socks": "очень яркие неоновые"}) is None
    assert _stylist(monkeypatch, RuntimeError("down")) is None
    assert _stylist(monkeypatch, None) is None


def test_trends_fall_back_to_base_without_search(monkeypatch):
    saved = {}
    monkeypatch.setattr(store, "_load", lambda key: saved.get(key))
    monkeypatch.setattr(store, "mutate_kv", lambda key, fn: saved.__setitem__(key, fn(saved.get(key))[0]))
    monkeypatch.setattr(research, "web_search", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no key")))

    trends = wardrobe_stylist.weekly_trends(("Городской",), today=date(2026, 10, 9))

    assert trends == list(wardrobe_stylist.BASE_TRENDS)
    entry = saved["wardrobe_trends_cache.json"]["styles"]["Городской"]
    assert entry["expires"] == "2026-10-10"


def test_trends_are_searched_once_a_week(monkeypatch):
    saved, calls = {}, []
    monkeypatch.setattr(store, "_load", lambda key: saved.get(key))
    monkeypatch.setattr(store, "mutate_kv", lambda key, fn: saved.__setitem__(key, fn(saved.get(key))[0]))
    monkeypatch.setattr(research, "web_search", lambda query, **_k: calls.append(query) or [{"content": "x"}])
    monkeypatch.setattr(ai, "llm_json", lambda *_a, **_k: {"trends": ["Свободные брюки", "Олива и беж", "Тон в тон"]})

    first = wardrobe_stylist.weekly_trends(("Городской",), today=date(2026, 10, 9))
    again = wardrobe_stylist.weekly_trends(("Городской",), today=date(2026, 10, 12))

    assert first == again == ["Свободные брюки", "Олива и беж", "Тон в тон"]
    assert calls == ["men's urban streetwear street style trends fall 2026"]


def test_card_shows_weather_reason_after_list():
    msg = render_wardrobe_message({
        "primary_style": "Городской", "items": [{"name": "Белая футболка", "zone": "Верх"}],
        "sock_recommendation": "Серые носки",
        "weather_reason": "☂️ Дождь с 13:00 — непромокаемая верхняя одежда",
    })

    lines = msg.text.splitlines()
    assert lines.index("Дождь с 13:00 — непромокаемая верхняя одежда") == lines.index("- Серые носки") + 2
    assert lines[-1] == "Дождь с 13:00 — непромокаемая верхняя одежда"
    assert "☂️" not in render_wardrobe_message({"items": [{"name": "Футболка", "zone": "Верх"}]}).text


def test_send_looks_without_ai_uses_window_weather(monkeypatch):
    import asyncio
    from datetime import timedelta

    import category_news
    import config
    import settings
    import wardrobe
    import weather

    today = datetime.now(config.TZ).date()
    days = [today, today + timedelta(days=1)]
    hourly = {key: [] for key in _hourly(18, 15)["hourly"]}
    for day in days:
        for key, values in _hourly(18, 15, rain_from=0, gust=12, day=day.isoformat())["hourly"].items():
            hourly[key] += values
    wdata = {"hourly": hourly, "daily": {
        "time": [d.isoformat() for d in days], "temperature_2m_min": [16, 16], "temperature_2m_max": [18, 18],
        "windspeed_10m_max": [6, 6], "precipitation_probability_max": [80, 80],
        "precipitation_sum": [3, 3], "weathercode": [61, 61],
    }}
    w = _wardrobe(TEE, SHORTS, JEANS, SANDALS, SNEAKERS, RAIN_JACKET)
    saved = {}

    async def same(_cid, wardrobe_data):
        return wardrobe_data

    monkeypatch.setattr(wardrobe, "_get_cached_look", lambda _cid: None)
    monkeypatch.setattr(store, "load_wardrobe", lambda _cid: w)
    monkeypatch.setattr(store, "wardrobe_to_text", lambda _w: "шкаф")
    monkeypatch.setattr(store, "get_settings", lambda _cid: {"lat": 52.6, "lon": 4.7})
    monkeypatch.setattr(store, "get_wardrobe_history", lambda _cid: [])
    monkeypatch.setattr(weather, "fetch_weather", lambda *_a: wdata)
    monkeypatch.setattr(wardrobe, "_build_weather_rules", lambda *_a: ([], ""))
    monkeypatch.setattr(wardrobe, "migrate_item_attrs", same)
    monkeypatch.setattr(settings, "wardrobe_prefs_context", lambda _cid: "")
    monkeypatch.setattr(settings, "wardrobe_styles", lambda _cid: ["Городской"])
    monkeypatch.setattr(wardrobe, "_get_or_create_purchase_recommendation", lambda *_a, **_k: None)
    monkeypatch.setattr(category_news, "cached_line", lambda _key: "")
    monkeypatch.setattr(wardrobe, "save_outfit_feedback", lambda *_a: None)
    monkeypatch.setattr(wardrobe, "_save_cached_look", lambda _cid, ids, look_data: saved.update(ids=ids, look=look_data))
    monkeypatch.setattr(wardrobe_stylist, "weekly_trends", lambda _styles: list(wardrobe_stylist.BASE_TRENDS))
    monkeypatch.setattr(ai, "llm_json", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("AI down")))

    asyncio.run(wardrobe.send_looks(None, 1, silent=True))

    assert set(saved["ids"]) == {"tee", "jeans", "sneakers", "rain"}
    assert saved["look"]["weather_reason"].startswith("Дождь и порывы ветра до 12 м/с, лучше выбрать")
    assert saved["look"]["sock_recommendation"] and "main_accent" not in saved["look"]


def test_weather_suggests_accessories_instead_of_wardrobe_items():
    sunny = wear_window(_hourly(28, lambda h: 21 if h < 11 else 28), NOW)
    frost = wear_window(_hourly(-2, -6), NOW)
    chilly = wear_window(_hourly(6, 4), NOW)

    assert rules.weather_reason({**sunny, "sunny": True}, [TEE, SHORTS, SNEAKERS]).endswith(
        ", лучше выбрать лёгкие вещи и солнечные очки.")
    assert rules.weather_reason(frost, [TEE, JEANS, SNEAKERS]) == "Холодно, ощущается -6°, лучше выбрать шапку и перчатки."
    assert rules.weather_reason(chilly, [TEE, JEANS, SNEAKERS]) == "Холодно, ощущается +4°, лучше выбрать шарф."
    assert rules.weather_reason(wear_window(_hourly(18, 17), NOW), [TEE, JEANS, SNEAKERS]) == ""


HOODIE = _it("hoodie", "Кофты", "Серая худи с капюшоном", "серый")


def test_hoodie_is_a_layer_over_a_tee_in_cool_weather():
    ctx = wear_window(_hourly(13, 11), NOW)
    w = _wardrobe(TEE, HOODIE, JEANS, SNEAKERS)

    outfit = pick_best_outfit(w, ctx, [], "", selected_styles=[])

    assert {"tee", "hoodie"} <= _ids(outfit)  # футболка под худи
    from ui.wardrobe import outfit_item_names
    from wardrobe_outfit import outfit_display_order
    names = outfit_item_names({"items": [{"name": it["name"], "zone": it["zone"]}
                                         for it in sorted(outfit, key=outfit_display_order)]})
    assert names[:2] == ["Белая футболка", "Серая худи с капюшоном"]


def test_no_hoodie_in_warm_weather_and_never_alone():
    warm = wear_window(_hourly(24, 22), NOW)
    assert "hoodie" not in _ids(pick_best_outfit(_wardrobe(TEE, HOODIE, JEANS, SNEAKERS), warm, [], "",
                                                  selected_styles=[]))
    cool = wear_window(_hourly(13, 11), NOW)
    assert pick_best_outfit(_wardrobe(HOODIE, JEANS, SNEAKERS), cool, [], "", selected_styles=[]) is None


def test_sweaters_move_to_their_own_closet_category():
    w = {"_v": 2, "zones": {"Верх": {
        "Худи": [{"id": "h", "zone": "Верх", "name": "Серая худи"}],
        "Футболки": [{"id": "t", "zone": "Верх", "name": "Белая футболка"},
                     {"id": "s", "zone": "Верх", "name": "Синий свитшот"}],
    }}}

    assert store._move_sweaters(w) is True
    assert w["zones"]["Верх"] == {"Футболки": [{"id": "t", "zone": "Верх", "name": "Белая футболка"}]}
    assert {item["id"] for items in w["zones"]["Кофты"].values() for item in items} == {"h", "s"}
    assert w["zones"]["Кофты"]["Свитшоты"][0]["subcategory"] == "Свитшоты" and w["_v"] == 3
    assert store._move_sweaters(w) is False


def test_cached_look_is_rebuilt_when_rain_appears(monkeypatch):
    import wardrobe
    cached = {"item_ids": ["tee"], "look_data": {"items": [], "weather": {
        "rain": False, "wind": False, "cold": True, "summer": False}}}
    rainy = (wear_window(_hourly(18, 15, rain_from=0, gust=12), NOW), {"sunny": False})
    dry_same = (wear_window(_hourly(13, 11), NOW), {"sunny": False})

    assert wardrobe.weather_signature(*rainy) != cached["look_data"]["weather"]
    assert wardrobe.weather_signature(*dry_same) == cached["look_data"]["weather"]
    assert wardrobe.weather_signature({}, None) is None  # без погоды — не сравниваем
