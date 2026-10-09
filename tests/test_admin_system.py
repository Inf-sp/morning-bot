import copy
import asyncio
import os
from datetime import datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import admin
import api_usage
import settings
import tracking
import verify
from ui import admin as admin_ui
from fakes import RecordingBot


def test_tracking_keeps_diagnostic_context_and_redacts_secrets(monkeypatch):
    state = {"log": []}
    monkeypatch.setattr(tracking.store, "_load", lambda _key: state)
    monkeypatch.setattr(tracking.store, "_save", lambda _key, value: state.update(value))
    _patch_mutate_kv(monkeypatch, tracking.store)
    monkeypatch.setattr(tracking.config, "APP_VERSION", "1.2.3")
    monkeypatch.setattr(tracking.config, "GEMINI_API_KEY", "secret-key-123456")

    try:
        raise NameError("learning_ui is not defined secret-key-123456")
    except NameError as exc:
        tracking.log_error(
            "app", str(exc), exc=exc, section="Обучение",
            action="не открылось задание", service="Groq", fallback="Gemini",
        )

    entry = state["log"][0]
    assert entry["section"] == "Обучение"
    assert entry["action"] == "не открылось задание"
    assert entry["error"].startswith("NameError:")
    assert entry["file"] == "test_admin_system.py"
    assert entry["line"] > 0
    assert entry["function"] == "test_tracking_keeps_diagnostic_context_and_redacts_secrets"
    assert entry["service"] == "Groq"
    assert entry["fallback"] == "Gemini"
    assert entry["version"] == "1.2.3"
    assert "Traceback" in entry["traceback"]
    assert "secret-key-123456" not in str(entry)
    assert "[REDACTED]" in entry["error"]



def test_old_system_callback_opens_admin_home_without_system_button(monkeypatch):
    monkeypatch.setattr(admin.service_monitor, "rows", lambda: ["⚪ Gemini · Везде · лимит неизвестен"])
    monkeypatch.setattr(admin.provider_runtime, "states", lambda: [])
    monkeypatch.setattr(admin, "_active_error_rows", lambda limit=5: [])
    bot = RecordingBot()

    asyncio.run(admin.send_api_ai(bot, "42"))

    markup = bot.sent[0]["reply_markup"].inline_keyboard
    assert bot.sent[0]["text"].startswith("🛠️ Админ")
    assert "⚪ Gemini · Везде · лимит неизвестен" in bot.sent[0]["text"]
    assert "API работают" not in bot.sent[0]["text"]
    assert all(button.text != "🛠 Система" for row in markup for button in row)
    assert [[button.text for button in row] for row in markup] == [
        ["🔄 Обновить карточки"], ["🩺 Проверить API"], ["👥 Пользователи"], ["#️⃣ Главная"],
    ]


def test_admin_card_refresh_menu_has_all_cards():
    bot = RecordingBot()

    asyncio.run(admin.send_card_refresh_menu(bot, "42"))

    markup = bot.sent[0]["reply_markup"].inline_keyboard
    assert [row[0].text for row in markup[:-1]] == [
        "☀️ Мой день", "👔 Гардероб", "🥣 Готовка", "🧠 Обучение", "🍿 Досуг",
    ]
    assert bot.sent[0]["text"].startswith("🔄 Обновить карточки")


def test_admin_refresh_card_reports_success(monkeypatch):
    calls = []

    async def refresh(cid, key):
        calls.append((cid, key))
        return True

    monkeypatch.setattr(admin, "_refresh_card_cache", refresh)
    bot = RecordingBot()

    asyncio.run(admin.refresh_card(bot, "42", "leisure"))

    assert calls == [("42", "leisure")]
    assert "✅ 🍿 Досуг обновлена." in bot.sent[0]["text"]


def test_logs_have_only_clear_and_navigation_rows(monkeypatch):
    entry = {
        "id": "abc123", "ts": 1_700_000_000, "source": "app",
        "section": "Обучение", "action": "не открылось задание",
        "error": "NameError: learning_ui is not defined",
        "file": "learning.py", "line": 248,
    }
    monkeypatch.setattr(tracking, "get_errors", lambda limit=200: [entry])
    monkeypatch.setattr(admin.time, "time", lambda: 1_700_000_100)
    bot = RecordingBot()

    asyncio.run(admin.send_logs(bot, "42"))

    labels = [[button.text for button in row] for row in bot.sent[0]["reply_markup"].inline_keyboard]
    assert labels == [["❌ Очистить ошибки"], ["⬅️ Назад", "#️⃣ Главная"]]
    assert all("Скопировать" not in button for row in labels for button in row)


