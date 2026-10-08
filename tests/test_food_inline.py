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

    def generate(cid, now, refresh):
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
    assert labels[0] == "✨ Другой рецепт"


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
