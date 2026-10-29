import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import day_menu
import vacation

REAL_TRIP_FACTS = vacation.trip_facts  # conftest подменяет факты дня; здесь нужен настоящий сбор
REAL_TRIP_DISHES = vacation.trip_dishes
REAL_EATING_OUT = vacation.eating_out_line
REAL_FOOD_TIPS = vacation.food_tips
REAL_TRAVEL_PHRASES = vacation.travel_phrases
REAL_LOCAL_TZ = vacation.local_tz
REAL_COUNTRY_BRIEF = vacation.country_brief


def _setup(monkeypatch, ai_data, history=()):
    profile = {"cooking_day_menu_history": {"month": day_menu._today(None).strftime("%Y-%m"),
                                            "names": list(history), "by_cuisine": {"local_za": list(history)}}}
    monkeypatch.setattr(day_menu.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(day_menu, "_settings", lambda: type("S", (), {
        "food_meals": staticmethod(lambda _cid: ["breakfast", "lunch", "dinner"]),
        "food_restrictions": staticmethod(lambda _cid: []),
        "food_restrictions_prompt": staticmethod(lambda _cid: ""),
        "cuisines": staticmethod(lambda _cid: ["korean"]),
        "CUISINE_OPTIONS": [("korean", "Корейская")],
    })())
    monkeypatch.setattr(vacation, "active", lambda _cid: True)
    monkeypatch.setattr(vacation, "local_cuisine_label", lambda _cid: "Южноафриканская")
    monkeypatch.setattr(day_menu, "_ai_dishes", lambda *_a, **_k: ai_data)


def test_trip_dish_is_kept_even_if_shown_before(monkeypatch):
    _setup(monkeypatch, {"dinner": {"name": "Брай", "note": "мясо на углях"}, "top": "dinner"}, history=["Брай"])

    menu = day_menu.build_day_menu("42", cuisine="local_za")

    assert menu["cuisine"] == "local_za"
    assert day_menu.trip_dish(menu)["name"] == "Брай"  # повтор лучше пустого экрана
    assert set(menu["dishes"]) == {"dinner"}  # приёмы пищи без блюда пропущены, а не пустые


def test_cached_trip_menu_without_dish_is_rebuilt(monkeypatch):
    menu = {"date": day_menu._today(None).isoformat(), "cuisine": "local_za", "top": "dinner",
            "trip_v": day_menu._TRIP_MENU_VERSION, "dishes": {"dinner": {"name": ""}}}
    monkeypatch.setattr(day_menu.store, "get_profile", lambda _cid: {"cooking_day_menu": menu})
    monkeypatch.setattr(vacation, "active", lambda _cid: True)
    monkeypatch.setattr(vacation, "cuisine", lambda _cid: "local_za")
    assert day_menu.get_cached_day_menu("42") is None
    menu["dishes"] = {"lunch": {"name": "Боботи"}}
    assert day_menu.get_cached_day_menu("42") is menu


def test_cuisine_fact_about_another_country_is_dropped():
    assert vacation._about_other_country("Традиционно корейцы едят рис, суп и панчханы.", "южноафриканская")
    assert not vacation._about_other_country("В ЮАР брай — почти национальный праздник.", "южноафриканская")
    assert not vacation._about_other_country("Корейцы едят кимчи почти каждый день.", "корейская")
    assert not vacation._about_other_country("Немцы любят колбаски.", "немецкая")


def test_country_fact_of_day_in_my_day_summary():
    from ui import myday as myday_ui

    msg = myday_ui.day_summary("Ср, 22 окт", "Cape Town", vacation={
        "country_fact": "В ЮАР 11 официальных языков"})

    assert "💡 Факт дня: В ЮАР 11 официальных языков." in msg.text


def test_country_facts_keep_mentions_of_other_countries(monkeypatch):
    monkeypatch.setattr(vacation, "trip_facts", REAL_TRIP_FACTS)
    saved = {"cc": "ZA", "since": "2026-10-20", "until": "2026-10-30", "guide": {"cuisine_ru": "Южноафриканская"}}
    monkeypatch.setattr(vacation, "state", lambda _cid: saved)
    monkeypatch.setattr(vacation, "country_name_en", lambda _cid: "South Africa")
    monkeypatch.setattr(vacation, "_save", lambda _cid, value: saved.update(value))
    monkeypatch.setattr(vacation.util, "on_event_loop", lambda: False)
    prompts = []
    monkeypatch.setattr(vacation.ai, "llm_json", lambda prompt, *_a, **_k: prompts.append(prompt) or {
        "facts": ["Кейптаун основали голландцы в 1652 году"]})

    assert vacation.trip_facts("42", "country") == ["Кейптаун основали голландцы в 1652 году"]
    assert "South Africa (ZA)" in prompts[0] and "о самой стране" in prompts[0]


def test_cached_menu_of_another_cuisine_is_not_shown_on_trip(monkeypatch):
    home = {"date": day_menu._today(None).isoformat(), "cuisine": "korean",
            "dishes": {meal: {"name": f"Блюдо {meal}"} for meal in ("breakfast", "lunch", "dinner")}}
    monkeypatch.setattr(day_menu.store, "get_profile", lambda _cid: {"cooking_day_menu": home})
    monkeypatch.setattr(day_menu, "_settings", lambda: type("S", (), {
        "food_meals": staticmethod(lambda _cid: ["breakfast", "lunch", "dinner"])})())
    monkeypatch.setattr(vacation, "active", lambda _cid: True)
    monkeypatch.setattr(vacation, "cuisine", lambda _cid: "local_za")
    assert day_menu.get_cached_day_menu("42") is None  # домашнее меню до отпуска — собрать кухню страны
    monkeypatch.setattr(vacation, "active", lambda _cid: False)
    assert day_menu.get_cached_day_menu("42") is home


def test_trip_screen_without_dish_says_unavailable():
    from ui import menu as menu_ui

    msg = menu_ui.day_menu({"cuisine": "local_za", "dishes": {}}, cuisine_label="Южноафриканская",
                           flag="🇿🇦", trip=True, trip_dish={})
    assert "Блюдо дня сейчас недоступно" in msg.text and "Южноафриканская" in msg.text


def test_trip_dish_from_trip_set_when_ai_fails(monkeypatch):
    _setup(monkeypatch, {}, history=["Брай"])
    monkeypatch.setattr(vacation, "trip_dishes", lambda _cid: [
        {"name": "Брай", "original": "Braai", "emoji": "🔥", "note": "Мясо на углях."},
        {"name": "Боботи", "original": "Bobotie", "emoji": "🥘", "note": "Запеканка с фаршем и карри."},
    ])

    menu = day_menu.build_day_menu("42", cuisine="local_za")

    assert menu["cuisine"] == "local_za"
    assert day_menu.trip_dish(menu)["name"] == "Боботи"  # сначала не показанное
    assert day_menu.trip_dish(menu)["original"] == "Bobotie"


def test_trip_dishes_are_built_once_per_trip(monkeypatch):
    fresh = vacation
    monkeypatch.setattr(vacation, "trip_dishes", REAL_TRIP_DISHES)
    saved = {"cc": "ZA"}
    monkeypatch.setattr(fresh, "state", lambda _cid: saved)
    monkeypatch.setattr(fresh, "country_name_en", lambda _cid: "South Africa")
    monkeypatch.setattr(fresh, "_save", lambda _cid, value: saved.update(value))
    monkeypatch.setattr(fresh.util, "on_event_loop", lambda: False)
    calls = []
    monkeypatch.setattr(fresh.ai, "llm_json", lambda prompt, *_a, **_k: calls.append(prompt) or {"dishes": [
        {"name": "Билтонг", "original": "Biltong", "emoji": "🥩", "note": "Вяленое мясо."},
        {"name": "Biltong", "original": "", "emoji": "", "note": "латиница — не берём"},
    ]})

    assert [dish["name"] for dish in fresh.trip_dishes("42")] == ["Билтонг"]
    assert fresh.trip_dishes("42")[0]["original"] == "Biltong" and len(calls) == 1
    assert "South Africa (ZA)" in calls[0]


def test_country_card_in_my_day_only_on_first_day(monkeypatch):
    from datetime import date
    saved = {"cc": "ZA", "since": "2026-10-20"}
    monkeypatch.setattr(vacation, "state", lambda _cid: dict(saved))
    monkeypatch.setattr(vacation, "_save", lambda _cid, value: (saved.clear(), saved.update(value)))

    assert vacation.country_card_first_time("42", date(2026, 10, 20))
    assert vacation.country_card_first_time("42", date(2026, 10, 20))  # та же сводка, открыта снова
    assert not vacation.country_card_first_time("42", date(2026, 10, 21))

    saved.clear()
    saved.update({"cc": "ZA", "since": "2026-10-18"})  # поездка до появления отметки — справку уже видели
    assert not vacation.country_card_first_time("42", date(2026, 10, 23))


def test_trip_leisure_screen_starts_with_country_brief_and_useful():
    from ui import leisure as leisure_ui
    from ui import vacation as vacation_ui

    facts = {"country": {"capital": "Pretoria", "area": 1221037, "currency": "ZAR", "symbol": "R",
                         "calling": "+27"},
             "population": {"value": 64_700_000, "year": 2025}, "rate": 18.58, "hours": 1}
    guide = {"tips": {"plugs": "тип M, 230 В", "emergency": "112"}}
    brief = {"capital_ru": "Претория", "currency_ru": "ранд", "emergency": "112", "police": "10111",
             "ambulance": "10177"}
    about, useful = vacation_ui.trip_brief(facts, guide, brief)
    footer = vacation_ui.holiday_line({"name": "День свободы", "date": "27 апреля",
                                       "about": "В этот день в 1994 году прошли первые всеобщие выборы."})
    msg = leisure_ui.trip_leisure_screen("ЮАР", {}, flag="🇿🇦", about=about, useful=useful, footer=footer)
    assert msg.text == (
        "🇿🇦 Досуг в отпуске · ЮАР\n\n"
        "Коротко о стране:\n• Столица - Претория\n• Население 64,7 млн (2025)\n• Площадь 1 221 037 км²\n"
        "• Валюта - ранд (ZAR) · 1 € ≈ 18,58 R\n• Время на 1 час больше, чем в Амстердаме\n• Телефонный код +27\n\n"
        "Полезно знать:\n• Экстренный номер: 112\n• Полиция: 10111\n• Скорая помощь: 10177\n"
        "• Розетки: тип M · 230 В\n\n"
        "День свободы · 27 апреля · В этот день в 1994 году прошли первые всеобщие выборы."
    )


def test_my_data_screen_opens_own_data_and_library_on_trip(monkeypatch):
    import asyncio
    import settings
    shown = []

    async def show(_bot, _cid, msg, reply_markup=None, query=None):
        shown.append((msg, reply_markup))

    monkeypatch.setattr(settings.rich_delivery, "show", show)
    monkeypatch.setattr(settings.store, "get_learning_language", lambda _cid: "nl")
    asyncio.run(settings.handle_callback(None, "42", "set_mydata"))
    msg, markup = shown[-1]
    assert msg.text.startswith("🗂 Мои данные")
    assert [row[0].callback_data for row in markup.inline_keyboard] == [
        "w_closet", "as_fridge_home", "a_dictlang_nl", "set_mylib", "set_home"]

    import leisure_hub
    sent = []

    async def hub_show(_bot, _cid, msg, _q=None):
        sent.append(msg)

    monkeypatch.setattr(leisure_hub, "_show", hub_show)
    asyncio.run(settings.handle_callback(None, "42", "set_mylib"))
    rows = sent[-1].reply_markup.inline_keyboard
    assert sent[-1].text.startswith("🎚️ Моя библиотека") and rows[-1][0].callback_data == "set_mydata"


def test_trip_food_screen_footer_is_tipping_and_rate():
    from ui import menu as menu_ui

    msg = menu_ui.day_menu({"cuisine": "local_za", "dishes": {}}, cuisine_label="Южноафриканская", flag="🇿🇦",
                           trip=True, trip_dish={"name": "Банни чоу", "note": "хлеб с карри"},
                           fact="Бильтонг — вяленое мясо", trip_stats={"day": 4, "days": 14},
                           eating_out="Чаевые: 10–15 % в кафе · 1 € = 18,51 R")
    assert msg.text.endswith("Чаевые: 10–15 % в кафе · 1 € = 18,51 R") and "Бильтонг" not in msg.text
    assert menu_ui.trip_food_stats({"day": 2}) == "День 2"


def test_eating_out_line_rotates_tips_by_trip_day(monkeypatch):
    from datetime import date
    monkeypatch.setattr(vacation, "eating_out_line", REAL_EATING_OUT)
    monkeypatch.setattr(vacation, "state", lambda _cid: {"cc": "ZA", "since": "2026-10-20"})
    monkeypatch.setattr(vacation, "guide", lambda _cid: {"tips": {"tipping": "10–15 % в кафе.",
                                                                  "water": "Обычно можно пить."}})
    monkeypatch.setattr(vacation, "_cached", lambda *_a, **_k: {"currency": "ZAR", "symbol": "R"})
    monkeypatch.setattr(vacation, "_eur_rate", lambda _currency: 18.514)
    monkeypatch.setattr(vacation, "food_tips", lambda _cid: {"check": "150–250 R, около 8–14 €",
                                                             "hours": "обед с 12 до 14, ужин после 19"})
    days = [vacation.eating_out_line("42", date(2026, 10, 20 + index)) for index in range(5)]
    assert days == [
        "Чаевые: 10–15 % в кафе · 1 € = 18,51 R",
        "Средний чек в кафе: 150–250 R, около 8–14 €",
        "Вода из-под крана: обычно можно пить",
        "Едят местные: обед с 12 до 14, ужин после 19",
        "Чаевые: 10–15 % в кафе · 1 € = 18,51 R",
    ]


def test_food_tips_without_digits_are_dropped(monkeypatch):
    real = REAL_FOOD_TIPS
    monkeypatch.setattr(vacation, "food_tips", real)
    saved = {"cc": "ZA"}
    monkeypatch.setattr(vacation, "state", lambda _cid: saved)
    monkeypatch.setattr(vacation, "country_name_en", lambda _cid: "South Africa")
    monkeypatch.setattr(vacation, "_save", lambda _cid, value: saved.update(value))
    monkeypatch.setattr(vacation.util, "on_event_loop", lambda: False)
    monkeypatch.setattr(vacation.ai, "llm_json", lambda *_a, **_k: {"check": "недорого", "hours": "ужин после 19"})
    assert vacation.food_tips("42") == {"hours": "ужин после 19"}

def test_travel_phrases_built_for_whole_trip_in_batches_and_five_per_day(monkeypatch):
    from datetime import date
    monkeypatch.setattr(vacation, "travel_phrases", REAL_TRAVEL_PHRASES)
    saved = {"cc": "ZA", "since": "2026-10-20", "until": "2026-10-29"}  # 10 дней — 50 фраз, два запроса
    monkeypatch.setattr(vacation, "state", lambda _cid: saved)
    monkeypatch.setattr(vacation, "_save", lambda _cid, value: saved.update(value))
    monkeypatch.setattr(vacation, "guide", lambda _cid: {"language_ru": "Африкаанс"})
    monkeypatch.setattr(vacation, "country_name_en", lambda _cid: "South Africa")
    monkeypatch.setattr(vacation.util, "on_event_loop", lambda: False)
    prompts = []

    def fake(prompt, *_a, **_k):
        start = len(prompts) * 25
        prompts.append(prompt)
        return {"phrases": [{"text": f"Frase {start + i}", "ru": f"Фраза {start + i}", "say": "ф"} for i in range(25)]}

    monkeypatch.setattr(vacation.ai, "llm_json", fake)
    items = vacation.travel_phrases("42")
    assert len(items) == 50 and len(prompts) == 2
    assert "Не базовые приветствия" in prompts[0] and "Не повторяй" in prompts[1] and "Frase 0" in prompts[1]
    assert vacation.travel_phrases("42") == items and len(prompts) == 2  # запас уже на весь отпуск — без запросов
    day1 = vacation.phrases_today("42", date(2026, 10, 20))
    day2 = vacation.phrases_today("42", date(2026, 10, 21))
    assert [p["text"] for p in day1] == [f"Frase {i}" for i in range(5)]
    assert [p["text"] for p in day2] == [f"Frase {i}" for i in range(5, 10)]


def test_trip_learning_screen_has_no_situation_line():
    from ui import menu as menu_ui

    msg = menu_ui._trip_learning({"flag": "🇿🇦", "language": "Африкаанс", "phrases": [
        {"text": "Hoeveel kos dit?", "ru": "Сколько это стоит?", "say": "хуфеел кос дит"}]})
    assert msg.text == "🇿🇦 Обучение в отпуске · Африкаанс\n\nHoeveel kos dit? → Сколько это стоит? (хуфеел кос дит)"
    utf16 = msg.text.encode("utf-16-le")
    italic = [utf16[2 * e.offset:2 * (e.offset + e.length)].decode("utf-16-le") for e in msg.entities if e.type == "italic"]
    assert italic == ["(хуфеел кос дит)"]
    quoted = menu_ui._trip_learning({"language": "Африкаанс", "phrases": [{"text": "Ja", "ru": "Да"}],
                                     "language_fact": "«Слово «апартеид» — из африкаанс»."})
    assert quoted.text.endswith("\n\nСлово «апартеид» — из африкаанс.")  # внешние кавычки сняты


def test_languages_fact_is_last_in_language_facts_rotation(monkeypatch):
    from datetime import date
    monkeypatch.setattr(vacation, "state", lambda _cid: {"cc": "ZA", "since": "2026-10-20"})
    monkeypatch.setattr(vacation, "_cached", lambda *_a, **_k: {"languages": ["Afrikaans", "English", "Zulu"]})
    monkeypatch.setattr(vacation, "guide", lambda _cid: {"languages_ru": {"Afrikaans": "африкаанс",
                                                                          "English": "английский", "Zulu": "зулу"}})
    monkeypatch.setattr(vacation, "trip_facts", lambda _cid, _topic: ["Факт 1", "Факт 2"])
    days = [vacation.language_fact_today("42", date(2026, 10, 20 + index)) for index in range(4)]
    assert days == ["Факт 1", "Факт 2", "ЮАР: 3 официальных языка — африкаанс, английский, зулу", "Факт 1"]


def test_country_brief_keeps_only_digit_numbers_and_holidays(monkeypatch):
    monkeypatch.setattr(vacation, "country_brief", REAL_COUNTRY_BRIEF)
    saved = {"cc": "ZA", "since": "2026-10-20"}
    monkeypatch.setattr(vacation, "state", lambda _cid: saved)
    monkeypatch.setattr(vacation, "country_name_en", lambda _cid: "South Africa")
    monkeypatch.setattr(vacation, "_save", lambda _cid, value: saved.update(value))
    monkeypatch.setattr(vacation.util, "on_event_loop", lambda: False)
    monkeypatch.setattr(vacation.util, "build_off_loop", lambda *_a: None)
    monkeypatch.setattr(vacation.ai, "llm_json", lambda *_a, **_k: {
        "capital_ru": "Претория", "currency_ru": "ранд", "emergency": "112", "police": "10111",
        "ambulance": "скорая", "holidays": [{"name": "День свободы", "date": "27 апреля", "about": "Выборы 1994."},
                                            {"name": "День наследия", "date": "24 сентября", "about": ""}]})
    brief = vacation.country_brief("42")
    assert brief["police"] == "10111" and brief["ambulance"] == ""  # не цифры — пусто
    from datetime import date
    assert vacation.holiday_of_day("42", date(2026, 10, 21))["name"] == "День наследия"  # по кругу дней


def test_my_day_trip_lines_are_short():
    from ui import myday as myday_ui

    long_fact = ("Столовая гора в Кейптауне — одна из старейших гор мира, ей около 260 миллионов лет, "
                 "и она входит в число новых семи чудес природы")
    msg = myday_ui.day_summary("Сб, 24 окт", "Cape Town", vacation={
        "try_dish": {"name": "Банни чоу", "note": "хлеб с карри"}, "country_fact": long_fact})
    assert "🍽 Попробовать: Банни чоу — хлеб с карри.\n" in msg.text
    assert myday_ui._short_note("Хлеб в виде лодочки, наполненный ароматным карри (обычно говядина или курица), "
                                "популярно в Дурбане.") == "хлеб в виде лодочки"
    assert "💡 Факт дня: Столовая гора в Кейптауне — одна из старейших гор мира, ей около 260 миллионов лет." in msg.text


def test_sunset_and_golden_hour_use_trip_timezone(monkeypatch):
    from datetime import date
    from zoneinfo import ZoneInfo
    import sun
    import weather

    cape = ZoneInfo("Africa/Johannesburg")
    # Прогноз — во времени Амстердама (после перевода часов UTC+1); в Кейптауне UTC+2 — на час больше.
    assert weather._clock("2026-10-25T18:12", cape) == "19:12"
    assert weather._clock("2026-10-25T18:12") == "18:12"
    golden_cape = sun.evening_golden_range(-33.92, 18.42, date(2026, 10, 25), cape)
    golden_ams = sun.evening_golden_range(-33.92, 18.42, date(2026, 10, 25), weather.TZ)
    assert golden_cape and golden_ams and golden_cape != golden_ams


def test_local_tz_is_home_timezone_when_not_on_trip(monkeypatch):
    monkeypatch.setattr(vacation, "local_tz", REAL_LOCAL_TZ)
    monkeypatch.setattr(vacation, "active", lambda _cid: False)
    assert vacation.local_tz("42") is vacation.config.TZ
    monkeypatch.setattr(vacation, "active", lambda _cid: True)
    monkeypatch.setattr(vacation.store, "get_settings", lambda _cid: {"lat": -33.92, "lon": 18.42})
    monkeypatch.setattr(vacation, "_cached", lambda *_a, **_k: "Africa/Johannesburg")
    assert str(vacation.local_tz("42")) == "Africa/Johannesburg"


def test_my_day_title_says_which_trip_day():
    from datetime import date
    import myday
    from ui import myday as myday_ui

    assert myday._trip_day({"since": "2026-10-19"}, date(2026, 10, 26)) == 8
    assert myday._trip_day({}, date(2026, 10, 26)) == 0
    msg = myday_ui.day_summary("Пн, 26 октября", "Кейптаун", country="ZA", flag="🇿🇦", vacation={"day": 8})
    assert msg.text.startswith("Мой 8 день в отпуске · Пн, 26 октября · Кейптаун")


def test_trip_start_is_earliest_of_route_suitcase_and_mode(monkeypatch):
    import wardrobe_suitcase
    from datetime import date
    monkeypatch.setattr(vacation, "state", lambda _cid: {
        "cc": "ZA", "since": "2026-10-22",  # режим включили заново после смены маршрута
        "legs": [{"city": "Кейптаун", "start": "2026-10-19"}, {"city": "Йоханнесбург", "start": "2026-10-27"}]})
    monkeypatch.setattr(wardrobe_suitcase, "saved_suitcase", lambda _cid: {"trip": {"start": "2026-10-20", "cc": "ZA"}})
    assert vacation.trip_start("42") == date(2026, 10, 19)


def test_restarting_trip_keeps_its_start(monkeypatch):
    import asyncio
    saved = {"home": {"city": "Alkmaar"}, "cc": "ZA", "since": "2026-10-19"}
    monkeypatch.setattr(vacation, "state", lambda _cid: dict(saved))
    monkeypatch.setattr(vacation, "_save", lambda _cid, value: (saved.clear(), saved.update(value)))
    monkeypatch.setattr(vacation.store, "get_settings", lambda _cid: {"cc": "ZA"})
    monkeypatch.setattr(vacation, "_reset_day_caches", lambda _cid: None)

    async def finish(*_a, **_k):
        return None

    monkeypatch.setattr(vacation, "_finish", finish)
    asyncio.run(vacation._start(None, "42", {"city": "Alkmaar"}, "2026-11-03", legs=[{"city": "Йоханнесбург"}]))
    assert saved["since"] == "2026-10-19"