def test_logs_hide_llm_provider_payload_and_code_location(monkeypatch):
    now = 1_784_466_000
    entry = {
        "id": "llm-1", "ts": now, "source": "llm",
        "section": "Ассистент", "action": "не сформирован ответ",
        "kind": "all-providers-failed",
        "error": (
            'groq_standard:groq_standard 400: {"error":{"message":'
            '"Failed to validate JSON. See failed_generation"}}; chain:deadline'
        ),
        "file": "ai.py", "line": 1334,
    }
    monkeypatch.setattr(admin.time, "time", lambda: now + 10)
    monkeypatch.setattr(admin.tracking, "get_errors", lambda limit=200: [entry])
    monkeypatch.setattr(admin.provider_runtime, "history", lambda limit=200: [])
    bot = RecordingBot()

    asyncio.run(admin.send_logs(bot, "42"))

    text = bot.sent[0]["text"]
    assert "Ассистент · не удалось подготовить ответ" in text
    assert "groq_standard" not in text
    assert "failed_generation" not in text
    assert "ai.py" not in text


def test_logs_keep_ai_chain_failures_separate_for_each_section(monkeypatch):
    now = int(datetime(2026, 8, 12, 13, 24, tzinfo=admin.config.TZ).timestamp())
    errors = [
        {
            "id": "llm-1", "ts": now, "source": "llm", "section": "Ассистент",
            "kind": "all-providers-failed",
            "error": "groq_standard: HTTP 429; openrouter: HTTP 503",
        },
        {
            "id": "llm-2", "ts": now - 90, "source": "llm", "section": "Питание",
            "kind": "all-providers-failed",
            "error": "groq_standard: rate limit; openrouter: timeout",
        },
    ]
    monkeypatch.setattr(admin.time, "time", lambda: now + 10)
    monkeypatch.setattr(admin.tracking, "get_errors", lambda limit=200: errors)
    monkeypatch.setattr(admin.provider_runtime, "history", lambda limit=200: [])
    bot = RecordingBot()

    asyncio.run(admin.send_logs(bot, "42"))

    text = bot.sent[0]["text"]
    assert text.count("Ассистент · не удалось подготовить ответ") == 1
    assert text.count("Питание · не удалось подготовить ответ") == 1
    assert "12 авг · 13:24" in text
    assert "причина: Groq — лимит; OpenRouter — сервис не ответил" in text
    assert "Разные категории" not in text
    assert "повторилось 2 раза" not in text


def test_expected_ai_outage_does_not_create_a_second_app_error(monkeypatch):
    logged = []

    class Bot:
        async def send_message(self, **_kwargs):
            return None

    monkeypatch.setattr(tracking, "log_error", lambda *args, **kwargs: logged.append((args, kwargs)))

    asyncio.run(verify.safe_error(
        Bot(), "42", Exception("Сейчас не удалось подготовить ответ. Попробуй ещё раз чуть позже."),
    ))

    assert logged == []


def test_json_serialization_error_is_logged_as_an_application_error(monkeypatch):
    logged = []

    class Bot:
        async def send_message(self, **_kwargs):
            return None

    monkeypatch.setattr(tracking, "log_error", lambda *args, **kwargs: logged.append((args, kwargs)))

    try:
        raise TypeError("Object of type set is not JSON serializable")
    except TypeError as exc:
        asyncio.run(verify.safe_error(Bot(), "42", exc))

    assert logged[0][0][0] == "app"


def test_logs_hide_monitor_incidents_resolved_by_recovery_or_fallback(monkeypatch):
    now = 1_784_466_000
    recovered = {
        "ts": now, "service": "groq", "event_type": "error",
        "text": "Groq: не удалось определить статус.", "status_code": 400,
        "latency_ms": 2071,
        "started_at": now - 90, "recovered_at": now,
    }
    fallback = {
        "ts": now - 1, "service": "gemini", "event_type": "error",
        "text": "Gemini: лимит исчерпан.", "status_code": 429,
        "fallback_target": "groq", "started_at": now - 60,
    }
    monkeypatch.setattr(admin.time, "time", lambda: now + 10)
    monkeypatch.setattr(admin.tracking, "get_errors", lambda limit=200: [])
    monkeypatch.setattr(admin.provider_runtime, "history", lambda limit=200: [recovered, fallback])
    bot = RecordingBot()

    asyncio.run(admin.send_logs(bot, "42"))

    text = bot.sent[0]["text"]
    assert "Система · Groq" not in text
    assert "Система · Gemini" not in text


def test_logs_collapse_duplicate_monitor_incidents_and_show_all_unique_rows(monkeypatch):
    now = 1_784_466_000
    app_errors = [{
        "id": f"app-{index}", "ts": now - 100 - index,
        "source": "app", "section": "Система", "action": f"действие {index}",
        "error": f"ValueError: ошибка {index}", "file": "bot.py", "line": 10 + index,
    } for index in range(13)]
    ticketmaster = [{
        "ts": now - index, "service": "ticketmaster", "event_type": "error",
        "incident_id": f"ticketmaster-{index}",
        "text": "Ticketmaster: лимит исчерпан.", "status_code": 429,
        "started_at": now - 60 - index,
    } for index in range(5)]
    monkeypatch.setattr(admin.time, "time", lambda: now)
    monkeypatch.setattr(admin.tracking, "get_errors", lambda limit=200: app_errors)
    monkeypatch.setattr(admin.provider_runtime, "history", lambda limit=200: ticketmaster)
    bot = RecordingBot()

    asyncio.run(admin.send_logs(bot, "42"))

    text = bot.sent[0]["text"]
    assert text.count("Система · Ticketmaster · слишком много запросов") == 1
    assert "повторилось 5 раз" in text
    assert "действие 12" in text
    assert "Ещё записей" not in text


