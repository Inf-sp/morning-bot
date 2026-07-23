import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import assistant


class _Bot:
    async def send_chat_action(self, **_kwargs):
        pass


def _reset_chat_state(monkeypatch):
    monkeypatch.setattr(assistant.store, "last_action", {})
    monkeypatch.setattr(assistant.store, "last_source", {})
    monkeypatch.setattr(assistant.store, "chat_history", {})
    monkeypatch.setattr(assistant.store, "last_surface", {})


def test_chat_question_with_ingredient_routes_to_recipe_card(monkeypatch):
    _reset_chat_state(monkeypatch)
    calls = []

    async def fake_run(bot, cid, action, recipe_ingredients=None):
        calls.append((bot, cid, action, recipe_ingredients))

    monkeypatch.setattr(assistant, "_run_intent", fake_run)

    bot = _Bot()
    asyncio.run(assistant.chat_reply(bot, "42", "Что приготовить из мидий?"))

    assert calls == [(bot, "42", "meal_recipe", "мидий")]


def test_ingredient_recipe_intent_uses_standard_cooking_card(monkeypatch):
    calls = []

    async def fake_send_recipe(bot, cid, constraint):
        calls.append((bot, cid, constraint))

    import cooking
    monkeypatch.setattr(cooking, "send_recipe", fake_send_recipe)

    bot = _Bot()
    asyncio.run(assistant._run_intent(bot, "42", "meal_recipe", "мидий"))

    assert calls == [(bot, "42", "блюдо из мидий")]
