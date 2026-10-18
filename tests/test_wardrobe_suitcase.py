import asyncio

import pytest
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

    picked = wardrobe_suitcase._ai_pick("42", CLOTHES, TRIP, "", 8, avoid=["Пуховик"])

    assert picked == [0, 2, 4]  # несуществующий номер, повтор и придуманная вещь отброшены
    assert "tip" not in prompts[0]  # совет к погоде пишет код, не AI
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
        ["w_pack_ok"], ["w_pack_add"], ["m_wardrobe", "m_menu"]]  # «Пересобрать» нет — его закрывает «Добавить»
    monkeypatch.setattr(wardrobe_suitcase, "_clothes", lambda _cid: CLOTHES)
    monkeypatch.setattr(wardrobe_suitcase, "_weather", lambda _trip: {"tmin": 18, "tmax": 24, "rainy": 0})

    # «Пересобрать» — вещи чемодана кнопками; нажал — вещь уходит сразу, без «Подобрать замену».
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_again"))
    rows = bot.sent[-1]["reply_markup"].inline_keyboard
    assert bot.sent[-1]["text"].startswith("🧳 Что не берёшь? · Кейптаун · 8 дней")
    assert [[b.text for b in row] for row in rows] == [
        ["✅ Добавить ещё вещь"], ["Белая футболка"], ["⬅️ Назад", "#️⃣ Главная"]]
    assert [rows[0][0].callback_data, rows[1][0].callback_data, rows[2][0].callback_data] == [
        "w_pack_add", "w_pack_t:0", "w_pack_view"]
    import telegram_runtime

    def colors(markup):
        return [[(b["text"], b.get("style")) for b in row]
                for row in telegram_runtime._enhance_markup(markup)["inline_keyboard"]]

    assert colors(bot.sent[-1]["reply_markup"])[:2] == [[("Добавить ещё вещь", "success")],
                                                       [("Белая футболка", None)]]
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_t:0"))
    assert profile["suitcase_draft"]["result"]["groups"] == [] and profile["suitcase_draft"]["declined"] == [
        "Белая футболка"]  # ушла сразу и больше не предлагается
    # «Добавить вещь в чемодан» → категория → вещи шкафа; нажал — вещь в чемодане, зелёная.
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_add"))
    zones = bot.sent[-1]["reply_markup"].inline_keyboard
    assert bot.sent[-1]["text"].startswith("🧳 Добавить в чемодан · Кейптаун")
    assert [row[0].text for row in zones][:2] == ["Футболки и рубашки", "Брюки и шорты"]
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_z:0"))
    assert [row[0].text for row in bot.sent[-1]["reply_markup"].inline_keyboard][:2] == [
        "□ Белая футболка", "□ Серая футболка"]
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_a:0:1"))
    assert bot.sent[-1]["reply_markup"].inline_keyboard[1][0].text == "✅ Серая футболка"
    assert profile["suitcase_draft"]["result"]["groups"] == [("Верх", ["Серая футболка"])]
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_a:0:0"))  # отклонённую можно вернуть вручную
    assert profile["suitcase_draft"]["declined"] == []
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_a:0:0"))
    assert profile["suitcase_draft"]["declined"] == ["Белая футболка"]

    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_ok"))
    assert wardrobe_suitcase.saved_suitcase("42")["trip"]["city"] == "Кейптаун"
    assert bot.sent[-1]["text"] == ("🧳 Чемодан · Кейптаун · 20–27 октября · 8 дней\n\n"  # без «✅ Чемодан сохранён»
                                    "Футболки и рубашки (1 вещь): серая футболка\n\nВсего в списке: 1 вещь")
    assert [[b.callback_data for b in row] for row in bot.sent[-1]["reply_markup"].inline_keyboard] == [
        ["w_pack_add_s"], ["w_pack_looks"], ["w_pack_del"], ["m_wardrobe", "m_menu"]]
    assert wardrobe_suitcase.saved_suitcase("42")["declined"] == ["Белая футболка"]  # отказ помнится и после «Подтвердить»

    # «Собрать чемодан» дома теперь сразу открывает сохранённый чемодан, а не спрашивает о поездке.
    asyncio.run(wardrobe_suitcase.send(bot, "42"))
    assert bot.sent[-1]["text"].startswith("🧳 Чемодан · Кейптаун") and "сохранён" not in bot.sent[-1]["text"]
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

    assert "белая футболка" in bot.sent[-1]["text"] and "сохранён" not in bot.sent[-1]["text"]  # без новой сборки
    assert [[b.callback_data for b in row] for row in bot.sent[-1]["reply_markup"].inline_keyboard] == [
        ["w_pack_add_s"], ["w_pack_looks"], ["w_pack_del"], ["m_wardrobe", "m_menu"]]


