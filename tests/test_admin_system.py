import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import admin
import tracking
from ui import admin as admin_ui


def _snapshot(service, **values):
    return {"services": [{"service": service, **values}]}


def test_green_service_uses_section_and_short_usage_without_status_noise(monkeypatch):
    monkeypatch.setattr(admin, "_configured_service", lambda _service: True)
    snapshot = _snapshot(
        "cohere", status="ok", day_requests=3,
        quotas=[{"period": "day", "used": 3, "limit": 1000}],
    )

    line = admin._system_service_line("cohere", "Cohere", "Обучение", "Gemini", snapshot)

    assert line == "🟢 Cohere · Обучение · 3 из 1 000"
    assert all(word not in line for word in ("доступен", "работает", "активен", "в норме"))


def test_failed_service_with_fallback_is_yellow_and_hides_http_code(monkeypatch):
    monkeypatch.setattr(admin, "_configured_service", lambda _service: True)
    snapshot = _snapshot("gemini", status="bad", last_error_reason="HTTP 429")

    line = admin._system_service_line(
        "gemini", "Gemini", "Разные категории", "GitHub Models", snapshot,
    )

    assert line == (
        "🟡 Gemini · Разные категории · лимит запросов · используется GitHub Models"
    )
    assert "429" not in line


def test_service_without_working_fallback_can_be_red(monkeypatch):
    monkeypatch.setattr(admin, "_configured_service", lambda _service: True)
    snapshot = _snapshot("telegram", status="bad", last_error_reason="connection error")

    line = admin._system_service_line("telegram", "Telegram", "Сообщения", "", snapshot)

    assert line == "🔴 Telegram · Сообщения · нет подключения"


def test_system_ui_has_no_last_raw_error_block():
    message = admin_ui.api_ai(["🟢 Cohere · Обучение · 3 из 1 000"], "21:44")

    assert message.text.startswith("🛠 Система\n\nАвтоматический резерв: включён")
    assert "Последняя ошибка" not in message.text
    assert message.text.endswith("Обновлено в 21:44")


def test_tracking_keeps_diagnostic_context_and_redacts_secrets(monkeypatch):
    state = {"log": []}
    monkeypatch.setattr(tracking.store, "_load", lambda _key: state)
    monkeypatch.setattr(tracking.store, "_save", lambda _key, value: state.update(value))
    monkeypatch.setattr(tracking.config, "APP_VERSION", "1.2.3")
    monkeypatch.setattr(tracking.config, "GEMINI_API_KEY", "secret-key-123456")

    try:
        raise NameError("learning_ui is not defined secret-key-123456")
    except NameError as exc:
        tracking.log_error(
            "app", str(exc), exc=exc, section="Обучение",
            action="не открылось задание", service="Cohere", fallback="Gemini",
        )

    entry = state["log"][0]
    assert entry["section"] == "Обучение"
    assert entry["action"] == "не открылось задание"
    assert entry["error"].startswith("NameError:")
    assert entry["file"] == "test_admin_system.py"
    assert entry["line"] > 0
    assert entry["function"] == "test_tracking_keeps_diagnostic_context_and_redacts_secrets"
    assert entry["service"] == "Cohere"
    assert entry["fallback"] == "Gemini"
    assert entry["version"] == "1.2.3"
    assert "Traceback" in entry["traceback"]
    assert "secret-key-123456" not in str(entry)
    assert "[REDACTED]" in entry["error"]


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


def test_system_screen_has_logs_on_separate_row(monkeypatch):
    monkeypatch.setattr(admin.api_usage, "snapshot", lambda: {"services": []})
    monkeypatch.setattr(admin, "_remote_stat", lambda _service: "")
    monkeypatch.setattr(admin, "_configured_service", lambda _service: True)
    monkeypatch.setattr(admin.config, "DATABASE_URL", "postgres://configured")
    bot = _Bot()

    asyncio.run(admin.send_api_ai(bot, "42"))

    markup = bot.sent[0]["reply_markup"].inline_keyboard
    assert [button.text for button in markup[0]] == ["⚠️ Логи"]
    assert [button.text for button in markup[-1]] == ["⬅️ Назад", "#️⃣ Меню"]
    assert "Последняя ошибка" not in bot.sent[0]["text"]


def test_logs_have_copy_clear_and_navigation_rows(monkeypatch):
    entry = {
        "id": "abc123", "ts": 1_700_000_000, "source": "app",
        "section": "Обучение", "action": "не открылось задание",
        "error": "NameError: learning_ui is not defined",
        "file": "learning.py", "line": 248,
    }
    monkeypatch.setattr(tracking, "get_errors", lambda limit=200: [entry])
    monkeypatch.setattr(admin.time, "time", lambda: 1_700_000_100)
    bot = _Bot()

    asyncio.run(admin.send_logs(bot, "42"))

    labels = [[button.text for button in row] for row in bot.sent[0]["reply_markup"].inline_keyboard]
    assert labels[0][0].startswith("📋 Скопировать · ")
    assert labels[1] == ["❌ Очистить логи"]
    assert labels[2] == ["⬅️ Назад", "#️⃣ Меню"]


def test_copy_log_sends_full_safe_context(monkeypatch):
    entry = {
        "id": "abc123", "ts": 1_700_000_000, "source": "app",
        "section": "Обучение", "action": "не открылось задание",
        "error": "NameError: learning_ui is not defined", "traceback": "full traceback",
        "file": "learning.py", "line": 248, "function": "send_task",
        "service": "Cohere", "fallback": "Gemini", "version": "1.2.3",
    }
    monkeypatch.setattr(tracking, "get_errors", lambda limit=200: [entry])
    bot = _Bot()

    asyncio.run(admin.send_log_copy(bot, "42", "abc123"))

    text = bot.sent[0]["text"]
    for label in ("Время:", "Раздел:", "Действие:", "Ошибка:", "Traceback:",
                  "Файл:", "Строка:", "Функция:", "Сервис:", "Резерв:", "Версия:"):
        assert label in text
