import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import menu
import recipe_generation
import util


def test_home_meal_time_windows():
    assert recipe_generation._home_meal_for_hour(5) == "dinner"
    assert recipe_generation._home_meal_for_hour(6) == "breakfast"
    assert recipe_generation._home_meal_for_hour(10) == "breakfast"
    assert recipe_generation._home_meal_for_hour(11) == "lunch"
    assert recipe_generation._home_meal_for_hour(15) == "lunch"
    assert recipe_generation._home_meal_for_hour(16) == "dinner"
    assert recipe_generation._home_meal_for_hour(21) == "dinner"


def test_nightly_cooking_warm_prepares_all_meals(monkeypatch):
    hours = []

    def idea(_cid, now=None, refresh=False):
        hours.append((now.hour, refresh))
        return {"name": "Каша"}

    monkeypatch.setattr(recipe_generation, "get_cooking_home_idea", idea)

    result = recipe_generation.warm_cooking_home_ideas("42")

    assert result == {"breakfast": True, "lunch": True, "dinner": True}
    assert hours == [(8, False), (13, False), (18, False)]


def test_other_food_menu_refresh_edits_current_inline_message(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def replace(self, text, **kwargs):
            calls.append(("replace", text, kwargs))

        async def stop(self, delete=True):
            calls.append(("stop", delete))

    async def start_inline(q, bot=None, cid=None, stages=None, preserve_message=False):
        calls.append(("start_inline", q, bot, cid, stages, preserve_message))
        return Status()

    class Bot:
        async def send_message(self, **kwargs):
            raise AssertionError("food refresh must not send a new chat message")

    monkeypatch.setattr(util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(menu, "has_available_fridge", lambda _cid: True)
    monkeypatch.setattr(recipe_generation, "get_cooking_home_idea", lambda *_args: {"name": "Паста"})
    monkeypatch.setattr(menu.menu_ui, "food_menu", lambda _idea, **_kwargs: SimpleNamespace(
        text="Обновлённый рецепт", entities=[], reply_markup="food-kb"))

    class Query:
        message = object()

    asyncio.run(menu.send_food_menu(Bot(), "42", refresh=True, q=Query()))

    assert calls[0][0] == "start_inline"
    assert calls[0][-1] is True
    assert calls[1] == ("replace", "Обновлённый рецепт", {
        "entities": [], "reply_markup": "food-kb",
    })
    assert calls[-1] == ("stop", True)
