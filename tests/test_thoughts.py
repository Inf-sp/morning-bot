import asyncio
from datetime import datetime
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import secure
import settings
import saved_items
import thoughts
import thoughts_knowledge


class FakeRepo:
    def __init__(self):
        self.items = []

    def all(self):
        return [dict(item) for item in self.items]

    def save(self, items):
        self.items = [dict(item) for item in items]

    def mutate(self, function):
        updated, result = function([dict(item) for item in self.items])
        self.items = [dict(item) for item in updated]
        return result


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=len(self.sent), reply_markup=kwargs.get("reply_markup"))


class FakeMessage:
    def __init__(self, message_id=10):
        self.message_id = message_id
        self.text = ""
        self.reply_markup = None
        self.deleted = False

    async def edit_text(self, text, **kwargs):
        self.text = text
        self.reply_markup = kwargs.get("reply_markup")

    async def delete(self):
        self.deleted = True


class FakeQuery:
    def __init__(self):
        self.message = FakeMessage()

    async def edit_message_reply_markup(self, reply_markup=None):
        self.message.reply_markup = reply_markup


def _button_count(markup):
    return sum(len(row) for row in markup.inline_keyboard)


def _setup_state(monkeypatch):
    repo = FakeRepo()
    repo.reviews = FakeRepo()
    settings_state = {}
    fixed_now = datetime(2026, 7, 16, 14, 0, tzinfo=config.TZ)
    monkeypatch.setattr(thoughts, "_repo", lambda _cid: repo)
    monkeypatch.setattr(thoughts, "_review_repo", lambda _cid: repo.reviews)
    monkeypatch.setattr(thoughts, "_now", lambda: fixed_now)
    monkeypatch.setattr(
        thoughts.settings,
        "get",
        lambda cid, key, default=None: settings_state.get((str(cid), key), default),
    )
    monkeypatch.setattr(
        thoughts.settings,
        "set_",
        lambda cid, key, value: settings_state.__setitem__((str(cid), key), value),
    )
    return repo, settings_state, fixed_now


def test_empty_home_has_no_review_button(monkeypatch):
    _setup_state(monkeypatch)
    bot = FakeBot()

    asyncio.run(thoughts.send_home(bot, "42"))

    message = bot.sent[0]
    assert message["text"] == (
        "😮‍💨 Мысли\n\n"
        "Не держи всё в голове.\n"
        "Напиши мысль, задачу или тревогу одним сообщением.\n\n"
        "Сейчас в голове:\n"
        "Список пуст.\n\n"
        "Сегодня записано: 0"
    )
    assert message["transient"] is True
    assert [button.text for row in message["reply_markup"].inline_keyboard for button in row] == [
        "⬅️ Назад", "#️⃣ Меню"
    ]


def test_home_shows_active_thoughts_and_review_button(monkeypatch):
    repo, _settings, fixed_now = _setup_state(monkeypatch)
    repo.items = [
        {"id": "1", "text": "Кажется, я не успею закончить всё сегодня.", "status": "open", "date": "2026-07-16"},
        {"id": "2", "text": "Нужно купить фильтры для фонтана.", "status": "open", "date": "2026-07-16"},
        {"id": "3", "text": "Не забыть ответить на сообщение.", "status": "open", "date": "2026-07-16"},
    ]
    bot = FakeBot()

    asyncio.run(thoughts.send_home(bot, "42"))

    text = bot.sent[0]["text"]
    assert "Сейчас в голове:\n• Кажется, я не успею" in text
    assert "• Нужно купить фильтры для фонтана." in text
    assert "Сегодня записано: 3" in text
    labels = [button.text for row in bot.sent[0]["reply_markup"].inline_keyboard for button in row]
    assert labels == ["✨ Разобрать мысли", "⬅️ Назад", "#️⃣ Меню"]


def test_crisis_and_medical_have_priority_without_model(monkeypatch):
    async def fail_model(*_args, **_kwargs):
        raise AssertionError("model must not override safety rules")

    monkeypatch.setattr(thoughts.ai, "allm_json", fail_model)

    crisis = asyncio.run(thoughts.classify("Я хочу причинить вред себе"))
    medical = asyncio.run(thoughts.classify("У меня боль в груди и одышка"))

    assert crisis["type"] == "crisis"
    assert crisis["requires_safety_response"] is True
    assert medical["type"] == "medical"
    assert medical["can_be_reviewed_later"] is False