def test_pack_button_at_home_and_on_vacation(monkeypatch):
    def callbacks(markup):
        return [b.callback_data for row in markup.inline_keyboard for b in row]

    monkeypatch.setattr(wardrobe_suitcase.vacation, "active", lambda _cid: False)
    monkeypatch.setattr(wardrobe_suitcase, "saved_suitcase", lambda _cid: {})
    assert callbacks(wardrobe.build_wardrobe_keyboard("42"))[2] == "w_pack"  # дома — под стилями
    labels = [[b.text for b in row] for row in wardrobe.build_wardrobe_keyboard("42").inline_keyboard]
    assert labels[1] == ["🧳 Собрать чемодан"]
    monkeypatch.setattr(wardrobe_suitcase, "saved_suitcase", lambda _cid: {"trip": TRIP, "result": {"groups": []}})
    labels = [[b.text for b in row] for row in wardrobe.build_wardrobe_keyboard("42").inline_keyboard]
    assert labels[1] == ["🧳 Чемодан в отпуск"]  # чемодан уже собран
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
    daily = [{"date": "2026-10-20", "tmax": 22, "tmin": 15, "rain": False, "wind": 3, "code": 1, "kind": "forecast"},
             {"date": "2026-10-21", "tmax": 19, "tmin": 13, "rain": True, "wind": 5, "code": 61, "kind": "forecast"}]

    looks = wardrobe_suitcase.daily_looks(closet, names, trip, daily)

    assert [look["date"] for look in looks] == ["2026-10-20", "2026-10-21", "2026-10-22"]
    assert looks[1]["tmax"] == 19 and looks[1]["rain"] and looks[1]["icon"] == "🌧️"
    assert looks[2]["tmax"] is None and looks[2]["icon"] == "🌡️"  # данных нет — без погоды
    assert all(len(look["items"]) == 3 and "Пиджак не брал" not in look["items"] for look in looks)
    assert set(looks[0]["items"]).isdisjoint(looks[1]["items"])  # вещи чередуются, а не повторяются подряд


