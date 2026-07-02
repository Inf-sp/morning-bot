import pytest
from telegram import MessageEntity

from ui import assistant as assistant_ui


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _entities_of(msg, entity_type):
    return [e for e in msg.entities if e.type == entity_type]


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


@pytest.mark.unit
def test_assistant_answer_title_entity_covers_full_title_text():
    msg = assistant_ui.assistant_answer(
        "🚫 <b>Запрещено</b>\n\nОчень важный пункт."
    )

    bold = _entities_of(msg, MessageEntity.BOLD)
    assert bold, "title should be rendered via section() -> bold entity"
    title_entity = bold[0]
    assert title_entity.offset == 0
    assert _slice_u16(msg.text, title_entity.offset, title_entity.length) == "Запрещено"


@pytest.mark.unit
def test_assistant_answer_no_body_has_no_trailing_blank_line():
    msg = assistant_ui.assistant_answer("Простой заголовок")

    assert msg.text == "Простой заголовок"
    assert msg.text[-1] != "\n"
    bold = _entities_of(msg, MessageEntity.BOLD)
    assert len(bold) == 1
    assert _slice_u16(msg.text, bold[0].offset, bold[0].length) == "Простой заголовок"


@pytest.mark.unit
def test_assistant_answer_empty_input_falls_back_to_placeholder():
    msg = assistant_ui.assistant_answer("")

    assert msg.text == "Пусто\n\nПопробуй ещё раз."
    bold = _entities_of(msg, MessageEntity.BOLD)
    assert len(bold) == 1
    assert _slice_u16(msg.text, bold[0].offset, bold[0].length) == "Пусто"


@pytest.mark.unit
def test_assistant_answer_strips_final_intro_label_and_keeps_list_join():
    msg = assistant_ui.assistant_answer(
        "Запрещено\n\n"
        "Очень важный пункт.\n\n"
        "Значит:\n"
        "- нельзя двигать логотип\n"
        "- нельзя менять размер логотипа\n\n"
        "Последний совет: Можно менять только фон."
    )

    assert "Последний совет:" not in msg.text
    assert "Что важно:\n- нельзя двигать логотип" in msg.text
    assert "- нельзя менять размер логотипа\n\nМожно менять только фон." in msg.text
    assert "\n\n\n" not in msg.text

    label_offset = msg.text.index("Что важно:")
    bold_at_label = [
        e for e in _entities_of(msg, MessageEntity.BOLD) if e.offset == label_offset
    ]
    assert bold_at_label
    assert _slice_u16(msg.text, bold_at_label[0].offset, bold_at_label[0].length) == "Что важно:"