def test_capture_preserves_original_text_and_hidden_fields(monkeypatch):
    repo, _settings, _fixed_now = _setup_state(monkeypatch)
    bot = FakeBot()

    async def classify(_text):
        return {
            "type": "practical_problem",
            "confidence": 0.87,
            "urgency": "medium",
            "can_be_actioned": True,
            "can_be_reviewed_later": True,
            "requires_safety_response": False,
        }

    monkeypatch.setattr(thoughts, "classify", classify)
    original = "  Нужно подготовиться к экзамену.  "

    asyncio.run(thoughts.capture(bot, "42", original))

    assert repo.items[0]["text"] == original
    assert repo.items[0]["type"] == "practical_problem"
    assert repo.items[0]["confidence"] == 0.87
    assert repo.items[0]["can_be_actioned"] is True
    assert bot.sent[0]["text"].startswith("✅ Сохранено")
    assert original in bot.sent[0]["text"]
    assert bot.sent[0]["transient"] is True


def test_crisis_capture_uses_direct_netherlands_protocol(monkeypatch):
    repo, settings_state, _fixed_now = _setup_state(monkeypatch)
    bot = FakeBot()

    asyncio.run(thoughts.capture(bot, "42", "Хочу причинить вред себе"))

    assert repo.items[0]["status"] == "routed"
    assert repo.items[0]["type"] == "crisis"
    assert bot.sent[-1]["text"] == secure.CRISIS_MSG
    assert "112" in bot.sent[-1]["text"]
    assert "0800-0113" in bot.sent[-1]["text"]
    assert settings_state[("42", "_thoughts_safety_date")] == "2026-07-16"


def test_day_reminder_skips_recent_entry_then_sends_one_button(monkeypatch):
    _repo, settings_state, fixed_now = _setup_state(monkeypatch)
    bot = FakeBot()
    monkeypatch.setattr(thoughts.settings, "notif_on", lambda _cid, _kind: True)
    settings_state[("42", "_thoughts_last_added_at")] = fixed_now.timestamp() - 60 * 60

    assert asyncio.run(thoughts.send_day_reminder(bot, "42")) is False
    assert bot.sent == []

    settings_state[("42", "_thoughts_last_added_at")] = fixed_now.timestamp() - 3 * 60 * 60
    assert asyncio.run(thoughts.send_day_reminder(bot, "42")) is True
    assert bot.sent[0]["text"].startswith("😮‍💨 Есть что выгрузить?")
    assert _button_count(bot.sent[0]["reply_markup"]) == 1


def test_evening_close_only_sends_when_open_records_exist(monkeypatch):
    _repo, _settings, _fixed_now = _setup_state(monkeypatch)
    bot = FakeBot()
    monkeypatch.setattr(thoughts.settings, "notif_on", lambda _cid, _kind: True)
    monkeypatch.setattr(thoughts, "open_records", lambda _cid: [])
    assert asyncio.run(thoughts.send_evening_close(bot, "42")) is False

    monkeypatch.setattr(thoughts, "open_records", lambda _cid: [{"id": "x"}])
    assert asyncio.run(thoughts.send_evening_close(bot, "42")) is True
    assert bot.sent[0]["text"] == (
        "😌 Закроем день\n\n"
        "Осталось записей: 1\n"
        "Разберём или оставим до завтра?"
    )
    assert _button_count(bot.sent[0]["reply_markup"]) == 2


def test_legacy_inbox_opens_new_home_without_clear_all(monkeypatch):
    repo, _settings, fixed_now = _setup_state(monkeypatch)
    repo.items = [{
        "id": "one",
        "text": "Закончить отчёт",
        "date": fixed_now.strftime("%Y-%m-%d"),
        "status": "open",
    }]
    bot = FakeBot()

    asyncio.run(thoughts.send_inbox(bot, "42"))

    labels = [
        button.text
        for row in bot.sent[0]["reply_markup"].inline_keyboard
        for button in row
    ]
    assert labels == ["✨ Разобрать мысли", "⬅️ Назад", "#️⃣ Меню"]
    assert "статист" not in bot.sent[0]["text"].casefold()
    assert "очист" not in " ".join(labels).casefold()


