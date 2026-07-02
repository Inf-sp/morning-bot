import pytest

from ui import food


@pytest.mark.unit
def test_food_card_escapes_recipe_fields():
    msg = food.food_card({
        "name": "Омлет <сыр>",
        "ingredients": "яйца & молоко",
        "steps": ["смешать", "жарить <5 минут>"],
    })

    assert msg.parse_mode == "HTML"
    assert "🥣 <b>Рецепт дня</b>" in msg.text
    assert "<b>Омлет &lt;сыр&gt;</b>" in msg.text
    assert "яйца &amp; молоко" in msg.text
    assert "• жарить &lt;5 минут&gt;" in msg.text


@pytest.mark.unit
def test_fridge_updated_groups_added_items():
    msg = food.fridge_updated(
        {"молочка": ["молоко"]},
        ["молоко"],
        ["сыр"],
        [("камень", "не продукт")],
        ["молочка"],
        {"молочка": "🥛"},
        {"молочка": "Молочка"},
    )

    assert "🧊 Холодильник обновлён" in msg.text
    assert "🥛 Молочка: молоко" in msg.text
    assert "Уже было:\nсыр" in msg.text
    assert "• камень — не продукт" in msg.text


@pytest.mark.unit
def test_my_recipes_list_escapes_names():
    msg = food.my_recipes_list([{"name": "Паста <быстро>"}])

    assert "🍳 Мои рецепты — 1" in msg.text
    assert "• Паста <быстро>" in msg.text
