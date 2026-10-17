import asyncio
import os
from datetime import date

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import wardrobe
import wardrobe_suitcase
from ui import wardrobe as wardrobe_ui


def _item(name, temp=(5, 26), rain_ok=False):
    return {"name": name, "temp_range": list(temp), "rain_ok": rain_ok}


CLOTHES = [
    ("Верх", _item("Белая футболка", (15, 35))), ("Верх", _item("Серая футболка", (15, 35))),
    ("Низ", _item("Бежевые чиносы")), ("Низ", _item("Джинсы")),
    ("Обувь", _item("Белые кеды")),
    ("Верхняя одежда", _item("Пуховик", (-20, 5))), ("Верхняя одежда", _item("Ветровка", (5, 26), rain_ok=True)),
]
WARM = {"tmin": 18, "tmax": 24, "rainy": 0, "days": 8, "wind": 5, "typical": False}
TRIP = {"city": "Кейптаун", "cc": "ZA", "lat": -33.9, "lon": 18.4, "start": "2026-10-20", "end": "2026-10-27"}


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


def _memory_profile(monkeypatch):
    profile = {}

    def mutate(_cid, change):  # как store.mutate_profile: профиль целиком заменяется результатом
        updated = change(dict(profile))[0]
        profile.clear()
        profile.update(updated)

    monkeypatch.setattr(wardrobe_suitcase.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(wardrobe_suitcase.store, "mutate_profile", mutate)
    return profile


def test_ai_suitcase_keeps_only_real_wardrobe_items(monkeypatch):
    prompts = []
    monkeypatch.setattr(wardrobe_suitcase.ai, "llm_json", lambda prompt, *_a, **_k: prompts.append(prompt) or {
        "items": [0, 2, 2, 99, "кроссовки", 4], "tip": "Вечером у океана прохладно"})

    picked, tip = wardrobe_suitcase._ai_pick("42", CLOTHES, TRIP, "", 8, avoid=["Пуховик"])

    assert picked == [0, 2, 4]  # несуществующий номер, повтор и придуманная вещь отброшены
    assert tip == "Вечером у океана прохладно"
    assert "другой вариант" in prompts[0] and "2026-10-20 – 2026-10-27" in prompts[0]  # «Пересобрать»


def test_rule_suitcase_fits_trip_weather_and_takes_rain_jacket():
    picked = wardrobe_suitcase.rule_pick(CLOTHES, {"tmin": 14, "tmax": 22, "rainy": 2}, days=9)
    names = [CLOTHES[i][1]["name"] for i in picked]

    assert "Ветровка" in names and "Пуховик" not in names  # тёплый пуховик не по погоде, дождь — ветровка
    assert {"Белая футболка", "Серая футболка", "Бежевые чиносы", "Джинсы", "Белые кеды"} <= set(names)


def test_empty_wardrobe_gets_a_basic_set_for_the_trip_weather(monkeypatch):
    monkeypatch.setattr(wardrobe_suitcase, "_clothes", lambda _cid: [])
    monkeypatch.setattr(wardrobe_suitcase, "_weather", lambda _trip: {"tmin": 23, "tmax": 34, "rainy": 0,
                                                                         "days": 8, "wind": 9, "typical": True})

    result = wardrobe_suitcase.pack("42", TRIP)

    assert result["basic"] and result["days"] == 8 and result["typical"]
    groups = dict(result["groups"])
    assert groups["Верх"][0] == "футболки — 6" and "шорты — 2" in groups["Низ"] and "сандалии" in groups["Обувь"]
    text = wardrobe_ui.suitcase_screen(result).text
    assert text.startswith("🧳 Чемодан · Кейптаун · 20–27 октября · 8 дней\n\nОбычно в эти дни: днём от +23°")
    assert "В шкафу пока нет вещей — вот базовый набор под эту погоду:" in text


def test_home_suitcase_asks_city_and_dates_then_builds_and_confirms(monkeypatch):
    import weather_location
    profile = _memory_profile(monkeypatch)
    monkeypatch.setattr(wardrobe_suitcase.vacation, "active", lambda _cid: False)
    monkeypatch.setattr(wardrobe_suitcase.vacation, "split_city_and_period",
                        lambda text: ("Кейптаун", (date(2026, 10, 20), date(2026, 10, 27))) if "20" in text
                        else (text, None))

    async def geocode(name):
        return {"latitude": -33.9, "longitude": 18.4, "name": "Кейптаун", "country_code": "za"}

    monkeypatch.setattr(weather_location, "geocode", geocode)
    monkeypatch.setattr(wardrobe_suitcase, "pack", lambda _cid, trip, avoid=(), declined=(): {
        "place": trip["city"], "start": trip["start"], "end": trip["end"], "days": 8,
        "groups": [("Верх", ["Белая футболка"])], "avoid": list(avoid)})
    bot = Bot()

    asyncio.run(wardrobe_suitcase.send(bot, "42"))
    assert wardrobe_suitcase.store.pending_input["42"] == "suitcase_trip"  # дома — сначала «Куда и когда?»
    asyncio.run(wardrobe_suitcase.trip_entered(bot, "42", "Кейптаун когда-нибудь"))
    assert bot.sent[-1]["text"].startswith("🧳 Собрать чемодан\n\nНе понял даты поездки.")
    asyncio.run(wardrobe_suitcase.trip_entered(bot, "42", "Кейптаун, 20–27 октября"))

    assert profile["suitcase_draft"]["trip"]["cc"] == "ZA" and "42" not in wardrobe_suitcase.store.pending_input
    assert [[b.callback_data for b in row] for row in bot.sent[-1]["reply_markup"].inline_keyboard] == [
        ["w_pack_ok"], ["w_pack_again"], ["m_wardrobe", "m_menu"]]
    monkeypatch.setattr(wardrobe_suitcase, "_clothes", lambda _cid: CLOTHES)
    monkeypatch.setattr(wardrobe_suitcase, "_weather", lambda _trip: {"tmin": 18, "tmax": 24, "rainy": 0})

    # «Пересобрать» — вещи чемодана кнопками: зелёная «беру», нажал — красная «не беру».
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_again"))
    rows = bot.sent[-1]["reply_markup"].inline_keyboard
    assert bot.sent[-1]["text"].startswith("🧳 Что не берёшь? · Кейптаун · 8 дней")
    assert [[b.text for b in row] for row in rows] == [
        ["✅ Белая футболка"], ["Подобрать замену"], ["⬅️ Назад", "#️⃣ Главная"]]
    assert [rows[0][0].callback_data, rows[2][0].callback_data] == ["w_pack_t:0", "w_pack_view"]
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_t:0"))
    button = bot.sent[-1]["reply_markup"].inline_keyboard[0][0]
    assert button.text == "□ Белая футболка" and button.api_kwargs == {"style": "danger"}
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_swap"))
    assert profile["suitcase_draft"]["result"]["groups"] == [("Верх", ["Серая футболка"])]
    assert "Заменил: белая футболка → серая футболка." in bot.sent[-1]["text"]
    assert profile["suitcase_draft"]["removed"] == []

    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_ok"))
    assert wardrobe_suitcase.saved_suitcase("42")["trip"]["city"] == "Кейптаун"
    assert bot.sent[-1]["text"].startswith("🧳 Чемодан · Кейптаун · 20–27 октября · 8 дней\n\n✅ Сохранён")
    assert "Заменил" not in bot.sent[-1]["text"]
    assert [[b.callback_data for b in row] for row in bot.sent[-1]["reply_markup"].inline_keyboard] == [
        ["w_pack_looks"], ["w_pack_edit"], ["w_pack_del"], ["m_wardrobe", "m_menu"]]
    assert wardrobe_suitcase.saved_suitcase("42")["declined"] == ["Белая футболка"]  # отказ помнится и после «Подтвердить»

    # «Собрать чемодан» дома теперь сразу открывает сохранённый чемодан, а не спрашивает о поездке.
    asyncio.run(wardrobe_suitcase.send(bot, "42"))
    assert "✅ Сохранён — открывается из Гардероба." in bot.sent[-1]["text"]
    assert "42" not in wardrobe_suitcase.store.pending_input

    # «Удалить подборку» — сразу, без подтверждения; дальше снова вопрос «Куда и когда едешь?».
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_del"))
    assert "suitcase_saved" not in profile and "suitcase_draft" not in profile
    assert bot.sent[-1]["text"].startswith("🧳 Собрать чемодан\n\nКуда и когда едешь?")
    assert wardrobe_suitcase.store.pending_input["42"] == "suitcase_trip"


def test_on_vacation_the_confirmed_suitcase_of_this_country_is_shown(monkeypatch):
    profile = _memory_profile(monkeypatch)
    profile["suitcase_saved"] = {"trip": TRIP, "result": {"place": "Кейптаун", "groups": [("Верх", ["Белая футболка"])]}}
    monkeypatch.setattr(wardrobe_suitcase.vacation, "active", lambda _cid: True)
    monkeypatch.setattr(wardrobe_suitcase, "vacation_trip", lambda _cid: {**TRIP, "start": "2026-10-21"})
    monkeypatch.setattr(wardrobe_suitcase, "pack", lambda *_a: (_ for _ in ()).throw(AssertionError("rebuilt")))
    bot = Bot()

    asyncio.run(wardrobe_suitcase.send(bot, "42"))

    assert "✅ Сохранён" in bot.sent[-1]["text"]  # собранный дома чемодан — без новой сборки
    assert [[b.callback_data for b in row] for row in bot.sent[-1]["reply_markup"].inline_keyboard] == [
        ["w_pack_looks"], ["w_pack_edit"], ["w_pack_del"], ["m_wardrobe", "m_menu"]]


def test_pack_button_at_home_and_on_vacation(monkeypatch):
    def callbacks(markup):
        return [b.callback_data for row in markup.inline_keyboard for b in row]

    monkeypatch.setattr(wardrobe_suitcase.vacation, "active", lambda _cid: False)
    assert callbacks(wardrobe.build_wardrobe_keyboard("42"))[2] == "w_pack"  # дома — под стилями
    monkeypatch.setattr(wardrobe_suitcase.vacation, "active", lambda _cid: True)
    # В отпуске нет стилей: «Ещё вариант» — новый образ дня, ниже «Мой чемодан».
    assert callbacks(wardrobe.build_wardrobe_keyboard("42")) == ["w_look", "w_pack", "w_closet", "m_menu"]


def test_vacation_look_uses_only_packed_items_when_suitcase_has_a_full_outfit(monkeypatch):
    wardrobe_data = {"_v": 2, "zones": {
        "Верх": {"Футболки": [{"name": "Белая футболка"}, {"name": "Чёрная футболка"}]},
        "Низ": {"Джинсы": [{"name": "Джинсы"}, {"name": "Брюки"}]},
        "Обувь": {"Кеды": [{"name": "Белые кеды"}]},
    }}
    profile = _memory_profile(monkeypatch)
    profile["suitcase_saved"] = {"trip": TRIP, "result": {"groups": [
        ["Верх", ["Белая футболка"]], ["Низ", ["Джинсы"]], ["Обувь", ["Белые кеды"]]]}}
    monkeypatch.setattr(wardrobe_suitcase.vacation, "active", lambda _cid: True)
    monkeypatch.setattr(wardrobe_suitcase, "vacation_trip", lambda _cid: TRIP)

    packed = wardrobe_suitcase.packed_wardrobe("42", wardrobe_data)
    assert packed["zones"]["Верх"]["Футболки"] == [{"name": "Белая футболка"}]  # только из чемодана

    profile["suitcase_saved"]["result"]["groups"] = [["Верх", ["Белая футболка"]]]  # нет низа и обуви
    assert wardrobe_suitcase.packed_wardrobe("42", wardrobe_data) is wardrobe_data
    profile["suitcase_saved"]["trip"] = {**TRIP, "cc": "ES"}  # чемодан другой страны не в счёт
    assert wardrobe_suitcase.packed_wardrobe("42", wardrobe_data) is wardrobe_data


def test_swap_takes_a_same_zone_item_for_the_weather_and_never_a_declined_one():
    groups = [("Верх", ["Белая футболка"]), ("Обувь", ["Белые кеды"]), ("Верхняя одежда", ["Ветровка"])]

    new_groups, swaps = wardrobe_suitcase.swap_items(
        CLOTHES, groups, ["Белая футболка", "Белые кеды", "Ветровка"], {"tmin": 18, "tmax": 24},
        declined=["Серая футболка"])

    # Серая футболка уже была отклонена, другой обуви нет, пуховик не по погоде — честно «замены нет».
    assert new_groups == []
    assert swaps == [("Белая футболка", ""), ("Белые кеды", ""), ("Ветровка", "")]
    assert wardrobe_suitcase.swap_items(CLOTHES, groups, ["Белая футболка"], {"tmin": 18, "tmax": 24})[0][0] == (
        "Верх", ["Серая футболка"])


def _closet(*items):
    zones = {}
    for index, (zone, name) in enumerate(items):
        zones.setdefault(zone, {}).setdefault("Все", []).append(
            {"id": f"i{index}", "name": name, "zone": zone, "warmth": "обычные", "temp_range": [5, 35]})
    return {"zones": zones}


def test_daily_looks_follow_each_day_weather_and_rotate_items():
    closet = _closet(("Верх", "Белая футболка"), ("Верх", "Серая футболка"), ("Низ", "Бежевые чиносы"),
                     ("Низ", "Джинсы"), ("Обувь", "Белые кеды"), ("Обувь", "Кроссовки"), ("Верх", "Пиджак не брал"))
    names = {"Белая футболка", "Серая футболка", "Бежевые чиносы", "Джинсы", "Белые кеды", "Кроссовки"}
    trip = {"start": "2026-10-20", "end": "2026-10-22"}
    daily = [{"date": "2026-10-20", "tmax": 22, "tmin": 15, "rain": False, "wind": 3},
             {"date": "2026-10-21", "tmax": 19, "tmin": 13, "rain": True, "wind": 5}]

    looks = wardrobe_suitcase.daily_looks(closet, names, trip, daily)

    assert [look["date"] for look in looks] == ["2026-10-20", "2026-10-21", "2026-10-22"]
    assert looks[1]["tmax"] == 19 and looks[1]["rain"] and looks[2]["tmax"] is None  # за прогнозом — без погоды
    assert all(len(look["items"]) == 3 and "Пиджак не брал" not in look["items"] for look in looks)
    assert set(looks[0]["items"]).isdisjoint(looks[1]["items"])  # вещи чередуются, а не повторяются подряд
    text = wardrobe_ui.suitcase_looks_screen({"place": "Кейптаун", "days": 3}, looks).text
    assert text.startswith("👕 Образы · Кейптаун · 3 дня\n\nВт 20 окт · +22° · ")
    assert "\nСр 21 окт · +19°, дождь · " in text


def test_trip_weather_keeps_each_day_for_daily_looks(monkeypatch):
    import weather_provider

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"daily": {"time": ["2026-10-20", "2026-10-21"], "temperature_2m_max": [22.4, 18.6],
                              "temperature_2m_min": [14.2, 12.8], "precipitation_probability_max": [10, 80],
                              "precipitation_sum": [0, 4], "windspeed_10m_max": [3.2, 9.6]}}

    monkeypatch.setattr(weather_provider.requests, "get", lambda *_a, **_k: Response())

    found = weather_provider.period_weather(1, 2, date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 16))

    assert found["rainy"] == 1 and found["tmin"] == 19 and found["tmax"] == 22
    assert found["daily"] == [
        {"date": "2026-10-20", "tmax": 22, "tmin": 14, "rain": False, "wind": 3},
        {"date": "2026-10-21", "tmax": 19, "tmin": 13, "rain": True, "wind": 10}]


