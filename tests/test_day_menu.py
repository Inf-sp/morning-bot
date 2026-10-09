import asyncio
import os
from datetime import date, datetime
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import bot_callbacks
import config
import day_menu
import menu
import recipe_generation
from ui import menu as menu_ui

NOW = datetime(2026, 10, 9, 9, 0, tzinfo=config.TZ)


def _profile(monkeypatch, profile=None, fridge=("яйца", "мука")):
    profile = {} if profile is None else profile

    def mutate(_cid, fn):
        fn(profile)

    monkeypatch.setattr(day_menu.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(day_menu.store, "mutate_profile", mutate)
    monkeypatch.setattr(recipe_generation, "_home_idea_context",
                        lambda _cid, now=None, cuisine=None: {"available": list(fridge)})
    return profile


def _ai(monkeypatch, answer):
    prompts = []

    def fake(prompt, *_a, **_k):
        prompts.append(prompt)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(day_menu.ai, "llm_json", fake)
    return prompts


def test_cuisine_of_the_day_rotates_through_preferences(monkeypatch):
    monkeypatch.setattr(day_menu._settings(), "cuisines", lambda _cid: ["italian", "georgian"])
    first = day_menu.day_cuisine("42", date(2026, 10, 9))
    second = day_menu.day_cuisine("42", date(2026, 10, 10))
    assert {first, second} == {"italian", "georgian"}

    monkeypatch.setattr(day_menu._settings(), "cuisines", lambda _cid: [])
    assert day_menu.day_cuisine("42", date(2026, 10, 9)) in day_menu.CUISINE_INTRO


def test_ai_menu_is_validated_and_saved_with_history(monkeypatch):
    profile = _profile(monkeypatch)
    prompts = _ai(monkeypatch, {
        "breakfast": {"name": "Crostata", "note": "песочный пирог с джемом."},
        "lunch": {"name": "Pasta al pomodoro", "note": "паста с томатами"},
        "dinner": {"name": "Crostata", "note": "дубль — должен замениться"},
    })

    menu_data = day_menu.get_day_menu("42", now=NOW, cuisine="italian")

    dishes = menu_data["dishes"]
    assert dishes["breakfast"] == {"name": "Crostata", "note": "песочный пирог с джемом"}
    assert dishes["dinner"]["name"] != "Crostata"  # повтор в одном меню заменён типичным блюдом
    assert dishes["dinner"]["name"] in {name for name, _ in recipe_generation.TYPICAL_DISHES["italian"]["dinner"]}
    assert "Итальянская" in prompts[0] and "яйца" in prompts[0]
    assert profile["cooking_day_menu"]["date"] == "2026-10-09"
    assert "Crostata" in profile["cooking_day_menu_history"]["names"]
    assert day_menu.get_cached_day_menu("42", now=NOW) == menu_data


def test_menu_without_ai_uses_typical_dishes(monkeypatch):
    _profile(monkeypatch)
    _ai(monkeypatch, RuntimeError("AI down"))

    dishes = day_menu.get_day_menu("42", now=NOW, cuisine="georgian")["dishes"]

    for meal in day_menu.MEALS:
        typical = dict(recipe_generation.TYPICAL_DISHES["georgian"][meal])
        assert dishes[meal]["name"] in typical and dishes[meal]["note"] == typical[dishes[meal]["name"]]


def test_other_menu_does_not_repeat_current_dishes(monkeypatch):
    _profile(monkeypatch)
    _ai(monkeypatch, RuntimeError("AI down"))
    first = day_menu.get_day_menu("42", now=NOW, cuisine="russian")
    second = day_menu.get_day_menu("42", now=NOW, refresh=True, cuisine="russian")

    first_names = {dish["name"] for dish in first["dishes"].values()}
    assert not first_names & {dish["name"] for dish in second["dishes"].values()}


def test_day_menu_screen_lists_three_dishes_without_recipes():
    msg = menu_ui.day_menu({"dishes": {
        "breakfast": {"name": "Crostata", "note": "Песочный пирог с джемом"},
        "lunch": {"name": "Pasta al pomodoro", "note": "паста с томатами"},
        "dinner": {"name": "Lasagna", "note": ""},
    }}, cuisine_label="Итальянская", intro="Свежие продукты и простые сочетания.")

    assert msg.text == (
        "🍳 Меню на сегодня · Итальянская кухня\n\n"
        "Свежие продукты и простые сочетания.\n\n"
        "Завтрак: Crostata — песочный пирог с джемом\n\n"
        "Обед: Pasta al pomodoro — паста с томатами\n\n"
        "Ужин: Lasagna"
    )
    rows = [[(b.text, b.callback_data) for b in row] for row in msg.reply_markup.inline_keyboard]
    assert rows == [
        [("Завтрак", "a_recipe_breakfast"), ("Обед", "a_recipe_lunch"), ("Ужин", "a_recipe_dinner")],
        [("✨ Новое меню", "food_pick")],
        [("🎚️ Настроить", "as_fridge_home"), ("#️⃣ Главная", "m_menu")],
    ]


def test_other_menu_picker_lists_cuisines_and_starts_new_day_menu(monkeypatch):
    kb = menu_ui.food_cuisine_kb([("italian", "🍕 Итальянская")]).inline_keyboard
    assert [(row[0].text, row[0].callback_data) for row in kb[:-1]] == [("🍕 Итальянская", "food_go_day_italian")]
    assert kb[-1][0].callback_data == "food_card"

    calls = []

    async def send_food_menu(_bot, cid, **kwargs):
        calls.append((kwargs.get("meal"), kwargs["cuisine"], kwargs["refresh"]))

    async def status_call(call, **_kw):
        return await call(None)

    monkeypatch.setattr(bot_callbacks.menu, "send_food_menu", send_food_menu)
    for data in ("food_go_day_georgian", "food_go_lunch_any"):  # новая кнопка и кнопка старого сообщения
        asyncio.run(bot_callbacks._food_recipe(SimpleNamespace(bot=None, cid="42", data=data, status=status_call)))

    assert calls == [(None, "georgian", True), (None, None, True)]


def test_meal_button_builds_recipe_of_that_menu_dish(monkeypatch):
    _profile(monkeypatch, {"cooking_day_menu": {
        "date": datetime.now(config.TZ).date().isoformat(), "cuisine": "italian",
        "dishes": {"breakfast": {"name": "Crostata", "note": "пирог"},
                   "lunch": {"name": "Pasta al pomodoro", "note": ""},
                   "dinner": {"name": "Lasagna", "note": ""}},
    }})
    requested, shown = [], []

    def recipe(cid, now=None, refresh=False, cuisine=None, dish=None):
        requested.append((now.hour, cuisine, dish["name"]))
        return {"name": dish["name"], "cuisine": cuisine, "ingredients": ["паста", "томаты"],
                "steps": [{"text": "Свари пасту", "minutes": 10}]}

    class Status:
        async def replace(self, text, **kwargs):
            shown.append((text, kwargs["reply_markup"]))

    monkeypatch.setattr(recipe_generation, "get_cooking_home_idea", recipe)

    asyncio.run(menu.send_food_menu(object(), "42", meal="lunch", status=Status()))

    assert requested == [(13, "italian", "Pasta al pomodoro")]
    text, markup = shown[0]
    assert text.startswith("🍳 Что приготовить на обед · Итальянская кухня\n\nPasta al pomodoro")
    assert [b.callback_data for b in markup.inline_keyboard[0]] == ["m_food", "m_menu"]


def test_recipe_cache_is_reused_only_for_the_same_menu_dish(monkeypatch):
    ready = {"name": "Crostata", "dish": "Crostata", "cuisine": "italian"}
    monkeypatch.setattr(recipe_generation, "_home_idea_context",
                        lambda _cid, now=None, cuisine=None: {"meal": "breakfast", "signature": "s"})
    monkeypatch.setattr(recipe_generation.store, "get_profile", lambda _cid: {})
    monkeypatch.setattr(recipe_generation, "get_cached_cooking_home_idea", lambda *_a, **_k: ready)

    assert recipe_generation.get_cooking_home_idea("42", dish={"name": "Crostata"}) is ready
