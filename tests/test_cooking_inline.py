import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import cooking
import recipe_generation


def test_inline_recipe_result_replaces_search_status(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def replace(self, text, **kwargs):
            calls.append(("replace", text, kwargs))

        async def stop(self, delete=True):
            raise AssertionError("inline recipe result must not stop and delete the status before rendering")

    class Bot:
        async def send_message(self, **kwargs):
            raise AssertionError("inline recipe result must not be sent as a new chat message")

    monkeypatch.setattr(cooking.food_ui, "food_card", lambda *args, **kwargs: SimpleNamespace(
        text="Новый рецепт", entities=[]))
    monkeypatch.setattr(cooking, "_recipe_kb", lambda *_args, **_kwargs: "recipe-kb")
    monkeypatch.setattr(cooking, "_persist_current_queue_recipe", lambda *_args: None)

    asyncio.run(cooking._send_queue_card(
        Bot(), "42", "dinner", {"name": "Паста", "steps": ["Свари пасту"]}, status=Status()))

    assert calls == [("replace", "Новый рецепт", {"entities": [], "reply_markup": "recipe-kb"})]


def test_cooking_callback_can_use_shared_inline_status(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def stop(self, delete=True):
            calls.append(("stop", delete))

    async def show_next_recipe(bot, cid, status=None):
        calls.append(("show_next_recipe", bot, cid, status))

    async def unexpected_start_inline(*_args, **_kwargs):
        raise AssertionError("shared callback status must be reused")

    monkeypatch.setattr(cooking, "show_next_recipe", show_next_recipe)
    monkeypatch.setattr(cooking.util.StatusManager, "start_inline", unexpected_start_inline)

    asyncio.run(cooking.handle_callback(object(), "42", object(), "as_food", status=Status()))

    assert calls[0][0] == "show_next_recipe"
    assert calls[0][3].mode == "inline"
    assert calls == [("show_next_recipe", calls[0][1], "42", calls[0][3])]


def test_recipe_menu_fallback_reuses_shared_inline_status(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

    async def send_food_menu(bot, cid, status=None):
        calls.append((bot, cid, status))
        assert status.mode == "inline"

    monkeypatch.setattr(cooking, "get_active_meal", lambda _cid: "")
    monkeypatch.setattr(cooking.menu, "send_food_menu", send_food_menu)

    asyncio.run(cooking.show_next_recipe(object(), "42", status=Status()))

    assert calls[0][2].mode == "inline"


def test_enter_meal_replaces_stale_unpresentable_queue(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def replace(self, *_args, **_kwargs):
            raise AssertionError("stale queue must not show the generic recipe error")

    stale = {"meal": "dinner", "items": [{"name": "Оборванный рецепт"}]}
    fallback = {
        "name": "Быстрый омлет с овощами", "ingredients": "яйца, овощи",
        "steps": ["Разогрей сковороду", "Обжарь овощи"],
    }
    next_items = iter((None, fallback))

    monkeypatch.setattr(cooking, "set_active_meal", lambda *_args: None)
    monkeypatch.setattr(cooking, "get_recipe_queue", lambda _cid: stale)
    monkeypatch.setattr(cooking, "_next_presentable_queue_recipe", lambda _cid: next(next_items))
    monkeypatch.setattr(cooking, "clear_recipe_queue", lambda _cid: calls.append("clear"))

    async def generate(_cid, meal, ingredients=None):
        calls.append(("generate", meal, ingredients))
        return [fallback]

    async def send(_bot, _cid, meal, recipe, status=None):
        calls.append(("send", meal, recipe, status))

    monkeypatch.setattr(cooking, "_generate_and_store_queue", generate)
    monkeypatch.setattr(cooking, "_send_queue_card", send)

    asyncio.run(cooking.enter_meal(object(), "42", "dinner", status=Status()))

    assert calls[:2] == ["clear", ("generate", "dinner", None)]
    assert calls[2][:3] == ("send", "dinner", fallback)
    assert calls[2][3].mode == "inline"


def test_local_fallback_uses_different_egg_recipe_for_each_meal():
    ingredients = "яйца, помидоры, сыр"

    names = {
        recipe_generation._fallback_leftovers_recipe(ingredients, meal=meal)["name"]
        for meal in ("breakfast", "lunch", "dinner")
    }

    assert len(names) == 3
