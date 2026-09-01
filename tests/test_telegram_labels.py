from telegram import MessageEntity

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
        "primary_style": "Скандинавский",
        "items": [
            {"name": "Белая рубашка", "zone": "Верх"},
            {"name": "Синие брюки", "zone": "Низ"},
        ],
        "how_to_wear": ["Подверни рукава, чтобы образ выглядел легче"],
        "main_accent": "Спокойные оттенки связывают комплект",
    })

    assert _bold_fragments(message) == [
        "🧥 Образ на сегодня · Скандинавский",
        "Надень:",
        "Главный акцент:",
    ]
    assert "🧥 Образ на сегодня · Скандинавский" in message.text
    assert "Гардероб · Образ на сегодня" not in message.text
    assert "Надень:\n- Белая рубашка\n- Синие брюки" in message.text
    assert "Как носить:" not in message.text
    assert "💡 Главный акцент: спокойные оттенки связывают комплект." in message.text


def test_day_summary_lifehack_keeps_capital_letter_after_label():
    message = day_summary("Пн, 20 июля", "Алкмар", lifehack="проверь расписание утром")

    assert "🦉Лайфхак: Проверь расписание утром" in message.text


def test_day_summary_header_shows_country_and_flag():
    message = day_summary("Вт, 4 августа", "Лилль", country="FR", flag="🇫🇷")

    assert message.text.startswith("Мой день · Вт, 4 августа · Лилль, FR 🇫🇷")


def test_day_summary_puts_quote_after_movie_rebus():
    message = day_summary(
        "Вт, 4 августа", "Лилль",
        movie_rebus={"emoji": "🦈 🌊 👨‍🔬", "answer": "Челюсти"},
        quote_text="Человека можно уничтожить, но нельзя победить.",
        quote_author="Эрнест Хемингуэй",
    )

    assert "🎬 Ребус дня: 🦈 🌊 👨‍🔬 → Челюсти" in message.text
    assert "🎫 Афиша" not in message.text
    assert message.text.endswith(
        "💭 «Человека можно уничтожить, но нельзя победить.» — Эрнест Хемингуэй"
    )
    spoiler = next(entity for entity in message.entities if entity.type == MessageEntity.SPOILER)
    assert message.text.encode("utf-16-le")[spoiler.offset * 2:(spoiler.offset + spoiler.length) * 2].decode("utf-16-le") == "Челюсти"


def test_day_summary_outfit_keeps_capital_letter_after_label():
    message = day_summary(
        "Пн, 20 июля", "Алкмар",
        outfit_items=["светло-серая рубашка", "чёрные брюки"],
        outfit_emoji="👕",
    )

    assert "👕 Образ: Светло-серая рубашка, чёрные брюки" in message.text


def test_day_summary_puts_cached_restaurant_immediately_after_outfit():
    message = day_summary(
        "Вт, 1 сентября", "Алкмар",
        outfit_items=["Белая футболка", "Чёрные брюки"], outfit_emoji="👕",
        restaurant_line="Roest Alkmaar · современная европейская · €€",
        lifehack="возьми зонт",
    )

    outfit_at = message.text.index("👕 Образ:")
    restaurant_at = message.text.index("🍽️ Куда сходить:")
    lifehack_at = message.text.index("🦉Лайфхак:")
    assert outfit_at < restaurant_at < lifehack_at
    assert "🍽️ Куда сходить: Roest Alkmaar · современная европейская · €€." in message.text


def test_day_summary_word_keeps_capital_letter_after_label():
    message = day_summary("Пн, 20 июля", "Алкмар", word_line="tijd → время")

    assert "🎯 Тренажер: Tijd → Время" in message.text


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
