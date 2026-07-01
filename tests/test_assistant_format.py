import pytest
from telegram import MessageEntity

import assistant


@pytest.mark.unit
def test_assistant_entities_card_uses_entities_without_markup():
    text, entities = assistant._assistant_entities_card(
        "🚫 <b>Запрещено</b>\n\n"
        "Очень важный пункт.\n\n"
        "> The existing text and logo placement must not be moved.\n\n"
        "Значит:\n"
        "- нельзя двигать логотип\n"
        "- нельзя менять размер логотипа\n\n"
        "Можно менять только фон."
    )

    assert text.startswith("Запрещено\n\nОчень важный пункт.")
    assert "<b>" not in text
    assert "🚫" not in text
    assert ">" not in text
    assert "Что важно:\n- нельзя двигать логотип" in text
    assert "Что важно:\n\n- нельзя двигать логотип" not in text
    assert "- нельзя менять размер логотипа\n\nМожно менять только фон." in text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in entities)
    label_offset = text.index("Что важно:")
    assert any(e.type == MessageEntity.BOLD and e.offset == label_offset for e in entities)


@pytest.mark.unit
def test_assistant_entities_card_strips_final_intro_label():
    text, _ = assistant._assistant_entities_card(
        "Запрещено\n\n"
        "Очень важный пункт.\n\n"
        "Значит:\n"
        "- нельзя двигать логотип\n"
        "- нельзя менять размер логотипа\n\n"
        "Последний совет: Можно менять только фон."
    )

    assert "Последний совет:" not in text
    assert "Что важно:" in text
    assert "- нельзя менять размер логотипа\n\nМожно менять только фон." in text