def test_declined_items_never_come_back_after_confirm_rebuild_or_new_pack(monkeypatch):
    profile = _memory_profile(monkeypatch)
    trip = {**TRIP}
    profile["suitcase_saved"] = {"trip": trip, "declined": ["Белая футболка"], "result": {
        "place": "Кейптаун", "days": 8, "groups": [("Верх", ["Серая футболка"]), ("Низ", ["Джинсы"])],
        "wx": {"tmin": 18, "tmax": 24}}}
    monkeypatch.setattr(wardrobe_suitcase, "_clothes", lambda _cid: CLOTHES)
    bot = Bot()

    # «Пересобрать» сохранённого: отказ из прошлого раза переходит в черновик.
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_edit"))
    assert profile["suitcase_draft"]["declined"] == ["Белая футболка"]
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_t:0"))
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_view"))  # «Назад» без замены — отметка остаётся
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_again"))
    assert profile["suitcase_draft"]["removed"] == [0]
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_swap"))
    # Других футболок нет (белая отклонена раньше) — верх берётся любой свободный, а не отклонённый.
    assert "Белая футболка" not in [name for _zone, names in profile["suitcase_draft"]["result"]["groups"]
                                    for name in names]
    assert profile["suitcase_draft"]["declined"] == ["Белая футболка", "Серая футболка"]

    # Новая сборка на ту же поездку: отклонённых вещей не видит ни AI, ни запасной подбор.
    seen = []
    monkeypatch.setattr(wardrobe_suitcase, "_weather", lambda _trip: WARM)
    monkeypatch.setattr(wardrobe_suitcase, "_ai_pick", lambda _cid, clothes, *_a: seen.extend(
        item["name"] for _zone, item in clothes) or ([], ""))
    result = wardrobe_suitcase.pack("42", trip, declined=wardrobe_suitcase._declined_for("42", trip))
    names = [name for _zone, group in result["groups"] for name in group]
    assert "Белая футболка" not in seen and "Серая футболка" not in seen
    assert "Белая футболка" not in names and "Серая футболка" not in names