def test_trip_weather_has_every_day_with_reliability_and_last_year_after_the_horizon(monkeypatch):
    import weather_provider
    calls = []

    class Response:
        def __init__(self, url, params):
            self.url, self.params = url, params

        def raise_for_status(self):
            pass

        def json(self):
            if "archive" in self.url:  # хвост поездки за горизонтом — те же дни год назад
                return {"daily": {"time": ["2025-11-03"], "temperature_2m_max": [25.1], "temperature_2m_min": [16],
                                  "precipitation_sum": [0], "windspeed_10m_max": [5], "weathercode": [1]}}
            days = 16
            return {"daily": {"time": [f"d{i}" for i in range(days)], "temperature_2m_max": [20 + i for i in range(days)],
                              "temperature_2m_min": [12] * days, "precipitation_probability_max": [80] * days,
                              "precipitation_sum": [4] * days, "windspeed_10m_max": [3] * days,
                              "weathercode": [61] * days}}

    monkeypatch.setattr(weather_provider.requests, "get",
                        lambda url, timeout=None, params=None: calls.append((url, params)) or Response(url, params))

    found = weather_provider.period_weather(1, 2, date(2026, 10, 18), date(2026, 11, 3), date(2026, 10, 18))

    daily = found["daily"]
    assert [day["date"] for day in daily][:2] == ["2026-10-18", "2026-10-19"] and len(daily) == 17  # без пропусков
    assert [day["kind"] for day in daily[:8]] == ["forecast"] * 8  # неделя вперёд — прогноз
    assert {day["kind"] for day in daily[8:16]} == {"early"}  # дальше — предварительно
    assert daily[16] == {"date": "2026-11-03", "tmax": 25, "tmin": 16, "rain": False, "wind": 5, "code": 1,
                         "kind": "typical"}
    assert calls[1][1]["start_date"] == "2025-11-03" and calls[1][1]["end_date"] == "2025-11-03"
    assert found["rainy"] == 16 and not found["typical"]  # сводка — по прогнозу, без хвоста прошлого года


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
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_t:0"))  # серая футболка — сразу из чемодана
    assert profile["suitcase_draft"]["result"]["groups"] == [("Низ", ["Джинсы"])]
    assert profile["suitcase_draft"]["declined"] == ["Белая футболка", "Серая футболка"]
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_swap"))  # старая кнопка — просто список вещей
    assert bot.sent[-1]["text"].startswith("🧳 Что не берёшь?")

    # Новая сборка на ту же поездку: отклонённых вещей не видит ни AI, ни запасной подбор.
    seen = []
    monkeypatch.setattr(wardrobe_suitcase, "_weather", lambda _trip: WARM)
    monkeypatch.setattr(wardrobe_suitcase, "_ai_pick", lambda _cid, clothes, *_a: seen.extend(
        item["name"] for _zone, item in clothes) or [])
    result = wardrobe_suitcase.pack("42", trip, declined=wardrobe_suitcase._declined_for("42", trip))
    names = [name for _zone, group in result["groups"] for name in group]
    assert "Белая футболка" not in seen and "Серая футболка" not in seen
    assert "Белая футболка" not in names and "Серая футболка" not in names


def test_pack_always_has_top_bottom_and_shoes(monkeypatch):
    monkeypatch.setattr(wardrobe_suitcase, "_clothes", lambda _cid: CLOTHES)
    monkeypatch.setattr(wardrobe_suitcase, "_weather", lambda _trip: WARM)
    monkeypatch.setattr(wardrobe_suitcase, "_ai_pick", lambda *_a: [0, 1])

    groups = dict(wardrobe_suitcase.pack("42", TRIP)["groups"])

    assert groups["Верх"] == ["Белая футболка", "Серая футболка"]
    assert groups["Низ"] == ["Бежевые чиносы"] and groups["Обувь"] == ["Белые кеды"]


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



def test_day_card_has_trip_head_day_weather_tiers_and_one_item_per_line():
    head = {"place": "ЮАР", "start": "2026-10-19", "end": "2026-11-04", "days": 17, "flag": "🇿🇦", "items": 12}
    look = {"date": "2026-10-19", "tmax": 18, "kind": "forecast", "icon": "🌧️", "rain": True, "socks": "Серые носки",
            "city": "Кейптаун",
            "items": ["бежевая футболка с оранжевым принтом", "Бежевые строгие брюки", "белые кеды"]}

    text = wardrobe_ui.suitcase_day_card(head, look, 0, 17, "2026-10-19").text

    # Без дат и температуры: день недели (поездка дольше недели — «день N»), пометка, только когда влияет на выбор,
    # вещи списком, как в «Надень сегодня», носки — последними.
    # В заголовке — страна и дни, в строке дня — город этого дня; внизу курсивом — чемодан.
    assert text == ("🇿🇦 Образы в отпуске · ЮАР · 17 дней\n\nПонедельник · день 1 · сегодня · Кейптаун\nДождь\n\n"
                    "- Бежевая футболка с оранжевым принтом\n- Бежевые строгие брюки\n- Белые кеды\n- Серые носки"
                    "\n\nТвой чемодан · 12 вещей · 17 образов")
    conditions = {kind: wardrobe_ui._day_condition({"tmax": 15, "wind": 9, "kind": kind})
                  for kind in ("forecast", "early", "typical")}
    assert conditions == {"forecast": "Прохладно, ветрено", "early": "", "typical": ""}  # дальше прогноза — без пометки
    assert wardrobe_ui._day_condition({"tmax": 21, "wind": 3, "kind": "forecast"}) == ""  # обычный день — без пометки
    assert wardrobe_ui._day_condition({"tmax": 30, "kind": "forecast"}) == "Жарко"


