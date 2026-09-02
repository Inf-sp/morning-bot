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


def test_chat_meal_with_ingredient_routes_directly_to_best_recipe(monkeypatch):
    _reset_chat_state(monkeypatch)
    calls = []

    async def fake_run(bot, cid, action, recipe_ingredients=None):
        calls.append((bot, cid, action, recipe_ingredients))

    monkeypatch.setattr(assistant, "_run_intent", fake_run)

    bot = _Bot()
    asyncio.run(assistant.chat_reply(bot, "42", "Обед с лососем"))

    assert calls == [(bot, "42", "meal_recipe", "обед с лососем")]


def test_ingredient_recipe_intent_uses_standard_cooking_card(monkeypatch):
    calls = []

    async def fake_send_recipe(bot, cid, constraint):
        calls.append((bot, cid, constraint))

    import cooking
    monkeypatch.setattr(cooking, "send_recipe", fake_send_recipe)

    bot = _Bot()
    asyncio.run(assistant._run_intent(bot, "42", "meal_recipe", "мидий"))

    assert calls == [(bot, "42", "блюдо из мидий")]


def test_meal_recipe_intent_keeps_the_requested_meal_and_ingredient(monkeypatch):
    calls = []

    async def fake_send_recipe(bot, cid, constraint):
        calls.append((bot, cid, constraint))

    import cooking
    monkeypatch.setattr(cooking, "send_recipe", fake_send_recipe)

    bot = _Bot()
    asyncio.run(assistant._run_intent(bot, "42", "meal_recipe", "обед с лососем"))

    assert calls == [(bot, "42", "обед с лососем")]


def test_language_question_with_nedostatki_does_not_route_to_fridge_recipe():
    text = (
        "Как легко запомнить различие между преимущества и недостатки "
        "в нидерландском языке nadeel voordeel"
    )

    assert assistant._recipe_ingredients_from_chat(text) is None
    assert assistant._detect_intent(text) is None


def test_real_leftovers_and_explicit_recipe_requests_still_route_directly():
    assert assistant._detect_intent("Что сделать с остатками курицы?") == "fridge"
    assert assistant._recipe_ingredients_from_chat("Что приготовить из мидий?") == "мидий"
