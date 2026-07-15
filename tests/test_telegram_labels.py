from telegram import MessageEntity

from ui.assistant import assistant_answer
from ui.builder import MessageBuilder
from ui.wardrobe import render_wardrobe_message
from util import tg_html


def _bold_fragments(message):
    return [
        message.text.encode("utf-16-le")[entity.offset * 2:(entity.offset + entity.length) * 2].decode("utf-16-le")
        for entity in message.entities
        if entity.type == MessageEntity.BOLD
    ]


def test_labeled_line_bolds_colon_and_lowercases_sentence():
    message = MessageBuilder().labeled_line("Как носить:", "Подверни рукава.").build_stripped()

    assert message.text == "Как носить: подверни рукава."
    assert _bold_fragments(message) == ["Как носить:"]


def test_wardrobe_card_uses_all_outfit_labels():
    message = render_wardrobe_message({
        "weather_decision": "Сегодня прохладно — нужен верхний слой.",
        "items": [{"name": "Белая рубашка"}, {"name": "Синие брюки"}],
        "style_tip": "Подверни рукава рубашки.",
        "reasons": ["Рубашка связывает светлый верх и тёмный низ"],
    })

    assert _bold_fragments(message) == [
        "👟 Гардероб",
        "Надень:",
        "Как носить:",
        "Почему работает:",
        "Образ готов:",
    ]
    assert "Как носить: подверни рукава рубашки." in message.text
    assert "Почему работает: рубашка связывает" in message.text
    assert "Образ готов: сегодня прохладно" in message.text


def test_free_text_formatter_applies_same_rule_to_plain_and_markdown_labels():
    assert tg_html("Надень: Белую рубашку.") == "<b>Надень:</b> белую рубашку."
    assert tg_html("**Как носить:** Подверни рукава.") == "<b>Как носить:</b> подверни рукава."


def test_assistant_card_bolds_inline_label():
    message = assistant_answer("Образ\nПочему работает: Светлый верх поддерживает обувь.")

    assert "Почему работает: светлый верх" in message.text
    assert _bold_fragments(message) == ["Образ", "Почему работает:"]