def test_early_and_typical_days_are_dressed_by_temperature_without_rain():
    for kind in ("early", "typical"):
        ctx = wardrobe_suitcase._day_context({"tmax": 15, "tmin": 9, "rain": True, "wind": 12, "kind": kind})
        assert not ctx["has_rain"] and not ctx["strong_wind"]
    assert wardrobe_suitcase._day_context({"tmax": 15, "rain": True, "kind": "forecast"})["has_rain"]


SAVED = {"trip": {**TRIP, "start": "2026-10-19", "end": "2026-10-21"},
         "result": {"place": "Кейптаун", "start": "2026-10-19", "end": "2026-10-21", "days": 3,
                    "groups": [("Верх", ["Белая футболка", "Серая футболка"]), ("Низ", ["Бежевые чиносы", "Джинсы"]),
                               ("Обувь", ["Белые кеды"])]}}
TRIP_CLOSET = {"zones": {
    "Верх": {"Все": [{"id": "w", "name": "Белая футболка", "zone": "Верх", "temp_range": [5, 35]},
                     {"id": "g", "name": "Серая футболка", "zone": "Верх", "temp_range": [5, 35]}]},
    "Низ": {"Все": [{"id": "c", "name": "Бежевые чиносы", "zone": "Низ", "temp_range": [5, 35]},
                    {"id": "j", "name": "Джинсы", "zone": "Низ", "temp_range": [5, 35]}]},
    "Обувь": {"Все": [{"id": "s", "name": "Белые кеды", "zone": "Обувь", "temp_range": [5, 35]}]}}}
TRIP_DAYS = [{"date": f"2026-10-{day}", "tmax": 21, "tmin": 15, "rain": False, "wind": 3, "code": 1,
              "kind": "forecast"} for day in (19, 20, 21)]


def _trip_env(monkeypatch, today=date(2026, 10, 20)):
    import copy
    profile = _memory_profile(monkeypatch)
    profile["suitcase_saved"] = copy.deepcopy(SAVED)
    fetched = []
    monkeypatch.setattr(wardrobe_suitcase, "_today", lambda: today)
    monkeypatch.setattr(wardrobe_suitcase, "_weather", lambda trip: fetched.append(trip) or {"daily": TRIP_DAYS})
    monkeypatch.setattr(wardrobe_suitcase.store, "load_wardrobe", lambda _cid: TRIP_CLOSET)
    monkeypatch.setattr(wardrobe_suitcase.vacation, "active", lambda _cid: True)
    monkeypatch.setattr(wardrobe_suitcase, "vacation_trip", lambda _cid: {**SAVED["trip"], "start": today.isoformat()})
    return profile, fetched


def test_plan_is_kept_while_paging_and_recomputed_next_day_or_after_suitcase_change(monkeypatch):
    profile, fetched = _trip_env(monkeypatch)

    first = wardrobe_suitcase.plan("42")
    assert len(first) == 3 and wardrobe_suitcase.plan("42") == first and len(fetched) == 1  # листание — без пересчёта

    monkeypatch.setattr(wardrobe_suitcase, "_today", lambda: date(2026, 10, 21))
    assert wardrobe_suitcase.plan("42", fetch=False) == first and len(fetched) == 1  # «Мой день» — без сети
    wardrobe_suitcase.plan("42")
    assert len(fetched) == 2  # новый день — свежий прогноз

    profile["suitcase_saved"]["result"]["groups"][0] = ("Верх", ["Белая футболка"])  # чемодан поменяли
    wardrobe_suitcase.plan("42")
    assert len(fetched) == 3


