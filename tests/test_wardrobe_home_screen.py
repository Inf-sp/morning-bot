from ui import wardrobe as wardrobe_ui


def test_home_screen_empty_wardrobe_text():
    msg = wardrobe_ui.home_screen(
        0,
        {"Верх": 0, "Низ": 0, "Обувь": 0},
        ["Верх", "Низ", "Обувь"],
    )

    assert msg.text == (
        "👟 Гардероб\n\n"
        "Образ на сегодня, разбор шкафа и проверка покупки перед тем, как тратить деньги.\n\n"
        "В шкафу пока пусто.\n\n"
        "Добавь несколько вещей, и бот сможет собирать образы под погоду."
    )


def test_home_screen_small_wardrobe_text_without_empty_categories():
    msg = wardrobe_ui.home_screen(
        7,
        {"Верх": 3, "Низ": 2, "Верхняя одежда": 0, "Обувь": 2, "Аксессуары": 0},
        ["Верх", "Низ", "Верхняя одежда", "Обувь", "Аксессуары"],
    )

    assert msg.text == (
        "👟 Гардероб\n\n"
        "Образ на сегодня, разбор шкафа и проверка покупки перед тем, как тратить деньги.\n\n"
        "В шкафу: 7 вещей\n"
        "База уже есть, но для точных образов нужно добавить ещё верх, низ и обувь.\n\n"
        "Верх - 3\n"
        "Низ - 2\n"
        "Обувь - 2"
    )
    assert "Верхняя одежда - 0" not in msg.text
    assert "Аксессуары - 0" not in msg.text


def test_home_screen_full_wardrobe_text_without_category_emoji():
    msg = wardrobe_ui.home_screen(
        52,
        {"Верх": 16, "Низ": 9, "Верхняя одежда": 6, "Обувь": 6, "Аксессуары": 15},
        ["Верх", "Низ", "Верхняя одежда", "Обувь", "Аксессуары"],
    )

    assert "👕" not in msg.text
    assert "👖" not in msg.text
    assert "🧥" not in msg.text
    assert "🧢" not in msg.text
    assert "В шкафу: 52 вещи" in msg.text
    assert "Шкаф заполнен хорошо - есть, из чего собирать образы." in msg.text
    assert "Аксессуары - 15" in msg.text
