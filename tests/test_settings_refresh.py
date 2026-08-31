import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import settings


def _labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def test_settings_home_has_no_manual_refresh_button(monkeypatch):
    sent = []
    monkeypatch.setattr(settings.store, "get_settings", lambda _cid: {"city": "Алкмар"})
    monkeypatch.setattr(settings.store, "learning_is_enabled", lambda _cid: False)

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    asyncio.run(settings.send_home(Bot(), "42"))

    labels = _labels(sent[0]["reply_markup"])
    assert "🔄 Обновить" not in labels
    assert labels == [
        "📍 Город", "🔔 Уведомления", "📤 Экспорт данных", "#️⃣ Главная",
    ]


def test_old_refresh_button_returns_to_current_settings(monkeypatch):
    edits = []
    monkeypatch.setattr(settings.store, "get_settings", lambda _cid: {"city": "Алкмар"})
    monkeypatch.setattr(settings.store, "learning_is_enabled", lambda _cid: False)

    class Message:
        async def edit_text(self, text, **kwargs):
            edits.append((text, kwargs))

    class Bot:
        async def send_message(self, **_kwargs):
            raise AssertionError("legacy callback must edit the existing message")

    query = type("Query", (), {"message": Message()})()
    asyncio.run(settings.handle_callback(Bot(), "42", "set_refresh_data", query))

    assert edits
    assert "Обновляю данные" not in edits[0][0]
    assert "🔄 Обновить" not in _labels(edits[0][1]["reply_markup"])