def test_day_card_pages_in_a_circle_from_the_suitcase_and_from_the_trip_wardrobe(monkeypatch):
    _trip_env(monkeypatch)
    bot = Bot()

    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_looks"))  # открывается на сегодняшнем дне
    rows = bot.sent[-1]["reply_markup"].inline_keyboard
    assert "Вторник · сегодня · Кейптаун" in bot.sent[-1]["text"]  # 3 дня — без «день N»
    assert [b.callback_data for b in rows[0]] == ["w_pack_day_0", "noop", "w_pack_day_2"] and rows[0][1].text == "2 из 3"
    assert [b.callback_data for b in rows[1]] == ["w_pack_saved", "m_menu"]

    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_day_2"))
    assert "Среда · Кейптаун\n" in bot.sent[-1]["text"]
    assert [b.callback_data for b in bot.sent[-1]["reply_markup"].inline_keyboard[0]] == [
        "w_pack_day_1", "noop", "w_pack_day_0"]  # по кругу

    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_trip_0"))
    assert [[b.callback_data for b in row] for row in bot.sent[-1]["reply_markup"].inline_keyboard] == [
        ["w_pack_trip_2", "noop", "w_pack_trip_1"], ["w_pack_alt_0"], ["w_pack"], ["w_closet", "m_menu"]]


def test_trip_wardrobe_opens_todays_planned_look_and_another_variant_replaces_it(monkeypatch):
    import wardrobe
    profile, _fetched = _trip_env(monkeypatch)
    monkeypatch.setattr(wardrobe, "send_looks", lambda *_a, **_k: pytest.fail("в отпуске с чемоданом — план"))
    bot = Bot()

    asyncio.run(wardrobe.send_home(bot, "42"))

    assert bot.sent[-1]["text"].startswith("🇿🇦 Образы в отпуске · Кейптаун · 3 дня\n\nВторник · сегодня · Кейптаун")
    today = profile["suitcase_saved"]["plan"]["looks"][1]["items"]
    summary = wardrobe.get_cached_outfit_summary("42")["items"]
    assert summary == [name[:1].lower() + name[1:] for name in today]  # «Мой день» — тот же образ

    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_alt_today"))
    changed = profile["suitcase_saved"]["plan"]["looks"][1]["items"]
    assert changed != today and len(changed) == 3  # другой комплект из вещей чемодана, сохранён в плане
    assert bot.sent[-1]["text"].count("сегодня") == 1


def test_trip_wardrobe_without_suitcase_keeps_the_daily_look(monkeypatch):
    import wardrobe
    _memory_profile(monkeypatch)
    monkeypatch.setattr(wardrobe_suitcase.vacation, "active", lambda _cid: True)
    calls = []

    async def looks(*_a, **_k):
        calls.append(1)

    monkeypatch.setattr(wardrobe, "send_looks", looks)
    asyncio.run(wardrobe.send_home(Bot(), "42"))
    assert calls == [1]


def test_suitcase_checklist_adds_missing_weather_items_or_names_the_gap():
    hot_trip = {"tmin": 16, "tmax": 34, "rainy": 3, "days": 17, "wind": 12,
                "daily": [{"tmin": 12}, {"tmin": 15}]}
    tops_only = [0, 2, 4]  # футболка, чиносы, кеды

    picked, gaps = wardrobe_suitcase._cover_weather(CLOTHES, tops_only, hot_trip)

    names = [CLOTHES[i][1]["name"] for i in picked]
    assert "Ветровка" in names and "Пуховик" not in names  # вечером +12° и ветер — слой из шкафа, по погоде
    assert gaps == ["шорт или лёгких брюк (днём до +34°)"]  # шорт в шкафу нет — «не хватает»
    # Три дождливых дня из 17 — не повод для непромокаемого.
    assert not wardrobe_suitcase._rainy_trip(hot_trip) and wardrobe_suitcase._rainy_trip({"rainy": 7, "days": 17})
    assert wardrobe_suitcase._weather_tip(hot_trip) == ("Днём до +34° — лёгкие вещи и защита от солнца, утром и "
                                                       "вечером до +12° и ветрено — кофта или ветровка под рукой.")

    text = wardrobe_ui.suitcase_screen({"place": "ЮАР", "days": 17, "groups": [
        ("Верх", ["Белая футболка", "Серая футболка"]), ("Обувь", ["Белые кеды"])],
        "wx": {"tmin": 16, "tmax": 34, "rainy": 3, "days": 13, "wind": 8},
        "gaps": gaps, "tip": "днём жарко, вечера ветреные"}).text
    # Погода и совет — одной строкой, дождливые дни — без «из 13 дней»; категории — «Обувь (1 пара): …».
    assert text == ("🧳 Чемодан · ЮАР · 17 дней\n\nДнём от +16° до +34°, 3 дождливых дня, ветер до 8 м/с. "
                    "Днём жарко, вечера ветреные.\n\n"
                    "Футболки и рубашки (2 вещи): белая футболка, серая футболка\n\nОбувь (1 пара): белые кеды\n\n"
                    "Не хватает: шорт или лёгких брюк (днём до +34°) — в шкафу таких нет.\n\n"
                    "Всего в списке: 2 вещи и 1 пара обуви")