def test_logs_collapse_duplicate_app_errors_and_keep_exact_message(monkeypatch):
    now = 1_784_466_000
    errors = [
        {"id": str(index), "ts": now - index, "source": "app", "kind": "ValueError",
         "error": "ValueError: exact failure", "file": "bot.py", "line": 42}
        for index in range(80)
    ]
    monkeypatch.setattr(admin.time, "time", lambda: now)
    monkeypatch.setattr(admin.tracking, "get_errors", lambda limit=200: errors)
    monkeypatch.setattr(admin.provider_runtime, "history", lambda limit=200: [])
    bot = RecordingBot()

    asyncio.run(admin.send_logs(bot, "42"))

    text = bot.sent[0]["text"]
    assert text.count("exact failure") == 1
    assert "повторилось 80 раз" in text
    assert len(text) < 4096


def test_logs_limit_total_message_size_for_many_unique_errors(monkeypatch):
    now = 1_784_466_000
    errors = [
        {"id": str(index), "ts": now - index, "source": "app", "kind": "Error",
         "error": f"Error: unique failure {index} " + ("x" * 220),
         "file": "bot.py", "line": index}
        for index in range(80)
    ]
    monkeypatch.setattr(admin.time, "time", lambda: now)
    monkeypatch.setattr(admin.tracking, "get_errors", lambda limit=200: errors)
    monkeypatch.setattr(admin.provider_runtime, "history", lambda limit=200: [])
    bot = RecordingBot()

    asyncio.run(admin.send_logs(bot, "42"))

    assert len(bot.sent[0]["text"]) < 4096
    assert "ошибок скрыто" in bot.sent[0]["text"]


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


def test_admin_home_ui_has_status_dots_and_no_errors_block():
    message = admin_ui.home(
        system_rows=[
            "AI", "🟢 Gemini · Основной · доступен",
            "Данные", "🟢 TMDB · Кино · доступен", "NS · Поезда · 0 сегодня",
        ],
        error_rows=["21 августа, 15:14 · Обучение · не открылось задание"],
    )

    assert message.text == (
        "🛠️ Админ\n\n"
        "AI\n"
        "🟢 Gemini · Основной · доступен\n\n"
        "Данные\n"
        "🟢 TMDB · Кино · доступен\n"
        "⚪ NS · Поезда · 0 сегодня"
    )


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


def _patch_mutate_kv(monkeypatch, store):
    """mutate_kv поверх уже подменённых в тесте _load/_save."""
    def mutate(key, change):
        value, result = change(copy.deepcopy(store._load(key) or {}))
        store._save(key, value)
        return result

    monkeypatch.setattr(store, "mutate_kv", mutate)


def test_api_check_rows_format_ok_fail_limit_with_comma_latency():
    msg = admin_ui.api_check([
        {"label": "Gemini", "status": "ok", "seconds": 0.83, "detail": ""},
        {"label": "Groq", "status": "fail", "seconds": 3.0, "detail": "ошибка авторизации"},
        {"label": "SerpApi", "status": "fail", "seconds": None, "detail": "ключ не настроен"},
        {"label": "Tavily", "status": "limit", "seconds": None, "detail": "лимит исчерпан"},
        {"label": "YouTube", "status": "ok", "seconds": None, "detail": "по реальным запросам"},
    ])

    assert msg.text.splitlines() == [
        "🩺 Проверка API", "",
        "✅ Gemini · 0,8 с",
        "❌ Groq · 3,0 с · ошибка авторизации",
        "❌ SerpApi · ключ не настроен",
        "⚠️ Tavily · лимит исчерпан",
        "✅ YouTube · по реальным запросам",
    ]


def test_api_check_screen_shows_results_and_buttons(monkeypatch):
    async def fake_check_all():
        return [{"label": "Gemini", "status": "ok", "seconds": 1.24, "detail": ""}]

    monkeypatch.setattr(admin.service_monitor, "live_check_all", fake_check_all)
    bot = RecordingBot()

    asyncio.run(admin.send_api_check(bot, "42"))

    assert "✅ Gemini · 1,2 с" in bot.sent[0]["text"]
    markup = bot.sent[0]["reply_markup"].inline_keyboard
    assert [[(b.text, b.callback_data) for b in row] for row in markup] == [
        [("🩺 Проверить снова", "adm_api_check")], [("⬅️ Назад", "adm_home"), ("#️⃣ Главная", "m_menu")],
    ]


def test_api_check_callback_is_admin_only(monkeypatch):
    called = []

    async def fake_send(bot, cid, q=None):
        called.append(cid)

    monkeypatch.setattr(admin, "send_api_check", fake_send)
    monkeypatch.setattr(settings.config, "CHAT_ID", "1")
    bot = RecordingBot()

    asyncio.run(settings.handle_callback(bot, "999", "adm_api_check", None))
    assert called == []
    asyncio.run(settings.handle_callback(bot, "1", "adm_api_check", None))
    assert called == ["1"]
