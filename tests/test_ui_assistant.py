import pytest
from telegram import MessageEntity

from ui import assistant as assistant_ui


@pytest.mark.unit
def test_assistant_answer_message_spec_uses_entities_without_markup():
    msg = assistant_ui.assistant_answer(
        "🚫 <b>Запрещено</b>\n\n"
        "Очень важный пункт.\n\n"
        "> The existing text and logo placement must not be moved.\n\n"
        "Значит:\n"
        "- нельзя двигать логотип\n"
        "- нельзя менять размер логотипа"
    )

    assert msg.text.startswith("Запрещено\n\nОчень важный пункт.")
    assert "<b>" not in msg.text
    assert "🚫" not in msg.text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in msg.entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in msg.entities)
    label_offset = msg.text.index("Что важно:")
    assert any(e.type == MessageEntity.BOLD and e.offset == label_offset for e in msg.entities)
