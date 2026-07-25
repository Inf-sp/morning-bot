import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import menu
import recipe_generation
import util


def test_other_food_menu_refresh_edits_current_inline_message(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def replace(self, text, **kwargs):
            calls.append(("replace", text, kwargs))

        async def stop(self, delete=True):
            calls.append(("stop", delete))

    async def start_inline(q, bot=None, cid=None, stages=None):
        calls.append(("start_inline", q, bot, cid, stages))
        return Status()

    class Bot:
        async def send_message(self, **kwargs):
            raise AssertionError("food refresh must not send a new chat message")

    monkeypatch.setattr(util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(recipe_generation, "get_cooking_home_idea", lambda *_args: {"name": "Паста"})
    monkeypatch.setattr(menu.menu_ui, "food_menu", lambda _idea: SimpleNamespace(
        text="Обновлённый рецепт", entities=[], reply_markup="food-kb"))

    class Query:
        message = object()

    asyncio.run(menu.send_food_menu(Bot(), "42", refresh=True, q=Query()))

    assert calls[0][0] == "start_inline"
    assert calls[1] == ("replace", "Обновлённый рецепт", {
        "entities": [], "reply_markup": "food-kb",
    })
    assert calls[-1] == ("stop", True)
