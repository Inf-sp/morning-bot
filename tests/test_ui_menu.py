import pytest
from telegram import MessageEntity

from ui.builder import u16_len
from ui import menu


@pytest.mark.unit
def test_menu_welcome_message_spec_has_entities():
    msg = menu.welcome()

    assert msg.text.startswith("👋 Привет! Я DM")
    assert "Разделы" in msg.text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in msg.entities)
    label_offset = u16_len(msg.text[:msg.text.index("Разделы")])
    assert any(e.type == MessageEntity.BOLD and e.offset == label_offset for e in msg.entities)


@pytest.mark.unit
def test_menu_screen_message_spec_has_html_and_keyboard():
    msg = menu.menu_screen("m_learn")

    assert msg.parse_mode == "HTML"
    assert msg.text.startswith("📚 <b>Обучение</b>")
    assert "Настройках" in msg.text
    assert msg.reply_markup is not None
