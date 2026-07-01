import pytest
from telegram import MessageEntity

import balance


@pytest.mark.unit
def test_health_entity_card_format():
    text, entities = balance._build_entity_card(
        "👩🏻‍⚕️ Разбор симптомов",
        "Болит горло второй день.",
        "Похоже на раздражение или инфекцию, но это не диагноз.",
        ["пей воду", "обратись к врачу при высокой температуре"],
        "Это справочная информация, не диагноз.",
        bullet_label="Рекомендации:",
    )

    assert text.startswith("Разбор симптомов\n\nБолит горло второй день.")
    assert "👩" not in text
    assert "Рекомендации:\n- пей воду." in text
    assert "Рекомендации:\n\n- пей воду." not in text
    assert "- обратись к врачу при высокой температуре.\n\nЭто справочная информация, не диагноз." in text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in entities)
    label_offset = text.index("Рекомендации:")
    assert any(e.type == MessageEntity.BOLD and e.offset == label_offset for e in entities)


@pytest.mark.unit
def test_motivation_entity_card_format():
    text, entities = balance._build_entity_card(
        "Мотивация",
        "Один шаг лучше идеального плана.",
        "Движение помогает снизить внутренний шум.",
        ["встань и пройди круг по комнате"],
        "Сделай первый шаг сейчас, без подготовки.",
        bullet_label="Действие:",
    )

    assert text.startswith("Мотивация\n\nОдин шаг лучше идеального плана.")
    assert "Действие:\n- встань и пройди круг по комнате." in text
    assert "Действие:\n\n- встань" not in text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in entities)
    label_offset = text.index("Действие:")
    assert any(e.type == MessageEntity.BOLD and e.offset == label_offset for e in entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in entities)
