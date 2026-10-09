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

    rows = [[b.text for b in row] for row in sent[0]["reply_markup"].inline_keyboard]
    assert "🔄 Обновить" not in [label for row in rows for label in row]
    assert rows == [
        ["📍 Город", "🧠 Язык обучения"], ["🔔 Уведомления", "☀️ Мой день"],
        ["📰 Новости", "📤 Экспорт"], ["#️⃣ Главная"],
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


def test_settings_home_shows_city_language_and_summary(monkeypatch):
    sent = []
    monkeypatch.setattr(settings.store, "get_settings", lambda _cid: {"city": "Алкмар"})
    monkeypatch.setattr(settings, "study_lang", lambda _cid: "нидерландский")
    monkeypatch.setattr(settings, "notif_on", lambda _cid, kind: kind == "evening_weather")
    monkeypatch.setattr(settings, "cuisines", lambda _cid: ["italian"])
    monkeypatch.setattr(settings.store, "load_wardrobe", lambda _cid: {"zones": {}})

    asyncio.run(settings.send_home(RecordingBot(sent), "42"))

    text = sent[0]["text"]
    assert text == ("🎚️ Настройки\n\nГород: Алкмар\nЯзык обучения: Нидерландский\n"
                    "Уведомлений включено: 1 · Кухонь: 1 · Вещей в шкафу: 0")
    callbacks = [b.callback_data for row in sent[0]["reply_markup"].inline_keyboard for b in row]
    assert "set_learning_global" in callbacks
    assert "set_notif" in callbacks
