import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import settings
from fakes import RecordingBot


def _labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def test_settings_home_has_no_manual_refresh_button(monkeypatch):
    sent = []
    monkeypatch.setattr(settings.store, "get_settings", lambda _cid: {"city": "Алкмар"})
    monkeypatch.setattr(settings.store, "learning_is_enabled", lambda _cid: False)

    asyncio.run(settings.send_home(RecordingBot(sent), "42"))

    labels = _labels(sent[0]["reply_markup"])
    assert "🔄 Обновить" not in labels
    assert labels == [
        "📍 Выбрать город", "🧠 Выбрать язык обучения", "🔔 Уведомления", "📤 Экспорт данных", "#️⃣ Главная",
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


def test_settings_home_lists_enabled_notifications_and_language(monkeypatch):
    sent = []
    monkeypatch.setattr(settings.store, "get_settings", lambda _cid: {"city": "Алкмар"})
    monkeypatch.setattr(settings, "study_lang", lambda _cid: "нидерландский")
    monkeypatch.setattr(settings, "notif_on", lambda _cid, kind: kind == "evening_weather")

    asyncio.run(settings.send_home(RecordingBot(sent), "42"))

    text = sent[0]["text"]
    assert "📍 Город: Алкмар\n🧠 Язык обучения: Нидерландский\n🔔 Уведомления:\n- Погода на завтра · 20:00" in text
    callbacks = [b.callback_data for row in sent[0]["reply_markup"].inline_keyboard for b in row]
    assert "set_learning_global" in callbacks

    monkeypatch.setattr(settings, "notif_on", lambda _cid, _kind: False)
    asyncio.run(settings.send_home(RecordingBot(sent), "42"))
    assert sent[-1]["text"].endswith("🔔 Уведомления: выключены")
