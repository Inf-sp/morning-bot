import pytest

from ui.builder import MessageBuilder, from_html


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _entities_of_type(msg, entity_type):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == entity_type]


@pytest.mark.unit
def test_section_is_bold_and_has_no_leading_blank_line_when_first():
    msg = MessageBuilder().section("Заголовок").line("текст").build()

    assert msg.text == "Заголовок\nтекст\n"
    assert _entities_of_type(msg, "bold") == ["Заголовок"]


@pytest.mark.unit
def test_consecutive_sections_are_separated_by_exactly_one_blank_line():
    msg = (
        MessageBuilder()
        .section("Первый")
        .line("строка 1")
        .section("Второй")
        .line("строка 2")
        .build()
    )

    assert msg.text == "Первый\nстрока 1\n\nВторой\nстрока 2\n"
    assert "\n\n\n" not in msg.text


@pytest.mark.unit
def test_warning_uses_default_emoji_and_bold_text_only():
    msg = MessageBuilder().section("Заголовок").warning("Сильный ветер").build()

    assert "⚠️ Сильный ветер" in msg.text
    assert _entities_of_type(msg, "bold") == ["Заголовок", "Сильный ветер"]


@pytest.mark.unit
def test_warning_emoji_override():
    msg = MessageBuilder().warning("Ураган", emoji="🚨").build()

    assert msg.text.startswith("🚨 Ураган")


@pytest.mark.unit
def test_tip_uses_lightbulb_by_default():
    msg = MessageBuilder().tip("Возьми зонт").build()

    assert msg.text.startswith("💡 Возьми зонт")
    assert _entities_of_type(msg, "bold") == ["Возьми зонт"]


@pytest.mark.unit
def test_bullet_prefixes_with_bullet_mark():
    msg = MessageBuilder().bullet("первый пункт").bullet("второй пункт").build()

    assert msg.text == "• первый пункт\n• второй пункт\n"


@pytest.mark.unit
def test_divider_between_blocks_and_not_at_the_very_start():
    msg = MessageBuilder().line("до").divider().line("после").build()

    assert "\n\n" in msg.text
    assert msg.text.count("—") > 0

    fresh = MessageBuilder().divider().build()
    assert not fresh.text.startswith("\n")


@pytest.mark.unit
def test_spacer_adds_exactly_one_blank_line_regardless_of_existing_trailing_newlines():
    with_one_newline = MessageBuilder().text_line("a").newline().spacer().text_line("b").build()
    with_no_newline = MessageBuilder().text_line("a").spacer().text_line("b").build()

    assert with_one_newline.text == "a\n\nb"
    assert with_no_newline.text == "a\n\nb"


@pytest.mark.unit
def test_spacer_on_empty_builder_is_noop():
    msg = MessageBuilder().spacer().text_line("x").build()

    assert msg.text == "x"


@pytest.mark.unit
def test_emoji_before_bold_section_title_uses_correct_utf16_offsets():
    msg = MessageBuilder().section("🌍 Заголовок с эмодзи").line("текст").build()

    bold = _entities_of_type(msg, "bold")
    assert bold == ["🌍 Заголовок с эмодзи"]


@pytest.mark.unit
def test_build_stripped_trims_trailing_newline_without_breaking_entities():
    msg = MessageBuilder().section("Заголовок").line("текст").build_stripped()

    assert msg.text == "Заголовок\nтекст"
    bold = _entities_of_type(msg, "bold")
    assert bold == ["Заголовок"]


@pytest.mark.unit
def test_embed_shifts_entities_and_adds_blank_line_when_content_exists():
    sub = from_html("⚠️ <b>Штормовое предупреждение</b>\n\nОжидаются шквалы.")
    msg = MessageBuilder().section("Погода на завтра").line("До +18°C").embed(sub).build_stripped()

    assert msg.text == "Погода на завтра\nДо +18°C\n\n⚠️ Штормовое предупреждение\n\nОжидаются шквалы."
    bold = _entities_of_type(msg, "bold")
    assert bold == ["Погода на завтра", "Штормовое предупреждение"]


@pytest.mark.unit
def test_embed_on_empty_builder_has_no_leading_blank_line():
    sub = from_html("<b>Заголовок</b>\nтекст")
    msg = MessageBuilder().embed(sub).build()

    assert msg.text == "Заголовок\nтекст"
    assert _entities_of_type(msg, "bold") == ["Заголовок"]


@pytest.mark.unit
def test_embed_preserves_link_entity_url():
    from telegram import MessageEntity
    from ui.builder import MessageSpec

    sub = MessageSpec(text="перейти", entities=[MessageEntity(MessageEntity.TEXT_LINK, 0, 7, url="https://example.com")])
    msg = MessageBuilder().line("шапка").embed(sub).build()

    link_entities = [e for e in msg.entities if e.type == "text_link"]
    assert len(link_entities) == 1
    assert link_entities[0].url == "https://example.com"
