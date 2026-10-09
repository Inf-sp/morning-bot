import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import menu
import recipe_generation
import util
import bot_callbacks


def test_home_meal_time_windows():
    assert recipe_generation._home_meal_for_hour(5) == "dinner"
    assert recipe_generation._home_meal_for_hour(6) == "breakfast"
    assert recipe_generation._home_meal_for_hour(10) == "breakfast"
    assert recipe_generation._home_meal_for_hour(11) == "lunch"
    assert recipe_generation._home_meal_for_hour(15) == "lunch"
    assert recipe_generation._home_meal_for_hour(16) == "dinner"
    assert recipe_generation._home_meal_for_hour(21) == "dinner"


def test_month_recipe_pool_only_uses_current_profile_and_month():
    context = {
        "meal": "breakfast",
        "month": "2026-08",
        "pool_signature": "current-profile",
    }
    profile = {
        "cooking_home_month_pools": {
            "breakfast": {
                "month": "2026-08",
                "signature": "current-profile",
                "ideas": [{"name": "Сырники"}, {"name": "Омлет"}],
            },
        },
    }

    assert [item["name"] for item in recipe_generation._home_month_pool(profile, context)] == [
        "Сырники", "Омлет",
    ]
    assert recipe_generation._home_month_pool(
        profile, {**context, "month": "2026-09"},
    ) == []
    assert recipe_generation._home_month_pool(
        profile, {**context, "pool_signature": "changed-fridge"},
    ) == []


def test_other_recipe_refreshes_current_meal_in_inline_status(monkeypatch):
    calls, generated = [], []

    class Status:
        async def replace(self, text, **kwargs):
            calls.append((text, kwargs))

    def generate(cid, now, refresh, cuisine=None):
        generated.append((cid, recipe_generation.current_meal(now), refresh))
        return {"name": "Шакшука", "cuisine": "mediterranean", "ingredients": ["яйца", "томаты"],
            "steps": [{"text": "Обжарь томаты", "minutes": 5}], "tip": "Посоли в конце."}

    monkeypatch.setattr(menu, "has_available_fridge", lambda _cid: True)
    monkeypatch.setattr(recipe_generation, "get_cooking_home_idea", generate)

    asyncio.run(menu.send_food_menu(object(), "42", refresh=True, status=Status()))

    assert generated == [("42", recipe_generation.current_meal(), True)]
    text, kwargs = calls[0]
    assert text.startswith("🍳 Что приготовить на ") and "Шакшука" in text
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert labels[0] == "✨ Новый рецепт"


def test_food_home_serves_cached_day_recipe_instantly(monkeypatch):
    shown = []

    class Status:
        async def replace(self, text, **_kwargs):
            shown.append(text)

    def no_generation(*_args, **_kwargs):
        raise AssertionError("cached recipe must not be regenerated")

    monkeypatch.setattr(menu, "has_available_fridge", lambda _cid: True)
    monkeypatch.setattr(recipe_generation, "get_cached_cooking_home_idea", lambda *_a, **_k: {"name": "Шакшука", "cuisine": "mediterranean", "ingredients": ["яйца", "томаты"],
            "steps": [{"text": "Обжарь томаты", "minutes": 5}], "tip": "Посоли в конце."})
    monkeypatch.setattr(recipe_generation, "get_cooking_home_idea", no_generation)

    import time
    started = time.monotonic()
    asyncio.run(menu.send_food_menu(object(), "42", status=Status()))

    assert time.monotonic() - started < 0.1
    assert "Шакшука" in shown[0]


def test_recipe_header_follows_time_of_day():
    for meal, words in (("breakfast", "на завтрак"), ("lunch", "на обед"), ("dinner", "на ужин")):
        text = menu.menu_ui.food_menu({"name": "Шакшука", "cuisine": "mediterranean", "ingredients": ["яйца", "томаты"],
            "steps": [{"text": "Обжарь томаты", "minutes": 5}], "tip": "Посоли в конце."}, meal=meal).text
        assert text.startswith(f"🍳 Что приготовить {words} · ")


def test_warm_prepares_three_day_recipes(monkeypatch):
    meals = []
    monkeypatch.setattr(
        recipe_generation, "get_cooking_home_idea",
        lambda cid, now=None, refresh=False: meals.append(recipe_generation.current_meal(now)) or {"name": "x"},
    )

    assert recipe_generation.warm_cooking_home_ideas("42") == {
        "breakfast": True, "lunch": True, "dinner": True,
    }
    assert meals == ["breakfast", "lunch", "dinner"]


def test_old_what_to_cook_button_opens_the_recipe_home():
    assert bot_callbacks._legacy_alias("m_food_gen") == "m_food"


