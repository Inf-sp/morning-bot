import copy
import asyncio
import os

import pytest
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
        ["🔄 Обновить карточки"], ["💾 Прислать копию базы"], ["🔁 Перезапустить бота"],
        ["👥 Пользователи"], ["#️⃣ Главная"],
    ]  # админка — все кнопки в один столбец
    assert all(button.callback_data != "adm_logs" for row in markup for button in row)  # ошибок на экране нет


def test_admin_card_refresh_menu_has_all_cards():
    bot = RecordingBot()

    asyncio.run(admin.send_card_refresh_menu(bot, "42"))

    markup = bot.sent[0]["reply_markup"].inline_keyboard
    assert [row[0].text for row in markup[:-2]] == [
        "☀️ Мой день", "👔 Гардероб", "🥣 Кухня", "🧠 Обучение", "🍿 Досуг",
    ]
    assert all(len(row) == 1 for row in markup)  # один столбец, «Назад» и «Главная» — отдельными строками
    assert bot.sent[0]["text"].startswith("🔄 Обновить карточки")


def test_admin_refresh_card_reports_success(monkeypatch):
    calls = []

    async def refresh(cid, key, bot=None):
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
    assert labels == [["❌ Очистить ошибки"], ["⬅️ Назад"], ["#️⃣ Главная"]]
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
        [("🩺 Проверить снова", "adm_api_check")], [("⬅️ Назад", "adm_home")], [("#️⃣ Главная", "m_menu")],
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


def _put(key, value):
    admin.store.mutate_kv(key, lambda _data: (value, None))


def test_admin_refresh_leisure_resets_cards_of_the_day(monkeypatch):
    import leisure_concerts
    import leisure_hub
    import leisure_movies

    _put(admin.config.MOVIE_RECO_CACHE_KEY, {"42": {"item": "film"}, "42|tv": {"item": "show"}, "7": {"item": "x"}})
    _put(admin.config.MUSIC_RECO_CACHE_KEY, {"42": {"item": "artist"}})
    monkeypatch.setattr(leisure_concerts, "invalidate_user_concerts_cache", lambda _cid: None)
    monkeypatch.setattr(leisure_movies, "warm_movie_premieres_cache", lambda _cid: asyncio.sleep(0))
    monkeypatch.setattr(leisure_hub, "warm_hub_cache", lambda _cid: asyncio.sleep(0, result=True))

    assert asyncio.run(admin._refresh_card_cache("42", "leisure")) is True
    assert admin.store._load(admin.config.MOVIE_RECO_CACHE_KEY) == {"7": {"item": "x"}}
    assert admin.store._load(admin.config.MUSIC_RECO_CACHE_KEY) == {}


def test_admin_refresh_card_names_failed_leisure_steps(monkeypatch):
    import leisure_hub

    async def refresh(cid, key, bot=None):
        leisure_hub.failed_steps[cid] = ["концерты", "артист дня"]
        return False

    monkeypatch.setattr(admin, "_refresh_card_cache", refresh)
    bot = RecordingBot()

    asyncio.run(admin.refresh_card(bot, "42", "leisure"))

    assert "⚠️ 🍿 Досуг: не обновились — концерты, артист дня" in bot.sent[0]["text"]


def test_night_line_lists_failed_sections_and_reserve_artist(monkeypatch):
    import bot_maintenance
    import leisure_hub

    _put(admin.config.NIGHT_WARM_KEY, {})
    _put(admin.config.MUSIC_RECO_CACHE_KEY, {})
    assert admin.night_line("42") is None

    bot_maintenance._save_night({"wardrobe": None, "cooking": None})
    assert admin.night_line("42") == "🌙 Ночь: всё собралось"

    monkeypatch.setitem(leisure_hub.failed_steps, "42", ["концерты"])
    night = {"wardrobe": None}
    bot_maintenance._night_failed(night, "leisure", "42")
    bot_maintenance._night_failed(night, "cooking", "42")
    bot_maintenance._save_night(night)
    _put(admin.config.MUSIC_RECO_CACHE_KEY, {"42": {"item": {"artist": "Kehlani", "source": "related"}}})

    assert admin.night_line("42") == (
        "🌙 Ночь: не собралось — Кухня, Досуг (концерты) · артист дня — похожий на артистов твоих жанров, без AI"
    )


