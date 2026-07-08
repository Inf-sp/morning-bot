import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("CHAT_ID", "1")

import admin
import ui.admin as admin_ui


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class Message:
    def __init__(self):
        self.edited = []

    async def edit_text(self, **kwargs):
        self.edited.append(kwargs)


class Query:
    def __init__(self):
        self.message = Message()


def _button_texts(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_home_is_short_and_not_debuggy():
    bot = Bot()
    asyncio.run(admin.send_home(bot, "1"))
    payload = bot.sent[-1]
    text = payload["text"]
    buttons = _button_texts(payload["reply_markup"])

    assert "📊 Система" in text
    assert "🔔 Уведомления" in text
    assert "👥 Пользователи" in text
    assert "💾 Данные" in text
    assert "Tavily" not in text
    assert "Personal News" not in text
    assert "следующее уведомление" not in text.lower()
    assert "последние сбои" not in text.lower()
    assert "последний api" not in text.lower()
    assert "OpenWeather" not in text
    assert "LLM 87/0" not in text
    assert len([line for line in text.splitlines() if line.strip()]) <= 8

    flat = [text for row in buttons for text in row]
    assert "🔄 Проверить всё" in flat or "🔄 Проверить снова" in flat
    assert "📊 Система" in flat
    assert "👥 Пользователи" in flat
    assert "🔔 Уведомления" in flat
    assert "🧪 Тесты" in flat
    assert "☁️ API" not in flat
    assert "🤖 LLM" not in flat


def test_system_rows_are_one_service_per_status_line():
    rows = admin._system_rows()
    assert any("LLM" in row for row in rows)
    assert any("OpenWeather" in row and "/500 сегодня" in row for row in rows)
    assert any("Tavily" in row for row in rows)
    assert all(row.startswith((admin_ui.OK, admin_ui.WARN, admin_ui.BAD)) for row in rows)
    assert all("осталось" not in row.lower() for row in rows)


def test_diagnostics_buttons_are_inline():
    bot = Bot()
    asyncio.run(admin.send_diagnostics(bot, "1"))
    buttons = _button_texts(bot.sent[-1]["reply_markup"])
    assert buttons[0] == ["☁️ API", "🤖 LLM"]
    assert buttons[1] == ["🧠 Новости", "📜 Логи"]


def test_tests_screen_short_two_column_buttons_and_history_time_first():
    admin._TEST_HISTORY[:] = ["13:15 · Утро · OK"]
    bot = Bot()
    asyncio.run(admin.send_tests(bot, "1"))
    payload = bot.sent[-1]
    assert "13:15 · Утро · OK" in payload["text"]
    buttons = _button_texts(payload["reply_markup"])
    assert buttons[:4] == [
        ["☀️ Утро", "🌦 Погода"],
        ["📚 NL", "🇬🇧 EN"],
        ["🍽 Еда", "🎬 Досуг"],
        ["🧠 News", "✅ Все"],
    ]


def test_users_invite_first_and_no_last_activity():
    bot = Bot()
    asyncio.run(admin.send_users(bot, "1"))
    payload = bot.sent[-1]
    assert _button_texts(payload["reply_markup"])[0] == ["➕ Инвайт"]
    assert "Последняя активность" not in payload["text"]
    assert "chat_id" not in payload["text"]
    assert "user_id" not in payload["text"]


def test_logs_do_not_show_ok_events_when_empty(monkeypatch):
    monkeypatch.setattr(admin.tracking, "get_errors", lambda limit=200, source=None: [])
    bot = Bot()
    asyncio.run(admin.send_logs(bot, "1"))
    text = bot.sent[-1]["text"]
    assert "Ошибок за 24 часа нет" in text
    assert "OK" not in text


def test_admin_screen_callback_edits_existing_message():
    bot = Bot()
    query = Query()
    asyncio.run(admin.send_system(bot, "1", q=query))
    assert query.message.edited
    assert not bot.sent
