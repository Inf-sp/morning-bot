import pytest

from ui import food


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _entities_of_type(msg, entity_type):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == entity_type]


@pytest.mark.unit
def test_food_card_keeps_html_like_chars_verbatim_and_bolds_headers():
    msg = food.food_card({
        "name": "Омлет <сыр>",
        "ingredients": "яйца & молоко",
        "steps": ["смешать", "жарить <5 минут>"],
    })

    assert "🥣 Рецепт дня" in msg.text
    assert "Омлет <сыр>" in msg.text
    assert "яйца & молоко" in msg.text
    assert "• жарить <5 минут>" in msg.text
    assert _entities_of_type(msg, "bold") == [
        "🥣 Рецепт дня",
        "Омлет <сыр>",
        "Ингредиенты:",
        "Приготовление:",
        "😋 Приятного аппетита!",
    ]


@pytest.mark.unit
def test_food_card_minimal_data_has_no_leaked_html():
    msg = food.food_card({"name": "", "ingredients": "", "steps": []})

    assert "<" not in msg.text and ">" not in msg.text
    assert _entities_of_type(msg, "bold") == ["🥣 Рецепт дня", "😋 Приятного аппетита!"]


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


@pytest.mark.unit
def test_food_card_shows_meal_emoji_and_cuisine_origin():
    msg = food.food_card(
        {
            "name": "Японский омлет",
            "ingredients": "яйца, соль, перец",
            "cuisine": "japanese",
            "cuisine_emoji": "🇯🇵",
            "chef_tip": "Не давай омлету подгореть снизу.",
            "steps": [{"text": "Взбейте яйца", "minutes": 2}],
        },
        label="Завтрак",
        meal="breakfast",
    )

    assert "🥐 Завтрак • 🇯🇵 Японская кухня" in msg.text
    assert "• Взбейте яйца — 2 мин." in msg.text
    assert "Совет шефа:" in msg.text
    assert "Не давай омлету подгореть снизу." in msg.text


@pytest.mark.unit
def test_food_card_falls_back_to_default_cuisine_emoji_when_missing():
    msg = food.food_card(
        {"name": "Паста", "cuisine": "italian", "ingredients": "паста"},
        label="Обед",
        meal="lunch",
        cuisine_emoji_fallback={"italian": "🇮🇹"},
    )

    assert "🥗 Обед • 🇮🇹 Итальянская кухня" in msg.text


@pytest.mark.unit
def test_food_card_without_meal_or_cuisine_keeps_legacy_header():
    msg = food.food_card({"name": "Суп", "ingredients": "вода"})

    assert "🥣 Рецепт дня" in msg.text
    assert "•" not in msg.text.split("\n", 1)[0]


@pytest.mark.unit
def test_fit_caption_returns_same_message_when_within_limit():
    msg = food.food_card({"name": "Короткий рецепт", "ingredients": "соль"})
    fitted = food.fit_caption(msg)

    assert fitted.text == msg.text
    assert fitted.entities == msg.entities


@pytest.mark.unit
def test_fit_caption_truncates_long_card_within_telegram_limit():
    from ui.builder import u16_len

    msg = food.food_card(
        {
            "name": "Рецепт с длинным описанием",
            "ingredients": ", ".join(f"ингредиент {i}" for i in range(100)),
            "chef_tip": "Совет " * 200,
            "steps": [{"text": "Шаг приготовления", "minutes": 5}] * 5,
        },
        label="Ужин",
        meal="dinner",
    )
    assert u16_len(msg.text) > food.TELEGRAM_CAPTION_LIMIT

    fitted = food.fit_caption(msg)

    assert u16_len(fitted.text) <= food.TELEGRAM_CAPTION_LIMIT
    assert fitted.text.endswith("…")
    assert all(e.offset + e.length <= u16_len(fitted.text) for e in fitted.entities)


@pytest.mark.unit
def test_food_card_strips_cuisine_adjective_from_name_to_avoid_duplication():
    msg = food.food_card(
        {
            "name": "Итальянские тосты с авокадо",
            "cuisine": "italian",
            "cuisine_emoji": "🇮🇹",
            "ingredients": "хлеб, авокадо",
        },
        label="Завтрак",
        meal="breakfast",
    )

    assert "🇮🇹 Итальянская кухня" in msg.text
    assert "Итальянские тосты с авокадо" not in msg.text
    assert "Тосты с авокадо" in msg.text


@pytest.mark.unit
def test_food_card_strips_cuisine_adjective_regardless_of_grammatical_form():
    cases = [
        ("Японский омлет", "japanese", "Омлет"),
        ("Турецкая шакшука", "turkish", "Шакшука"),
        ("Греческий салат", "greek", "Салат"),
    ]
    for name, cuisine, expected in cases:
        msg = food.food_card({"name": name, "cuisine": cuisine, "ingredients": "x"}, label="Обед", meal="lunch")
        assert expected in msg.text
        assert name not in msg.text


@pytest.mark.unit
def test_food_card_keeps_name_untouched_without_cuisine():
    msg = food.food_card({"name": "Итальянские тосты с авокадо", "ingredients": "хлеб"})

    assert "Итальянские тосты с авокадо" in msg.text


@pytest.mark.unit
def test_food_card_keeps_name_when_adjective_is_the_whole_name():
    msg = food.food_card({"name": "Итальянское", "cuisine": "italian", "ingredients": "x"}, label="Обед", meal="lunch")

    assert "Итальянское" in msg.text
