import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import cooking


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
