import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import admin
import api_usage
import settings
import tracking
from ui import admin as admin_ui


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
    monkeypatch.setattr(admin.service_monitor, "rows", lambda: ["⚪ Gemini · Везде · лимит неизвестен"])
    monkeypatch.setattr(admin.service_monitor, "last_check_time", lambda: "21:44")
    bot = _Bot()

    asyncio.run(admin.send_api_ai(bot, "42"))

    markup = bot.sent[0]["reply_markup"].inline_keyboard
    assert [button.text for button in markup[0]] == ["⚠️ Логи"]
    assert [button.text for button in markup[-1]] == ["⬅️ Назад", "#️⃣ Меню"]
    assert "Последняя ошибка" not in bot.sent[0]["text"]


def test_logs_have_only_clear_and_navigation_rows(monkeypatch):
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
    assert labels == [["❌ Очистить логи"], ["⬅️ Назад", "#️⃣ Меню"]]
    assert all("Скопировать" not in button for row in labels for button in row)


def test_admin_home_ui_uses_compact_exact_lines_without_ok():
    message = admin_ui.home(
        system_dot="🟡", system_text="Работает с ограничениями",
        system_line="3 сервиса ограничены · резерв включён",
        notif_line="отправлено 12 сегодня · ошибок нет",
        users_line="всего 4 · активны сегодня 2",
        data_line="подключение стабильно", logs_line="2 новые ошибки",
        updated_at="10:23", stale=False,
    )

    assert message.text == (
        "🛠️ Админ\n\n"
        "🟡 Работает с ограничениями\n\n"
        "📊 Система · 3 сервиса ограничены · резерв включён\n"
        "🔔 Уведомления · отправлено 12 сегодня · ошибок нет\n"
        "👨🏻‍💻 Пользователи · всего 4 · активны сегодня 2\n"
        "🗄 Данные · подключение стабильно\n"
        "⚠️ Логи · 2 новые ошибки\n\n"
        "Обновлено в 10:23"
    )
    assert "OK" not in message.text


def test_system_summary_counts_user_impact_and_not_replaced_api():
    states = [
        {"service": "gemini", "status": "warning", "fallback": "github_models"},
        {"service": "openweather", "status": "warning", "fallback": ""},
        {"service": "telegram", "status": "down", "fallback": ""},
        {"service": "database", "status": "down", "fallback": ""},
    ]

    summary = admin._system_summary(states)

    assert summary["line"] == "2 сервиса ограничены · резерв включён"
    assert summary["unavailable_functions"] == 0


def test_system_summary_deduplicates_unavailable_functions():
    states = [
        {"service": "gemini", "status": "down", "fallback": "", "error_type": "fallback"},
        {"service": "github_models", "status": "down", "fallback": "", "error_type": "auth"},
    ]

    summary = admin._system_summary(states)

    assert summary["line"] == "3 функции недоступны"
    assert summary["fallback_unavailable"] is True


def test_zero_user_metrics_are_hidden_from_home_line():
    assert admin._users_summary_line({"total": 4, "active_today": 0, "new_today": 0}) == "всего 4"


def test_log_cursor_hides_errors_after_logs_open(monkeypatch):
    now = 1_700_000_000
    errors = [
        {"id": "new", "ts": now, "source": "app", "kind": "ValueError"},
        {"id": "old", "ts": now - 1, "source": "app", "kind": "TypeError"},
    ]
    state = {}
    monkeypatch.setattr(admin.time, "time", lambda: now)
    monkeypatch.setattr(tracking, "get_errors", lambda limit=200: errors)
    monkeypatch.setattr(admin.store, "_load", lambda _key: state)

    def mutate(_key, fn):
        value, result = fn(state)
        if value is not state:
            state.clear()
            state.update(value)
        return result

    monkeypatch.setattr(admin.store, "mutate_kv", mutate)

    assert admin._new_log_errors("42")["count"] == 2
    admin._mark_logs_viewed("42", errors)
    assert admin._new_log_errors("42")["count"] == 0


class _FailingBot:
    async def send_message(self, **_kwargs):
        raise RuntimeError("Telegram failed")


def test_notification_tracking_records_failed_delivery(monkeypatch):
    requests = []
    errors = []
    monkeypatch.setattr(api_usage, "record_request", lambda *args, **kwargs: requests.append((args, kwargs)))
    monkeypatch.setattr(tracking, "log_error", lambda *args, **kwargs: errors.append((args, kwargs)))
    bot = settings._NotificationTrackingBot(_FailingBot(), "daily_words")

    try:
        asyncio.run(bot.send_message(chat_id="42", text="test"))
    except RuntimeError:
        pass

    assert requests[0][1]["units"] == {"requests": 0, "failures": 1}
    assert errors[0][0][0] == "broadcast"
    assert errors[0][1]["kind"] == "notif:daily_words"
