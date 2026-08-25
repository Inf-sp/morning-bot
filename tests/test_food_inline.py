import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import menu
import cooking
import recipe_generation
import restaurant_discovery
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


def test_pick_recipe_uses_meal_for_current_time(monkeypatch):
    calls = []

    class LunchTime:
        @classmethod
        def now(cls, _tz):
            return SimpleNamespace(hour=13)

    async def enter_meal(_bot, _cid, meal, status=None):
        calls.append((meal, status))

    monkeypatch.setattr(cooking, "datetime", LunchTime)
    monkeypatch.setattr(cooking, "enter_meal", enter_meal)
    status = object()

    asyncio.run(cooking.send_recipe_featured(object(), "42", status=status))

    assert calls == [("lunch", status)]


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


def test_nightly_cooking_warm_prepares_all_meals(monkeypatch):
    hours = []

    def idea(_cid, now=None, refresh=False):
        hours.append((now.hour, refresh))
        return {"name": "Каша"}

    monkeypatch.setattr(recipe_generation, "get_cooking_home_idea", idea)

    result = recipe_generation.warm_cooking_home_ideas("42")

    assert result == {"breakfast": True, "lunch": True, "dinner": True}
    assert hours == [(8, False), (13, False), (18, False)]


def test_other_food_place_refresh_replaces_inline_status(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def replace(self, text, **kwargs):
            calls.append(("replace", text, kwargs))

        async def stop(self, delete=True):
            calls.append(("stop", delete))

    monkeypatch.setattr(
        restaurant_discovery, "get_restaurant",
        lambda *_args, **_kwargs: {"name": "De Eendracht", "city": "Alkmaar"},
    )
    monkeypatch.setattr(menu.menu_ui, "restaurant_menu", lambda _card: SimpleNamespace(
        text="Обновлённое место", entities=[], reply_markup="food-kb"))

    asyncio.run(menu.send_food_menu(object(), "42", refresh=True, status=Status()))

    assert calls[0] == ("replace", "Обновлённое место", {
        "entities": [], "reply_markup": "food-kb",
    })


def test_food_home_uses_cached_restaurant_without_recipe_generation(monkeypatch):
    shown = []

    class Status:
        async def replace(self, text, **_kwargs):
            shown.append(text)

    monkeypatch.setattr(
        restaurant_discovery, "get_restaurant",
        lambda *_args, **_kwargs: {"name": "De Eendracht", "city": "Alkmaar"},
    )
    monkeypatch.setattr(menu.menu_ui, "restaurant_menu", lambda card: SimpleNamespace(
        text=card["name"], entities=[], reply_markup="food-kb",
    ))

    import time
    started = time.monotonic()
    asyncio.run(menu.send_food_menu(object(), "42", status=Status()))
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    assert shown == ["De Eendracht"]


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
