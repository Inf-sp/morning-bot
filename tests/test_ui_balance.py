import pytest
from telegram import MessageEntity

from ui import balance


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

    assert msg.parse_mode == "HTML"
    assert "📓 <b>Дневник тревог</b>" in msg.text
    assert "опоздаю &lt;на поезд&gt;" in msg.text


@pytest.mark.unit
def test_balance_evening_review_message_spec():
    msg = balance.evening_review(
        [{"text": "завалю проект"}],
        [{"note": "это предположение, не факт"}],
        "день был спокойнее, чем казалось",
    )

    assert msg.parse_mode == "HTML"
    assert "🥸 <b>Вечерний разбор</b>" in msg.text
    assert "<i>это предположение, не факт</i>" in msg.text
    assert "День был спокойнее" in msg.text
