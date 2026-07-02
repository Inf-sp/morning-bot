import pytest
from telegram import MessageEntity

from ui import balance


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _entities_of_type(msg, entity_type):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == entity_type]


@pytest.mark.unit
def test_balance_entity_card_message_spec():
    msg = balance.entity_card(
        "👩🏻‍⚕️ Разбор симптомов",
        "Болит горло второй день.",
        "Похоже на раздражение или инфекцию, но это не диагноз.",
        ["пей воду", "обратись к врачу при высокой температуре"],
        "Это справочная информация, не диагноз.",
        bullet_label="Рекомендации:",
    )

    assert msg.text.startswith("Разбор симптомов\n\nБолит горло второй день.")
    assert "👩" not in msg.text
    assert "Рекомендации:\n- пей воду." in msg.text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in msg.entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in msg.entities)
    label_offset = msg.text.index("Рекомендации:")
    assert any(e.type == MessageEntity.BOLD and e.offset == label_offset for e in msg.entities)


@pytest.mark.unit
def test_balance_worries_diary_message_spec():
    msg = balance.worries_diary([{"text": "опоздаю <на поезд>"}])

    assert msg.text.startswith("📓 Дневник тревог")
    assert "опоздаю <на поезд>" in msg.text
    assert "📓 Дневник тревог" in _entities_of_type(msg, "bold")
    assert "Тревоги за сегодня:" in _entities_of_type(msg, "bold")
    assert "• опоздаю <на поезд>" in msg.text


@pytest.mark.unit
def test_balance_worries_diary_empty_message_spec():
    msg = balance.worries_diary([])

    assert msg.text.startswith("📓 Дневник тревог")
    assert "Пока пусто. Напиши тревоги одним сообщением." in msg.text
    assert _entities_of_type(msg, "bold") == ["📓 Дневник тревог"]


@pytest.mark.unit
def test_balance_evening_review_empty_message_spec():
    msg = balance.evening_review_empty()

    assert msg.text.startswith("🥸 Вечерний разбор")
    assert "Сегодня тревог не записано." in msg.text
    assert _entities_of_type(msg, "bold") == ["🥸 Вечерний разбор"]


@pytest.mark.unit
def test_balance_evening_review_message_spec():
    msg = balance.evening_review(
        [{"text": "завалю проект"}],
        [{"note": "это предположение, не факт"}],
        "день был спокойнее, чем казалось",
    )

    assert msg.text.startswith("🥸 Вечерний разбор")
    assert "Сегодня тебя беспокоили:" in _entities_of_type(msg, "bold")
    assert "это предположение, не факт" in _entities_of_type(msg, "italic")
    assert "Итог дня:" in _entities_of_type(msg, "bold")
    assert "День был спокойнее" in msg.text
