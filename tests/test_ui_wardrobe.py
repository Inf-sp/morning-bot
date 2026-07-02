import pytest
from telegram import MessageEntity

from ui import wardrobe


@pytest.mark.unit
def test_wardrobe_entity_card_message_spec():
    msg = wardrobe.entity_card(
        "Проверка покупки",
        "Серые брюки.",
        "Вердикт: БРАТЬ.",
        ["сочетается с белой футболкой", "не дублирует базу"],
        "Можно брать, если посадка нормальная.",
        bullet_label="Почему:",
    )

    assert msg.text.startswith("Проверка покупки\n\nСерые брюки.")
    assert "Почему:\n- сочетается с белой футболкой." in msg.text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in msg.entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in msg.entities)
    label_offset = msg.text.index("Почему:")
    assert any(e.type == MessageEntity.BOLD and e.offset == label_offset for e in msg.entities)


@pytest.mark.unit
def test_wardrobe_look_message_spec():
    msg = wardrobe.look_message(["белая футболка", "синие джинсы"], intro="Сегодня сухо.", add_text="Возьми куртку.")

    assert msg.text.startswith("✨ Образ на сегодня")
    assert "Сегодня сухо." in msg.text
    assert "• белая футболка" in msg.text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in msg.entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in msg.entities)
    assert any(e.type == MessageEntity.ITALIC for e in msg.entities)
