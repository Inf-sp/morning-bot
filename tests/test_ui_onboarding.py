import pytest

from ui import onboarding


@pytest.mark.unit
def test_firstvisit_prompt_message_spec():
    msg = onboarding.firstvisit_prompt("wardrobe")

    assert msg.parse_mode == "HTML"
    assert msg.text.startswith("👕 <b>Настроим гардероб</b>")
    assert "Размер M" in msg.text


@pytest.mark.unit
def test_firstvisit_saved_escapes_items():
    msg = onboarding.firstvisit_saved(["Стиль: <casual>"])

    assert msg.parse_mode == "HTML"
    assert "✅ <b>Сохранено</b>" in msg.text
    assert "Стиль: &lt;casual&gt;" in msg.text


@pytest.mark.unit
def test_onboard_messages():
    assert onboarding.onboard_start().parse_mode == "HTML"
    assert "Добро пожаловать" in onboarding.onboard_start().text
    assert "Алекс &lt;test&gt;" in onboarding.onboard_name_saved("Алекс <test>").text
    assert onboarding.onboard_level_question("en").text.startswith("🇬🇧")
    assert "Что для тебя сейчас важнее" in onboarding.onboard_priorities_question().text
