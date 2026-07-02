import pytest
from telegram import MessageEntity

from ui import wardrobe


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _entities_of_type(msg, entity_type):
    return [e for e in msg.entities if e.type == entity_type]


def _bold_texts(msg):
    return [_slice_u16(msg.text, e.offset, e.length) for e in _entities_of_type(msg, MessageEntity.BOLD)]


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
    assert "\n\n\n" not in msg.text

    bold = _entities_of_type(msg, MessageEntity.BOLD)
    assert bold[0].offset == 0
    assert _slice_u16(msg.text, bold[0].offset, bold[0].length) == "Проверка покупки"

    label_offset = msg.text.index("Почему:")
    assert any(
        e.offset == label_offset and _slice_u16(msg.text, e.offset, e.length) == "Почему:"
        for e in bold
    )

    quote_entities = _entities_of_type(msg, MessageEntity.BLOCKQUOTE)
    assert len(quote_entities) == 1
    quote_entity = quote_entities[0]
    assert _slice_u16(msg.text, quote_entity.offset, quote_entity.length) == "Вердикт: БРАТЬ."


@pytest.mark.unit
def test_wardrobe_entity_card_title_only():
    msg = wardrobe.entity_card("Просто заголовок")

    assert msg.text == "Просто заголовок"
    bold = _entities_of_type(msg, MessageEntity.BOLD)
    assert len(bold) == 1
    assert bold[0].offset == 0
    assert _slice_u16(msg.text, bold[0].offset, bold[0].length) == "Просто заголовок"
    assert not _entities_of_type(msg, MessageEntity.BLOCKQUOTE)


@pytest.mark.unit
def test_wardrobe_look_message_spec():
    msg = wardrobe.look_message(
        ["белая футболка", "синие джинсы"], intro="Сегодня сухо.", add_text="Возьми куртку."
    )

    assert msg.text.startswith("✨ Образ на сегодня")
    assert "Сегодня сухо." in msg.text
    assert "• белая футболка" in msg.text
    assert "• синие джинсы" in msg.text
    assert "\n\n\n" not in msg.text

    bold = _entities_of_type(msg, MessageEntity.BOLD)
    assert bold[0].offset == 0
    assert _slice_u16(msg.text, bold[0].offset, bold[0].length) == "✨ Образ на сегодня"

    quote_entities = _entities_of_type(msg, MessageEntity.BLOCKQUOTE)
    assert len(quote_entities) == 1
    quote_entity = quote_entities[0]
    assert _slice_u16(msg.text, quote_entity.offset, quote_entity.length) == (
        "• белая футболка\n• синие джинсы"
    )

    italic_entities = _entities_of_type(msg, MessageEntity.ITALIC)
    assert len(italic_entities) == 1
    italic_entity = italic_entities[0]
    assert _slice_u16(msg.text, italic_entity.offset, italic_entity.length) == "Возьми куртку."
    assert msg.text.endswith("Возьми куртку.")


@pytest.mark.unit
def test_wardrobe_look_message_title_only():
    msg = wardrobe.look_message([])

    assert msg.text == "✨ Образ на сегодня"
    bold = _entities_of_type(msg, MessageEntity.BOLD)
    assert len(bold) == 1
    assert bold[0].offset == 0
    assert not _entities_of_type(msg, MessageEntity.BLOCKQUOTE)
    assert not _entities_of_type(msg, MessageEntity.ITALIC)
