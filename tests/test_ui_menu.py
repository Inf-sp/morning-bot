import pytest
from telegram import MessageEntity

from ui.builder import u16_len
from ui import menu


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _bold_texts(msg):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == MessageEntity.BOLD]


@pytest.mark.unit
def test_menu_welcome_message_spec_has_entities():
    msg = menu.welcome()

    assert msg.text.startswith("👋 Привет! Я DM")
    assert "Разделы" in msg.text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in msg.entities)
    label_offset = u16_len(msg.text[:msg.text.index("Разделы")])
    assert any(e.type == MessageEntity.BOLD and e.offset == label_offset for e in msg.entities)


@pytest.mark.unit
def test_menu_welcome_bold_entities_match_intro_section_and_settings_word():
    msg = menu.welcome()

    bold = _bold_texts(msg)
    assert bold == [
        "👋 Привет! Я DM — твой помощник на каждый день.",
        "Разделы",
        "Настройках",
    ]


@pytest.mark.unit
def test_menu_welcome_lists_sections_without_bullet_marks():
    msg = menu.welcome()

    assert "☀️ Мой день — погода, сводка и советы." in msg.text
    assert "🥣 Готовка — рецепты и идеи из продуктов." in msg.text
    assert "• " not in msg.text
    assert "\n\n\n" not in msg.text


@pytest.mark.unit
def test_menu_screen_message_spec_has_html_and_keyboard():
    msg = menu.menu_screen("m_learn")

    assert msg.parse_mode is None
    assert msg.text == (
        "📚 Обучение\n\n"
        "Выбери язык — и вперёд!\n\n"
        "Изменить параметры или посмотреть сохранённую информацию можно в 🎚️ Настройках."
    )
    assert _bold_texts(msg) == ["Обучение", "Настройках"]
    assert msg.reply_markup is not None


@pytest.mark.unit
def test_menu_screen_wardrobe_message_spec_has_html_and_keyboard():
    msg = menu.menu_screen("m_wardrobe")

    assert msg.parse_mode is None
    assert msg.text == (
        "👕 Гардероб\n\n"
        "Одежда без хаоса. Подберу образ, помогу разобрать шкаф и выбрать, что стоит докупить. "
        "Чем полнее гардероб, тем точнее рекомендации.\n\n"
        "Изменить параметры или посмотреть сохранённую информацию можно в 🎚️ Настройках."
    )
    assert _bold_texts(msg) == ["Гардероб", "Настройках"]
    assert msg.reply_markup is not None


@pytest.mark.unit
def test_menu_screen_afisha_has_five_category_buttons():
    msg = menu.menu_screen("m_afisha")

    assert "Афиша" in _bold_texts(msg)
    callbacks = [c for row in msg.reply_markup.inline_keyboard for c in [b.callback_data for b in row]]
    assert callbacks == [
        "a_concerts_find", "a_afisha_festivals", "a_afisha_theatre",
        "a_afisha_comedy", "a_afisha_exhibitions", "m_leisure",
    ]


@pytest.mark.unit
def test_menu_screen_leisure_links_to_afisha_and_city_digest():
    msg = menu.menu_screen("m_leisure")

    callbacks = [c for row in msg.reply_markup.inline_keyboard for c in [b.callback_data for b in row]]
    assert "m_afisha" in callbacks
    assert "a_city_digest" in callbacks
    assert "a_concerts_find" not in callbacks  # переехала внутрь Афиши


@pytest.mark.unit
def test_menu_screen_unknown_key_returns_fallback():
    msg = menu.menu_screen("does_not_exist")

    assert msg.text == "Выбери раздел в нижнем меню."
    assert msg.reply_markup is None
    assert msg.parse_mode is None
    assert not msg.entities


@pytest.mark.unit
def test_food_menu_message_spec_has_html_and_keyboard():
    msg = menu.food_menu()

    assert msg.parse_mode is None
    assert msg.text == (
        "🥣 Готовка\n\n"
        "Еда без хаоса. Соберу понятное меню на день, разберу холодильник и честно скажу, что с ним не так.\n\n"
        "Изменить параметры или посмотреть сохранённую информацию можно в 🎚️ Настройках."
    )
    assert _bold_texts(msg) == ["Готовка", "Настройках"]
    assert msg.reply_markup is not None