def test_add_more_goes_straight_into_the_saved_suitcase(monkeypatch):
    profile = _memory_profile(monkeypatch)
    profile["suitcase_saved"] = {"trip": TRIP, "declined": ["Серая футболка"],
                                 "result": {"place": "Кейптаун", "days": 8, "groups": [("Верх", ["Белая футболка"])]}}
    monkeypatch.setattr(wardrobe_suitcase, "_clothes", lambda _cid: CLOTHES)
    monkeypatch.setattr(wardrobe_suitcase.vacation, "active", lambda _cid: False)
    bot = Bot()

    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_add_s"))
    zones = bot.sent[-1]["reply_markup"].inline_keyboard
    assert zones[0][0].callback_data == "w_pack_z:0:s" and zones[-1][0].callback_data == "w_pack_saved"
    # Видны все категории шкафа — и те, где всё уже в чемодане: там вещь можно убрать.
    assert [row[0].text for row in zones[:-1]] == ["Футболки и рубашки", "Брюки и шорты", "Куртки и ветровки",
                                                   "Обувь"]
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_z:0:s"))
    asyncio.run(wardrobe_suitcase.handle(bot, "42", "w_pack_a:0:1:s"))  # серая футболка — сразу в чемодан

    saved = wardrobe_suitcase.saved_suitcase("42")
    assert saved["result"]["groups"] == [("Верх", ["Белая футболка", "Серая футболка"])]
    assert saved["declined"] == [] and "suitcase_draft" not in profile  # без черновика и «Подтвердить»
    rows = bot.sent[-1]["reply_markup"].inline_keyboard
    assert rows[1][0].text == "✅ Серая футболка" and rows[-1][0].callback_data == "w_pack_add_s"


def test_raincoat_is_swapped_for_a_windbreaker_when_rain_is_rare():
    clothes = [("Верх", _item("Белая футболка", (15, 35))), ("Низ", _item("Джинсы")), ("Обувь", _item("Белые кеды")),
               ("Верхняя одежда", _item("Дождевик", (5, 25), rain_ok=True)),
               ("Верхняя одежда", _item("Чёрная ветровка", (5, 26)))]
    dry = {"tmin": 16, "tmax": 24, "rainy": 3, "days": 16, "wind": 8}

    picked = wardrobe_suitcase._without_raincoat(clothes, [0, 1, 2, 3], dry)
    assert [clothes[i][1]["name"] for i in picked] == ["Белая футболка", "Джинсы", "Белые кеды", "Чёрная ветровка"]
    assert wardrobe_suitcase._without_raincoat(clothes, [0, 3], {**dry, "rainy": 8}) == [0, 3]  # часто дождь
    only_raincoat = clothes[:4]
    assert wardrobe_suitcase._without_raincoat(only_raincoat, [0, 3], dry) == [0, 3]  # другой нет — остаётся
    # Слой на прохладные вечера чек-лист тоже берёт не дождевик.
    picked, _gaps = wardrobe_suitcase._cover_weather(clothes, [0, 1, 2], {**dry, "daily": [{"tmin": 10}]})
    assert clothes[picked[-1]][1]["name"] == "Чёрная ветровка"
