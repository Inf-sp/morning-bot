import pytest
from telegram import MessageEntity

import wardrobe


@pytest.mark.unit
def test_fallback_improve_data_returns_useful_sections():
    data = wardrobe._fallback_improve_data({
        "верх": ["белая футболка"],
        "низ": ["синие джинсы"],
        "обувь": ["белые кеды"],
    })

    assert data["style"]
    assert data["verdict"]
    assert data["works"]
    assert data["weak"]
    assert data["replace"]
    assert "белая футболка" in data["outfit"]


@pytest.mark.unit
def test_wardrobe_entity_card_format():
    text, entities = wardrobe._build_entity_card(
        "Проверка покупки",
        "Серые брюки.",
        "Вердикт: БРАТЬ.",
        ["сочетается с белой футболкой", "не дублирует базу"],
        "Можно брать, если посадка нормальная.",
        bullet_label="Почему:",
    )

    assert text.startswith("Проверка покупки\n\nСерые брюки.")
    assert "Вердикт: БРАТЬ." in text
    assert "Почему:\n- сочетается с белой футболкой." in text
    assert "Почему:\n\n- сочетается" not in text
    assert "- не дублирует базу.\n\nМожно брать, если посадка нормальная." in text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in entities)
    label_offset = text.index("Почему:")
    assert any(e.type == MessageEntity.BOLD and e.offset == label_offset for e in entities)
