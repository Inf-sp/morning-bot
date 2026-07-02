import pytest

from ui import settings


@pytest.mark.unit
def test_settings_core_messages():
    assert settings.notifications().parse_mode == "HTML"
    assert "🔔 <b>Уведомления</b>" in settings.notifications().text
    assert "<b>Сейчас:</b> здоровье" in settings.priorities("здоровье").text
    assert "🎚️ <b>Настройки</b>" in settings.settings_home().text
    assert "🍿 <b>Настройки досуга</b>" in settings.leisure_settings().text


@pytest.mark.unit
def test_settings_body_profile_message():
    msg = settings.body_profile("рост 178")

    assert msg.parse_mode == "HTML"
    assert "<b>Сейчас сохранено:</b>\nрост 178" in msg.text
    assert "Напиши одним сообщением" in msg.text