def test_knowledge_base_contains_only_allowed_source_families():
    allowed = ("NICE", "NHS", "Mastering", "The Adult ADHD Tool Kit", "Russell Barkley", "113")
    assert thoughts_knowledge.GUIDANCE
    assert all(item["source"].startswith(allowed) for item in thoughts_knowledge.GUIDANCE)


def test_zeroentropy_reranks_the_closed_knowledge_base(monkeypatch):
    calls = []
    monkeypatch.setattr(thoughts_knowledge.config, "ZEROENTROPY_API_KEY", "test-key")

    def fake_rerank(query, documents, top_n):
        calls.append((query, list(documents), top_n))
        return [(documents[-1], 0.9)]

    monkeypatch.setattr(thoughts_knowledge.rerank, "rerank", fake_rerank)
    result = thoughts_knowledge.retrieve("Не успеваю закончить работу", "practical_problem")

    assert result == [calls[0][1][-1]]
    assert all("NICE" in doc or "ADHD" in doc or "NHS" in doc or "Barkley" in doc for doc in calls[0][1])


def test_thought_notifications_are_on_by_default_and_evening_is_at_20(monkeypatch):
    monkeypatch.setattr(settings, "get", lambda _cid, _key, default=None: default)

    assert settings.notif_on("42", "checkin_day") is True
    assert settings.notif_on("42", "checkin_eve") is True
    assert "20:00" in settings._notif_label("checkin_eve", "Закрыть день")


def test_legacy_clear_all_button_no_longer_deletes_records(monkeypatch):
    repo, _settings, fixed_now = _setup_state(monkeypatch)
    repo.items = [{
        "id": "one",
        "text": "Закончить отчёт",
        "date": fixed_now.strftime("%Y-%m-%d"),
        "status": "open",
    }]
    bot = FakeBot()

    import balance
    monkeypatch.setattr(balance.thoughts, "_repo", lambda _cid: repo)
    monkeypatch.setattr(balance.thoughts, "_now", lambda: fixed_now)
    asyncio.run(balance.worry_clear_all(bot, "42"))

    assert len(repo.items) == 1
    assert bot.sent[0]["text"].startswith("😮‍💨 Мысли")


def test_batch_review_is_short_deduplicated_and_has_only_allowed_buttons(monkeypatch):
    repo, _settings, _fixed_now = _setup_state(monkeypatch)
    repo.items = [
        {"id": "1", "text": "Купить фильтры", "type": "practical_problem", "status": "open", "date": "2026-07-16"},
        {"id": "2", "text": "Кажется, ничего не успею", "type": "anxious_prediction", "status": "open", "date": "2026-07-16"},
    ]

    async def fake_model(*_args, **_kwargs):
        return {
            "summary": "Смешались задачи и тревожные предположения.",
            "actions": ["Заказать фильтры.", "Заказать фильтры.", "Выбрать главную задачу.", "Лишнее действие."],
            "reframe": "Мысль о том, что ничего не получится, пока не является фактом?",
        }

    monkeypatch.setattr(thoughts.ai, "allm_json", fake_model)
    monkeypatch.setattr(thoughts.thoughts_knowledge, "retrieve", lambda *_args, **_kwargs: [])
    bot = FakeBot()
    q = FakeQuery()

    asyncio.run(thoughts.review_all(bot, "42", q=q))

    assert q.message.text.startswith("✨ Разбор мыслей")
    assert q.message.text.count("• Заказать фильтры.") == 1
    assert "Лишнее действие" in q.message.text
    assert "?" not in q.message.text
    assert len(q.message.text.split()) <= 120
    labels = [button.text for row in q.message.reply_markup.inline_keyboard for button in row]
    assert labels == ["Оставить на потом", "Очистить мысли", "⬅️ Назад", "#️⃣ Меню"]


