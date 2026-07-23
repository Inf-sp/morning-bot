from telegram import MessageEntity

from ui import balance as balance_ui
from ui import menu as menu_ui
from ui.assistant import assistant_answer
from ui.builder import MessageBuilder
from ui.myday import day_summary
from ui.constants import delete_label
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


def test_wardrobe_card_uses_current_outfit_labels():
    message = render_wardrobe_message({
        "weather_intro": "Сегодня прохладно — нужен верхний слой.",
        "items": [{"name": "Белая рубашка"}, {"name": "Синие брюки"}],
        "style_tip": "Подверни рукава рубашки.",
        "reasons": ["Рубашка связывает светлый верх и тёмный низ"],
    })

    assert _bold_fragments(message) == [
        "👟 Гардероб · Образ на сегодня",
        "Надень:",
        "Полезно:",
    ]
    assert "Надень:\n• Белая рубашка\n• Синие брюки" in message.text
    assert "💡 Полезно: подверни рукава рубашки." in message.text


def test_day_summary_lifehack_keeps_capital_letter_after_label():
    message = day_summary("Пн, 20 июля", "Алкмар", lifehack="проверь расписание утром")

    assert "Лайфхак: Проверь расписание утром" in message.text


def test_day_summary_outfit_keeps_capital_letter_after_label():
    message = day_summary("Пн, 20 июля", "Алкмар", outfit_items=["светло-серая рубашка", "чёрные брюки"])

    assert "Надень: Светло-серая рубашка, чёрные брюки" in message.text


def test_day_summary_word_keeps_capital_letter_after_label():
    message = day_summary("Пн, 20 июля", "Алкмар", word_line="tijd → время")

    assert "Нидерландский: Tijd → Время" in message.text


def test_free_text_formatter_applies_same_rule_to_plain_and_markdown_labels():
    assert tg_html("Надень: Белую рубашку.") == "<b>Надень:</b> Белую рубашку."
    assert tg_html("**Как носить:** Подверни рукава.") == "<b>Как носить:</b> Подверни рукава."


def test_assistant_card_bolds_inline_label():
    message = assistant_answer("Образ\nПочему работает: Светлый верх поддерживает обувь.")

    assert "Почему работает: Светлый верх" in message.text
    assert _bold_fragments(message) == ["Образ", "Почему работает:"]


def test_delete_button_label_always_uses_cross_emoji_once():
    assert delete_label("Удалить") == "❌ Удалить"
    assert delete_label("Убрать из любимого") == "❌ Убрать из любимого"
    assert delete_label("❌ Удалить") == "❌ Удалить"


def test_health_uses_thoughts_label_and_new_emoji():
    health_menu = menu_ui.menu_screen("m_balance")
    labels = [
        button.text
        for row in health_menu.reply_markup.inline_keyboard
        for button in row
    ]

    assert "😮‍💨 Мысли" in labels
    assert "📓 Тревоги" not in labels
    assert balance_ui.worries_diary([]).text.startswith("😮‍💨 Мысли\n")
