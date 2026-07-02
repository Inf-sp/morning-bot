import pytest

from ui import food


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _entities_of_type(msg, entity_type):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == entity_type]


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
def test_fridge_home_empty_uses_section_component():
    msg = food.fridge_home_empty()

    assert msg.text == "🧊 Мой холодильник\n\nПусто — добавь продукты, которые обычно есть дома."
    assert _entities_of_type(msg, "bold") == ["🧊 Мой холодильник"]


@pytest.mark.unit
def test_fridge_home_shows_counts_and_bold_title():
    msg = food.fridge_home(5, 3)

    assert msg.text == "🧊 Мой холодильник · 5 продуктов · 3 в наличии\n\nВыбери категорию:"
    assert _entities_of_type(msg, "bold") == ["🧊 Мой холодильник"]


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
    assert not msg.text.endswith("\n")
    assert _entities_of_type(msg, "bold") == [
        "🧊 Холодильник обновлён",
        "Добавил:",
        "Молочка:",
        "Уже было:",
        "Не добавил:",
    ]


@pytest.mark.unit
def test_my_recipes_empty_uses_section_component():
    msg = food.my_recipes_empty()

    assert msg.text == (
        "🍳 Мои рецепты\n\n"
        "Пусто. Сохраняй рецепты кнопкой «❤️ Сохранить рецепт» под любым рецептом."
    )
    assert _entities_of_type(msg, "bold") == ["🍳 Мои рецепты"]


@pytest.mark.unit
def test_my_recipes_list_escapes_names():
    msg = food.my_recipes_list([{"name": "Паста <быстро>"}])

    assert "🍳 Мои рецепты — 1" in msg.text
    assert "• Паста <быстро>" in msg.text
    assert not msg.text.endswith("\n")


@pytest.mark.unit
def test_my_recipes_list_uses_bullet_component_per_recipe():
    msg = food.my_recipes_list([{"name": "Паста"}, {"name": "Суп"}])

    assert msg.text == "🍳 Мои рецепты — 2\n\n• Паста\n• Суп"
    assert _entities_of_type(msg, "bold") == ["🍳 Мои рецепты"]