def test_pack_always_has_top_bottom_and_shoes(monkeypatch):
    monkeypatch.setattr(wardrobe_suitcase, "_clothes", lambda _cid: CLOTHES)
    monkeypatch.setattr(wardrobe_suitcase, "_weather", lambda _trip: WARM)
    monkeypatch.setattr(wardrobe_suitcase, "_ai_pick", lambda *_a: ([0, 1], "Возьми солнцезащитный крем."))

    groups = dict(wardrobe_suitcase.pack("42", TRIP)["groups"])

    assert groups["Верх"] == ["Белая футболка", "Серая футболка"]
    assert groups["Низ"] == ["Бежевые чиносы"] and groups["Обувь"] == ["Белые кеды"]


def test_swap_keeps_the_last_shoes_when_no_pair_fits_the_weather():
    clothes = [("Обувь", _item("Белые кеды")), ("Обувь", _item("Зимние ботинки", (-20, 5)))]

    groups, swaps = wardrobe_suitcase.swap_items(clothes, [("Обувь", ["Белые кеды"])], ["Белые кеды"],
                                                 {"tmin": 18, "tmax": 24})

    assert groups == [("Обувь", ["Зимние ботинки"])] and swaps == [("Белые кеды", "Зимние ботинки")]


def test_daily_looks_use_daytime_low_and_never_leave_a_day_empty():
    closet = _closet(("Верх", "Серая рубашка"), ("Верх", "Коричневая рубашка"), ("Низ", "Бежевые брюки"),
                     ("Обувь", "Белые кеды"), ("Верхняя одежда", "Чёрная ветровка"))
    for zone in ("Низ", "Обувь"):  # лёгкие: по правилам — только от +12° днём
        for item in closet["zones"][zone]["Все"]:
            item["warmth"] = "лёгкие"
    names = {"Серая рубашка", "Коричневая рубашка", "Бежевые брюки", "Чёрная ветровка"}  # обуви в чемодане нет
    trip = {"start": "2026-10-19", "end": "2026-10-21"}
    daily = [{"date": "2026-10-19", "tmax": 18, "tmin": 9, "rain": True, "wind": 4},  # ночью +9°, днём +18°
             {"date": "2026-10-20", "tmax": 19, "tmin": 8, "rain": False, "wind": 3},
             {"date": "2026-10-21", "tmax": 3, "tmin": -6, "rain": False, "wind": 2}]  # холодно для лёгкого

    looks = wardrobe_suitcase.daily_looks(closet, names, trip, daily)

    # Минимум дня — дневной (середина ночного минимума и максимума), а не ночные +9°: лёгкое проходит строгий подбор.
    assert wardrobe_suitcase._day_context(daily[0])["tmin"] == 14
    import wardrobe_outfit
    assert wardrobe_outfit.top_outfits(wardrobe_suitcase._suitcase_closet(closet, names),
                                       wardrobe_suitcase._day_context(daily[0]), [], "")
    assert all({"Бежевые брюки", "Белые кеды"} <= set(look["items"]) for look in looks)  # кеды — из шкафа
    assert all(any("рубашка" in name for name in look["items"]) for look in looks)
    assert "образ" not in wardrobe_ui.suitcase_looks_screen({"place": "Кейптаун", "days": 3}, looks).text.split(
        "\n", 1)[1]