def test_server_line_shows_database_backup_and_uptime_and_skips_missing(monkeypatch, tmp_path):
    database = tmp_path / "bot.db"
    database.write_bytes(b"x" * 3 * 1024 * 1024)
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "bot-2026-10-16.db").write_bytes(b"x")
    monkeypatch.setattr(admin.config, "DATABASE_PATH", str(database))
    monkeypatch.setattr(admin.config, "BACKUP_DIR", str(backups))
    monkeypatch.setattr(admin.deploy_report, "_STARTED_AT",
                        datetime.now(admin.config.TZ) - admin.timedelta(days=2, hours=4, minutes=5))
    monkeypatch.setattr(admin, "_memory_part", lambda: (_ for _ in ()).throw(OSError("no /proc")))

    line = admin.server_line()

    assert line.startswith("🖥 Диск ")
    assert "память" not in line
    assert " · база 3 МБ · копия базы сегодня " in line
    assert line.endswith(" · работает 2 дн. 4 ч")


class _DocumentBot(RecordingBot):
    def __init__(self):
        super().__init__()
        self.documents = []

    async def send_document(self, **kwargs):
        self.documents.append((kwargs["filename"], kwargs["document"].read(), kwargs["caption"]))


def test_backup_sends_consistent_sqlite_snapshot_or_explains_limit(monkeypatch, tmp_path):
    import sqlite3

    database = tmp_path / "bot.db"
    with sqlite3.connect(database) as connection:
        connection.execute("create table kv (key text, value text)")
        connection.execute("insert into kv values ('a', 'b')")
    monkeypatch.setattr(admin.config, "DATABASE_PATH", str(database))
    bot = _DocumentBot()

    asyncio.run(admin.send_backup(bot, "42"))

    name, body, caption = bot.documents[0]
    copy_path = tmp_path / name
    copy_path.write_bytes(body)
    with sqlite3.connect(copy_path) as connection:
        assert connection.execute("select * from kv").fetchall() == [("a", "b")]
    assert caption == "💾 Копия базы · 1 МБ"

    monkeypatch.setattr(admin, "_TELEGRAM_FILE_LIMIT", 10)
    asyncio.run(admin.send_backup(bot, "42"))
    assert len(bot.documents) == 1
    assert "больше лимита Telegram в 50 МБ" in bot.sent[-1]["text"]


def test_restart_refuses_without_systemd_auto_restart(monkeypatch):
    calls = []

    class Result:
        stdout = "no\n"

    monkeypatch.setattr(admin.subprocess, "run", lambda args, **_kwargs: calls.append(args) or Result())
    monkeypatch.setattr(admin.os, "_exit", lambda code: pytest.fail("must not exit"))
    bot = RecordingBot()

    asyncio.run(admin.restart(bot, "42", int(admin.time.time())))

    assert calls == [["systemctl", "show", "morning-bot", "-p", "Restart", "--value"]]
    assert "Перезапуск недоступен: сервис не поднимется сам." in bot.sent[0]["text"]


def test_restart_ignores_press_repeated_from_previous_run(monkeypatch):
    monkeypatch.setattr(admin, "_auto_restart_enabled", lambda: pytest.fail("must not check"))
    monkeypatch.setattr(admin, "send_home", lambda bot, cid, q=None: bot.send_message(chat_id=cid, text="home"))
    bot = RecordingBot()

    asyncio.run(admin.restart(bot, "42", 0))

    assert bot.sent[0]["text"] == "home"


def test_refresh_my_day_sends_fresh_card(monkeypatch):
    import asyncio
    import admin
    import myday
    calls = []

    async def refresh(cid, key, bot=None):
        calls.append(("refresh", key, bot is not None))
        return True

    async def menu(_bot, _cid, _q=None, status=""):
        calls.append(("menu", status))

    async def plany(_bot, cid, **_kwargs):
        calls.append(("plany", cid))

    monkeypatch.setattr(admin, "_refresh_card_cache", refresh)
    monkeypatch.setattr(admin, "send_card_refresh_menu", menu)
    monkeypatch.setattr(myday, "send_plany", plany)
    asyncio.run(admin.refresh_card(object(), "42", "myday"))
    assert calls == [("refresh", "myday", True), ("menu", "✅ ☀️ Мой день обновлена."), ("plany", "42")]
