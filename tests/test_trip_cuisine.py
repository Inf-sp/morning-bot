import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import day_menu
import vacation

REAL_TRIP_FACTS = vacation.trip_facts  # conftest подменяет факты дня; здесь нужен настоящий сбор
REAL_TRIP_DISHES = vacation.trip_dishes


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


def test_trip_leisure_screen_starts_with_country():
    from ui import leisure as leisure_ui

    msg = leisure_ui.trip_leisure_screen("ЮАР", {"films": [{"title": "Tsotsi"}]}, flag="🇿🇦",
                                         country=[("Столица", "Pretoria"), ("Валюта", "ZAR (R)")])
    assert msg.text.startswith("🇿🇦 Досуг в отпуске · ЮАР\n\nСтрана:\nСтолица: Pretoria\nВалюта: ZAR (R)\n\n")


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


def test_trip_food_screen_footer_is_trip_stats():
    from ui import menu as menu_ui

    msg = menu_ui.day_menu({"cuisine": "local_za", "dishes": {}}, cuisine_label="Южноафриканская", flag="🇿🇦",
                           trip=True, trip_dish={"name": "Банни чоу", "note": "хлеб с карри"},
                           fact="Бильтонг — вяленое мясо", trip_stats={"day": 4, "days": 14, "dishes": 6})
    assert msg.text.endswith("День 4 из 14 · подобрано 6 местных блюд")
    assert "Бильтонг" not in msg.text
    assert menu_ui.trip_food_stats({"day": 2, "dishes": 1}) == "День 2 · подобрано 1 местное блюдо"