def test_leave_for_later_saves_review_and_hides_repeat_review_today(monkeypatch):
    repo, settings_state, _fixed_now = _setup_state(monkeypatch)
    repo.items = [{
        "id": "1", "text": "Купить фильтры", "type": "practical_problem",
        "status": "open", "date": "2026-07-16",
    }]
    settings_state[("42", "_thoughts_review_cache")] = {
        "id": "review-1", "date": "2026-07-16", "created_at": "now",
        "thought_ids": ["1"],
        "result": {"summary": "Есть одна задача.", "actions": ["Купить фильтры."], "reframe": ""},
    }
    bot = FakeBot()
    q = FakeQuery()

    asyncio.run(thoughts.handle_callback(bot, "42", q, "thought_review_later"))

    assert repo.items[0]["status"] == "later"
    assert repo.reviews.items[0]["result"]["actions"] == ["Купить фильтры."]
    assert settings_state[("42", "_thoughts_review_later_date")] == "2026-07-16"
    assert settings_state[("42", "_thoughts_evening_closed_date")] == "2026-07-16"
    assert bot.sent[0]["text"].startswith("✅ Оставлено\n\nК мыслям можно вернуться позже.")
    labels = [button.text for row in bot.sent[0]["reply_markup"].inline_keyboard for button in row]
    assert "✨ Разобрать мысли" not in labels


def test_clear_requires_confirmation_then_closes_only_current_list(monkeypatch):
    repo, settings_state, _fixed_now = _setup_state(monkeypatch)
    repo.items = [
        {"id": "current", "text": "Ответить", "type": "practical_problem", "status": "open", "date": "2026-07-16"},
        {"id": "old", "text": "Старая запись", "type": "unknown", "status": "done", "date": "2026-07-15"},
    ]
    settings_state[("42", "_thoughts_review_cache")] = {
        "id": "review-1", "date": "2026-07-16", "thought_ids": ["current"],
        "result": {"summary": "Есть задача.", "actions": ["Ответить."], "reframe": ""},
    }
    bot = FakeBot()
    q = FakeQuery()

    asyncio.run(thoughts.handle_callback(bot, "42", q, "thought_review_clear"))
    assert q.message.text == "Очистить мысли?\n\nВсе записи из текущего списка будут убраны."
    assert next(item for item in repo.items if item["id"] == "current")["status"] == "open"

    asyncio.run(thoughts.handle_callback(bot, "42", q, "thought_review_clear_yes"))

    assert next(item for item in repo.items if item["id"] == "current")["status"] == "closed"
    assert next(item for item in repo.items if item["id"] == "old")["status"] == "done"
    event = repo.reviews.items[0]
    assert event["outcome"] == "cleared"
    assert event["record_count"] == 1
    assert "result" not in event and "thought_ids" not in event
    assert bot.sent[0]["text"].startswith("✅ Мысли очищены\n\nСейчас список пуст.")
    assert "Список пуст." in bot.sent[0]["text"]


def test_full_history_delete_is_only_in_settings_and_requires_confirmation(monkeypatch):
    bot = FakeBot()
    asyncio.run(saved_items.send_notes(bot, "42"))
    labels = [button.text for row in bot.sent[0]["reply_markup"].inline_keyboard for button in row]
    assert "❌ Удалить историю мыслей" in labels

    deleted = []
    monkeypatch.setattr(settings.store, "set_list", lambda *args: deleted.append(args))
    q = FakeQuery()
    asyncio.run(settings.confirm_delete_thought_history(bot, "42", q=q))

    assert deleted == []
    assert q.message.text.startswith("Удалить историю мыслей?")
    confirm_labels = [button.text for row in q.message.reply_markup.inline_keyboard for button in row]
    assert confirm_labels == ["Да, удалить историю", "Отмена"]


def test_confirmed_full_history_delete_preserves_personalization(monkeypatch):
    deleted = []
    saved_settings = []
    source = {
        "42": {
            "city": "Алкмар",
            "notif_checkin_day": False,
            "_thoughts_review_cache": {"result": "private"},
            "_thoughts_last_added_at": 123,
        }
    }
    monkeypatch.setattr(settings.store, "set_list", lambda key, cid, items: deleted.append((key, cid, items)))
    monkeypatch.setattr(settings, "_all", lambda: {key: dict(value) for key, value in source.items()})
    monkeypatch.setattr(settings.store, "_save", lambda key, value: saved_settings.append((key, value)))
    bot = FakeBot()
    q = FakeQuery()

    asyncio.run(settings.delete_thought_history(bot, "42", q=q))

    assert deleted == [
        (config.THOUGHTS_KEY, "42", []),
        (config.THOUGHT_REVIEWS_KEY, "42", []),
    ]
    remaining = saved_settings[0][1]["42"]
    assert remaining == {"city": "Алкмар", "notif_checkin_day": False}
    assert q.message.text.startswith("✅ История мыслей удалена")
