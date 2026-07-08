import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")

import bot
import config
import store
from telegram import MessageEntity


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, entities=None, **kwargs):
        self.messages.append({"chat_id": chat_id, "text": text, "entities": entities})


def _isolate_deploy_store(monkeypatch):
    mem = {}

    def load(key):
        return mem.get(key, {})

    def save(key, data):
        mem[key] = dict(data)

    monkeypatch.setattr(store, "_load", load)
    monkeypatch.setattr(store, "_save", save)
    return mem


def _write_release_notes(tmp_path):
    (tmp_path / "RELEASE_NOTES.md").write_text(
        "## v1.8.2 · Умнее новости\n\n"
        "Новости для тебя: убраны служебные сообщения, добавлен поиск по стране "
        "и fallback на найденные источники, чтобы раздел не показывал пустую карточку.\n\n"
        "## v1.8.3 · Чистые обновления\n\n"
        "Новости стали аккуратнее: бот показывает только полезный итог обновления.\n",
        encoding="utf-8",
    )


def test_new_version_sends_once_and_repeat_is_skipped(monkeypatch, tmp_path):
    _isolate_deploy_store(monkeypatch)
    _write_release_notes(tmp_path)
    monkeypatch.setattr(bot, "_ROOT", tmp_path)
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "42")
    monkeypatch.setattr(config, "APP_VERSION", "1.8.2")

    fake = FakeBot()
    asyncio.run(bot.maybe_send_admin_deploy_notification(fake))
    asyncio.run(bot.maybe_send_admin_deploy_notification(fake))

    assert len(fake.messages) == 1
    text = fake.messages[0]["text"]
    assert text.splitlines()[0] == "🚀 v1.8.2 · Умнее новости"
    assert "Новости для тебя: убраны служебные сообщения" in text
    assert "Версия:" not in text
    assert "Статус:" not in text
    assert "Что изменилось:" not in text
    assert "Деплой прошёл успешно" not in text
    assert "Что проверить" not in text
    assert "Проверка API" not in text
    assert "Deploy-уведомление стало надёжнее" not in text
    assert text.splitlines()[-1] == "Бот развёрнут и работает ✅"
    assert any(entity.type == MessageEntity.BLOCKQUOTE for entity in fake.messages[0]["entities"])


def test_version_change_sends_again_once(monkeypatch, tmp_path):
    mem = _isolate_deploy_store(monkeypatch)
    _write_release_notes(tmp_path)
    monkeypatch.setattr(bot, "_ROOT", tmp_path)
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "42")

    fake = FakeBot()
    monkeypatch.setattr(config, "APP_VERSION", "1.8.2")
    asyncio.run(bot.maybe_send_admin_deploy_notification(fake))
    monkeypatch.setattr(config, "APP_VERSION", "1.8.3")
    asyncio.run(bot.maybe_send_admin_deploy_notification(fake))
    asyncio.run(bot.maybe_send_admin_deploy_notification(fake))

    assert len(fake.messages) == 2
    assert fake.messages[1]["text"].splitlines()[0] == "🚀 v1.8.3 · Чистые обновления"
    assert mem[config.DEPLOY_REPORT_KEY]["last_admin_deploy_notified_version"] == "1.8.3"


def test_missing_release_note_uses_fallback(monkeypatch, tmp_path):
    _isolate_deploy_store(monkeypatch)
    _write_release_notes(tmp_path)
    monkeypatch.setattr(bot, "_ROOT", tmp_path)
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "42")
    monkeypatch.setattr(config, "APP_VERSION", "1.9.0")

    fake = FakeBot()
    asyncio.run(bot.maybe_send_admin_deploy_notification(fake))

    assert len(fake.messages) == 1
    assert fake.messages[0]["text"].splitlines()[0] == "🚀 v1.9.0 · Обновление"
    assert "Бот получил небольшие внутренние улучшения." in fake.messages[0]["text"]


def test_multiple_release_notes_use_two_quote_bullets():
    msg = bot.build_deploy_report_message(
        "1.8.7",
        [
            "Уведомления стали короче и чище.",
            "История обновлений больше не показывает повторы.",
            "Лишние технические детали скрыты.",
        ],
    )

    assert "• Уведомления стали короче и чище." in msg.text
    assert "• История обновлений больше не показывает повторы." in msg.text
    assert "Лишние технические детали скрыты." not in msg.text
    assert any(entity.type == MessageEntity.BLOCKQUOTE for entity in msg.entities)


def test_version_file_is_used_when_env_app_version_is_empty(monkeypatch, tmp_path):
    _isolate_deploy_store(monkeypatch)
    _write_release_notes(tmp_path)
    (tmp_path / "VERSION").write_text("1.8.3\n", encoding="utf-8")
    monkeypatch.setattr(bot, "_ROOT", tmp_path)
    monkeypatch.setattr(config, "_HERE", tmp_path)
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "42")
    monkeypatch.setattr(config, "APP_VERSION", "")

    fake = FakeBot()
    asyncio.run(bot.maybe_send_admin_deploy_notification(fake))

    assert len(fake.messages) == 1
    assert fake.messages[0]["text"].splitlines()[0] == "🚀 v1.8.3 · Чистые обновления"
    assert "Новости стали аккуратнее" in fake.messages[0]["text"]


def test_missing_app_version_and_version_file_skips_notification(monkeypatch, tmp_path):
    mem = _isolate_deploy_store(monkeypatch)
    _write_release_notes(tmp_path)
    monkeypatch.setattr(bot, "_ROOT", tmp_path)
    monkeypatch.setattr(config, "_HERE", tmp_path)
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "42")
    monkeypatch.setattr(config, "APP_VERSION", "")

    fake = FakeBot()
    asyncio.run(bot.maybe_send_admin_deploy_notification(fake))

    assert fake.messages == []
    assert mem == {}
