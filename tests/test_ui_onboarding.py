import pytest

from ui import onboarding


def _slice_u16(text, offset, length):
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


def _entities_of_type(msg, entity_type):
    return [_slice_u16(msg.text, e.offset, e.length) for e in msg.entities if e.type == entity_type]


@pytest.mark.unit
def test_firstvisit_prompt_message_spec():
    msg = onboarding.firstvisit_prompt("wardrobe")

    assert msg.text.startswith("👕 Настроим гардероб")
    assert "Размер M" in msg.text
    assert _entities_of_type(msg, "bold") == ["👕 Настроим гардероб"]
    assert _entities_of_type(msg, "italic") == [
        "Пример: Люблю минимализм и оверсайз. Uniqlo, Nike. Размер M, обувь EU 43, брюки W32 L32"
    ]


@pytest.mark.unit
def test_firstvisit_prompt_section_title_immediately_followed_by_content():
    msg = onboarding.firstvisit_prompt("learning")

    assert msg.text == (
        "📚 Настроим обучение\n"
        "Какие языки изучаешь и какой у тебя уровень?\n\n"
        "Пример: нидерландский A2, английский B1"
    )
    assert "\n\n\n" not in msg.text


@pytest.mark.unit
def test_firstvisit_saved_escapes_items():
    msg = onboarding.firstvisit_saved(["Стиль: <casual>"])

    assert "✅ Сохранено" in msg.text
    assert "Стиль: <casual>" in msg.text
    assert _entities_of_type(msg, "bold") == ["✅ Сохранено"]


@pytest.mark.unit
def test_firstvisit_saved_lists_items_as_bullets():
    msg = onboarding.firstvisit_saved(["первое", "второе"])

    assert msg.text == "✅ Сохранено\n• первое\n• второе"


@pytest.mark.unit
def test_onboard_start_is_stripped_and_bold_title():
    msg = onboarding.onboard_start()

    assert msg.text == (
        "👋 Добро пожаловать!\n"
        "Давай познакомимся — это займёт меньше минуты, и бот сразу будет знать тебя.\n\n"
        "Как тебя зовут?"
    )
    assert _entities_of_type(msg, "bold") == ["👋 Добро пожаловать!"]


@pytest.mark.unit
def test_onboard_messages():
    assert "Добро пожаловать" in onboarding.onboard_start().text
    assert "Алекс <test>" in onboarding.onboard_name_saved("Алекс <test>").text
    assert onboarding.onboard_level_question("en").text.startswith("🇬🇧")
    assert "Что для тебя сейчас важнее" in onboarding.onboard_priorities_question().text


@pytest.mark.unit
def test_onboard_name_saved_bolds_only_the_name():
    msg = onboarding.onboard_name_saved("Алекс")

    assert _entities_of_type(msg, "bold") == ["Алекс"]
    assert msg.text == (
        "Приятно познакомиться, Алекс! 🙌\n\n"
        "🌍 Из какого ты города? Напиши текстом — настрою погоду и контекст для советов."
    )
