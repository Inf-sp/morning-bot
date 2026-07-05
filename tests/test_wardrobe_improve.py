import pytest
from telegram import MessageEntity

import wardrobe


@pytest.mark.unit
def test_fallback_improve_data_returns_stylist_sections():
    data = wardrobe._fallback_improve_data({
        "верх": ["белая футболка"],
        "низ": ["синие джинсы"],
        "обувь": ["белые кеды"],
    })

    # Новая схема карточки-стилиста.
    assert isinstance(data["score"], int) and 0 <= data["score"] <= 100
    assert data["summary"]
    assert data["strengths"]
    assert data["weaknesses"] and isinstance(data["weaknesses"][0], dict)
    assert "why" in data["buy"][0] or data["buy"] == []
    assert any("белая футболка" in x for x in data["best_look"]["items"])


@pytest.mark.unit
def test_improve_card_renders_all_sections():
    from ui import wardrobe as wu
    data = {
        "score": 84,
        "summary": "Универсальная база.",
        "strengths": ["Белая рубашка — основа образов"],
        "weaknesses": [{"title": "Мало цвета", "text": "Всё чёрно-белое"}],
        "buy": [{"item": "Серая футболка", "why": "Свяжет верх и низ"}],
        "avoid": ["Ещё чёрные джинсы — дубль"],
        "best_look": {"items": ["👔 Рубашка", "👖 Брюки"], "why": "Пропорции"},
        "potential": "Город, следующий шаг — акцент.",
    }
    text = wu.improve_card(data).text
    assert "84 / 100" in text
    assert "⭐⭐⭐⭐☆" in text
    assert "Сильные стороны" in text and "Белая рубашка" in text
    assert "1. Мало цвета" in text
    assert "🥇" in text and "Серая футболка" in text
    assert "Что покупать не стоит" in text
    assert "Лучший образ" in text and "👔 Рубашка" in text
    assert "Потенциал" in text


@pytest.mark.unit
def test_priority_gaps_prepended_to_buy():
    import config
    import store
    cid = "gap-improve-cid"
    store._mem.pop(config.WARDROBE_GAPS_KEY, None)
    store.set_list(config.WARDROBE_GAPS_KEY, cid,
                   [{"item": "непромокаемая верхняя одежда", "reason": "дождливая погода", "priority": True}])
    d = {"buy": [{"item": "Серая футболка", "why": "x"}]}
    merged = wardrobe._merge_priority_gaps(cid, d)
    assert "непромокаемая" in merged["buy"][0]["item"].lower()
    assert merged["buy"][1]["item"] == "Серая футболка"
    store._mem.pop(config.WARDROBE_GAPS_KEY, None)


@pytest.mark.unit
def test_wardrobe_entity_card_format():
    text, entities = wardrobe._build_entity_card(
        "Проверка покупки",
        "Серые брюки.",
        "Вердикт: БРАТЬ.",
        ["сочетается с белой футболкой", "не дублирует базу"],
        "Можно брать, если посадка нормальная.",
        bullet_label="Почему:",
    )

    assert text.startswith("Проверка покупки\n\nСерые брюки.")
    assert "Вердикт: БРАТЬ." in text
    assert "Почему:\n- сочетается с белой футболкой." in text
    assert "Почему:\n\n- сочетается" not in text
    assert "- не дублирует базу.\n\nМожно брать, если посадка нормальная." in text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in entities)
    label_offset = text.index("Почему:")
    assert any(e.type == MessageEntity.BOLD and e.offset == label_offset for e in entities)