def test_opening_food_serves_the_day_restaurant_card(monkeypatch):
    """Обычное открытие «Готовки» показывает рецепт дня без принудительной пересборки."""
    calls = []

    class Status:
        async def stop(self, delete=True):
            return None

    async def start_inline(*_args, **_kwargs):
        return Status()

    async def send_food_menu(_bot, _cid, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(bot_callbacks.menu, "send_food_menu", send_food_menu)
    query = SimpleNamespace(
        data="m_food", message=SimpleNamespace(chat_id="42", message_id=1),
    )
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(bot=object())

    asyncio.run(bot_callbacks.handle(update, context, lambda *_args: None))

    assert calls[0].get("refresh", False) is False


def test_dinner_button_uses_dinner_cache_without_forced_refresh(monkeypatch):
    calls = []

    class Status:
        async def stop(self, delete=True):
            return None

    async def start_inline(*_args, **_kwargs):
        return Status()

    async def send_food_menu(_bot, _cid, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(bot_callbacks.menu, "send_food_menu", send_food_menu)
    query = SimpleNamespace(
        data="a_recipe_dinner",
        message=SimpleNamespace(chat_id="42", message_id=1),
    )
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(bot=object())

    asyncio.run(bot_callbacks.handle(update, context, lambda *_args: None))

    assert calls[0]["meal"] == "dinner"
    assert calls[0]["refresh"] is False


def _recipe(name):
    return {"name": name, "minutes": 10, "ingredients": ["яйца"],
            "steps": [{"text": "Взбей яйца", "minutes": 10}], "reason": "Быстро.", "tip": "Посоли."}


def _refresh_with(monkeypatch, llm_name, local_name):
    context = {"meal": "breakfast", "month": "2026-10", "pool_signature": "p", "signature": "s"}
    profile = {
        "cooking_home_ideas": {"breakfast": {"signature": "s", "idea": _recipe("Омлет")}},
        "cooking_home_month_pools": {"breakfast": {
            "month": "2026-10", "signature": "p", "ideas": [_recipe("Сырники"), _recipe("Омлет")],
        }},
    }
    monkeypatch.setattr(recipe_generation, "_home_idea_context", lambda _cid, now=None, cuisine=None: context)
    monkeypatch.setattr(recipe_generation.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(recipe_generation.store, "mutate_profile", lambda *_args: None)
    monkeypatch.setattr(recipe_generation, "_recipe_sources", lambda *_args, **_kw: [])
    monkeypatch.setattr(recipe_generation, "_normalize_home_idea", lambda data, _ctx: dict(data))
    monkeypatch.setattr(recipe_generation.ai, "llm_json", lambda *_args, **_kw: _recipe(llm_name))
    monkeypatch.setattr(recipe_generation, "_home_local_idea", lambda _ctx, avoid=(): _recipe(local_name))
    return recipe_generation.get_cooking_home_idea("42", refresh=True)["name"]


def test_other_recipe_skips_recipes_already_shown_this_month(monkeypatch):
    assert _refresh_with(monkeypatch, llm_name="Сырники", local_name="Гренки") == "Гренки"


def test_other_recipe_never_repeats_current_when_nothing_new(monkeypatch):
    assert _refresh_with(monkeypatch, llm_name="Омлет", local_name="Омлет") == "Сырники"


def test_local_fallback_skips_shown_recipes_when_ai_is_down():
    fridge = "яйца, помидоры, сыр, хлеб, рис, курица, лук"
    first = recipe_generation._fallback_leftovers_recipe(fridge, meal="lunch")["name"]
    second = recipe_generation._fallback_leftovers_recipe(fridge, meal="lunch", avoid=[first])["name"]

    assert second != first
    assert recipe_generation._fallback_leftovers_recipe(
        "яйца", meal="lunch", avoid=["Яичная сковорода с овощами", "Омлет"],
    )["name"] == "Яичная сковорода с овощами"


def test_other_recipe_asks_meal_then_cuisine():
    from ui import menu as menu_ui

    card = menu_ui.food_card_kb().inline_keyboard
    assert (card[0][0].text, card[0][0].callback_data) == ("✨ Новый рецепт", "food_pick")
    meals = menu_ui.food_meal_kb().inline_keyboard
    assert [row[0].text for row in meals[:-1]] == ["Завтрак", "Обед", "Ужин"]
    assert all(not row[0].api_kwargs for row in meals[:-1])
    assert meals[-1][0].callback_data == "food_card"
    cuisines = menu_ui.food_cuisine_kb("dinner", [("italian", "🍕 Итальянская")]).inline_keyboard
    assert [(row[0].text, row[0].callback_data) for row in cuisines[:-1]] == [
        ("Любая кухня", "food_go_dinner_any"), ("🍕 Итальянская", "food_go_dinner_italian"),
    ]
    assert all(row[0].api_kwargs == {"style": "success"} for row in cuisines[:-1])
    assert cuisines[-1][0].callback_data == "food_pick"


def test_chosen_cuisine_changes_prompt_but_not_day_cache_signature(monkeypatch):
    monkeypatch.setattr(recipe_generation.store, "get_list", lambda *_a: [{"name": "яйца", "on": True}])
    monkeypatch.setattr(recipe_generation.store, "get_profile", lambda _cid: {})
    monkeypatch.setattr(recipe_generation, "_cuisine_context", lambda _cid: "любые")

    base = recipe_generation._home_idea_context("42")
    chosen = recipe_generation._home_idea_context("42", cuisine="italian")

    assert chosen["signature"] == base["signature"]
    assert chosen["cuisine_codes"] == ["italian"] and "Итальянская" in chosen["cuisines"]


def test_food_go_passes_meal_and_cuisine_to_one_recipe(monkeypatch):
    import bot_callbacks
    from types import SimpleNamespace

    calls = []

    async def send_food_menu(_bot, cid, **kwargs):
        calls.append((cid, kwargs["meal"], kwargs["cuisine"], kwargs["refresh"]))

    async def status_call(call, **_kw):
        return await call(None)

    monkeypatch.setattr(bot_callbacks.menu, "send_food_menu", send_food_menu)
    c = SimpleNamespace(bot=None, cid="42", data="food_go_lunch_eastern_european", status=status_call)
    asyncio.run(bot_callbacks._food_recipe(c))
    c.data = "food_go_dinner_any"
    asyncio.run(bot_callbacks._food_recipe(c))

    assert calls == [("42", "lunch", "eastern_european", True), ("42", "dinner", None, True)]
