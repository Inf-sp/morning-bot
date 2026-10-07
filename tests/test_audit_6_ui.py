from ui.builder import MessageBuilder
from ui.learning_entry import render_learning_entry


def _render(entry):
    builder = MessageBuilder()
    render_learning_entry(builder, entry)
    return builder.text


def test_english_noun_plural_is_not_prefixed_with_dutch_article():
    text = _render({
        "term": "Brain", "translation": "мозг", "pos": "существительное",
        "plural": "brains", "lang": "en",
    })

    assert "Множественное число: brains" in text
    assert "de brains" not in text


def test_dutch_noun_plural_keeps_de_prefix():
    text = _render({
        "term": "Brein", "article": "het", "translation": "мозг",
        "pos": "существительное", "plural": "breinen", "lang": "nl",
    })

    assert "Множественное число: de breinen" in text


def test_purchase_card_reason_starts_on_its_own_line():
    from ui.wardrobe import purchase_card

    message = purchase_card({"name": "Белая футболка", "why": "закроет базовый верх"})

    assert message.text == "🛒 Белая футболка\n\nПочему тебе: закроет базовый верх."
