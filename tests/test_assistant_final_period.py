import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import assistant


def test_answer_without_final_punctuation_gets_period():
    assert assistant._with_final_period("чтобы они не промокли") == "чтобы они не промокли."
    assert assistant._with_final_period("Смотри «Дюна»") == "Смотри «Дюна»."


def test_answer_with_punctuation_emoji_or_code_is_unchanged():
    for text in ("Готово.", "Правда?", "Ура!", "Итак…", "ок 👍", "x\n```", ""):
        assert assistant._with_final_period(text) == text


def test_specific_recipe_request_gets_a_recipe_not_the_meal_picker(monkeypatch):
    import asyncio
    import cooking

    text = "есть рецепт быстрой выпечки в духовке к чаю например из гранолы"
    assert assistant._specific_recipe_request(text) == text
    for generic in ("что приготовить на ужин?", "дай рецепт", "что поесть", "хочу есть"):
        assert assistant._specific_recipe_request(generic) is None

    asked = []

    async def send_recipe(_bot, _cid, constraint="обычное блюдо", status=None):
        asked.append(constraint)

    monkeypatch.setattr(cooking, "send_recipe", send_recipe)
    asyncio.run(assistant._run_intent(object(), "42", "meal_request", text))
    assert asked == [f"по просьбе пользователя: {text}"]
