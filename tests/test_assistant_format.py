import pytest
from telegram import MessageEntity

import assistant


@pytest.mark.unit
def test_assistant_entities_card_uses_entities_without_markup():
    text, entities = assistant._assistant_entities_card(
        "🚫 <b>Запрещено</b>\n\n"
        "Очень важный пункт.\n\n"
        "> The existing text and logo placement must not be moved.\n\n"
        "Это значит:\n"
        "- нельзя двигать логотип\n"
        "- нельзя менять размер логотипа\n\n"
        "Можно менять только фон."
    )

    assert text.startswith("Запрещено\n\nОчень важный пункт.")
    assert "<b>" not in text
    assert "🚫" not in text
    assert ">" not in text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in entities)
