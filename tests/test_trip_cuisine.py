import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import day_menu
import vacation


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
    assert day_menu.get_cached_day_menu("42") is None
    menu["dishes"] = {"lunch": {"name": "Боботи"}}
    assert day_menu.get_cached_day_menu("42") is menu


def test_cuisine_fact_about_another_country_is_dropped():
    assert vacation._about_other_country("Традиционно корейцы едят рис, суп и панчханы.", "южноафриканская")
    assert not vacation._about_other_country("В ЮАР брай — почти национальный праздник.", "южноафриканская")
    assert not vacation._about_other_country("Корейцы едят кимчи почти каждый день.", "корейская")
    assert not vacation._about_other_country("Немцы любят колбаски.", "немецкая")
