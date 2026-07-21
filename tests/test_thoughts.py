import asyncio
from datetime import datetime
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import bot_text
import secure
import settings
import saved_items
import thoughts


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
        "Список пуст."
    )
    assert message["transient"] is True
    assert message["reply_markup"] is None


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
    assert "Сегодня записано" not in text
    labels = [button.text for row in bot.sent[0]["reply_markup"].inline_keyboard for button in row]
    assert labels == ["🧐 Разобрать мысли"]


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


def test_capture_cleans_lightly_and_keeps_one_message_as_one_thought(monkeypatch):
    repo, settings_state, _fixed_now = _setup_state(monkeypatch)
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
    original = "  • нужно подготовиться\nк экзамену, купить ручку  "

    asyncio.run(thoughts.capture(bot, "42", original, split_commas=True))

    assert len(repo.items) == 1
    assert repo.items[0]["text"] == "Нужно подготовиться к экзамену, купить ручку"
    assert repo.items[0]["type"] == "practical_problem"
    assert repo.items[0]["confidence"] == 0.87
    assert repo.items[0]["can_be_actioned"] is True
    assert bot.sent[0]["text"].startswith("😮‍💨 Мысли")
    assert "• Нужно подготовиться к экзамену, купить ручку" in bot.sent[0]["text"]
    assert bot.sent[0]["transient"] is True
    assert settings_state[("42", "_thoughts_capture_state")] == {"status": "idle"}
    assert thoughts.capture_waiting("42") is False


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


def test_day_reminder_skips_recent_entry_then_opens_optional_capture(monkeypatch):
    _repo, settings_state, fixed_now = _setup_state(monkeypatch)
    bot = FakeBot()
    monkeypatch.setattr(thoughts.settings, "notif_on", lambda _cid, _kind: True)
    settings_state[("42", "_thoughts_last_added_at")] = fixed_now.timestamp() - 60 * 60

    assert asyncio.run(thoughts.send_day_reminder(bot, "42")) is False
    assert bot.sent == []

    settings_state[("42", "_thoughts_last_added_at")] = fixed_now.timestamp() - 3 * 60 * 60
    assert asyncio.run(thoughts.send_day_reminder(bot, "42")) is True
    assert bot.sent[0]["text"].startswith("😮‍💨 Есть что выгрузить?")
    labels = [
        button.text
        for row in bot.sent[0]["reply_markup"].inline_keyboard
        for button in row
    ]
    assert labels == ["✍️ Выгрузить тревоги", "😌 Всё спокойно"]
    assert thoughts.capture_waiting("42") is True
    assert settings_state[("42", "_thoughts_capture_state")]["status"] == "implicit_wait"


def test_direct_text_after_reminder_routes_to_thought_capture(monkeypatch):
    cid = "thought-reminder-direct-text"
    captured = []

    async def no_match(*_args, **_kwargs):
        return False

    async def capture(_bot, routed_cid, text):
        captured.append((routed_cid, text))

    async def fail_chat(*_args, **_kwargs):
        raise AssertionError("free chat must not handle an active thought capture")

    monkeypatch.setattr(bot_text.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_text.tracking, "touch", lambda _cid: None)
    monkeypatch.setattr(bot_text.dictionary_import, "try_add_dict_from_chat", no_match)
    monkeypatch.setattr(bot_text.fridge, "try_add_fridge_from_chat", no_match)
    monkeypatch.setattr(bot_text.assistant, "try_add_love_from_chat", no_match)
    monkeypatch.setattr(bot_text.assistant, "chat_reply", fail_chat)
    monkeypatch.setattr(bot_text.balance.thoughts, "capture_waiting", lambda _cid: True)
    monkeypatch.setattr(bot_text.balance.thoughts, "capture", capture)
    bot_text.store.pending_input[cid] = "thought_reminder"
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=cid),
        message=SimpleNamespace(text="Завтра встреча с психологом\nНужно переложить ламинат"),
    )

    asyncio.run(bot_text.handle(
        update,
        SimpleNamespace(bot=SimpleNamespace()),
        lambda _bot, _cid: asyncio.sleep(0),
    ))

    assert captured == [(cid, "Завтра встреча с психологом\nНужно переложить ламинат")]


def test_specialized_pending_has_priority_over_thought_capture(monkeypatch):
    cid = "trainer-over-thoughts"
    routed = []
    cancelled = []

    async def no_match(*_args, **_kwargs):
        return False

    async def trainer_answer(_bot, routed_cid, text):
        routed.append((routed_cid, text))
        return True

    monkeypatch.setattr(bot_text.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_text.tracking, "touch", lambda _cid: None)
    monkeypatch.setattr(bot_text.dictionary_import, "try_add_dict_from_chat", no_match)
    monkeypatch.setattr(bot_text.balance.thoughts, "capture_waiting", lambda _cid: True)
    monkeypatch.setattr(
        bot_text.balance.thoughts,
        "cancel_capture",
        lambda routed_cid, **kwargs: cancelled.append((routed_cid, kwargs)),
    )
    monkeypatch.setattr(bot_text.trainer, "handle_text", trainer_answer)
    bot_text.store.pending_input[cid] = bot_text.trainer_session.PENDING_ANSWER
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=cid),
        message=SimpleNamespace(text="ging"),
    )

    asyncio.run(bot_text.handle(
        update,
        SimpleNamespace(bot=SimpleNamespace()),
        lambda _bot, _cid: asyncio.sleep(0),
    ))

    assert routed == [(cid, "ging")]
    assert cancelled == [(cid, {"clear_pending": False})]


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
    assert labels == ["🧐 Разобрать мысли"]
    assert "статист" not in bot.sent[0]["text"].casefold()
    assert "очист" not in " ".join(labels).casefold()


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


def test_batch_review_is_short_and_has_only_allowed_buttons(monkeypatch):
    repo, _settings, _fixed_now = _setup_state(monkeypatch)
    repo.items = [
        {"id": "1", "text": "Купить фильтры", "type": "practical_problem", "status": "open", "date": "2026-07-16"},
        {"id": "2", "text": "Кажется, ничего не успею", "type": "anxious_prediction", "status": "open", "date": "2026-07-16"},
    ]

    async def fake_model(*_args, **_kwargs):
        return {
            "summary": "Смешались задачи и тревожные предположения.",
            "analysis": [
                "Фильтры требуют действия.",
                "Ощущение срочности не содержит конкретного срока.",
            ],
            "next_step": "Закажи фильтры для фонтана.",
        }

    monkeypatch.setattr(thoughts.ai, "allm_json", fake_model)
    bot = FakeBot()
    q = FakeQuery()

    asyncio.run(thoughts.review_all(bot, "42", q=q))

    assert q.message.text.startswith("🧐 Разбор мыслей")
    assert q.message.text.count("• Фильтры требуют действия.") == 1
    assert "Закажи фильтры для фонтана." in q.message.text
    assert "?" not in q.message.text
    labels = [button.text for row in q.message.reply_markup.inline_keyboard for button in row]
    assert labels == ["🕒 Оставить на потом", "❌ Удалить мысли"]
    assert [len(row) for row in q.message.reply_markup.inline_keyboard] == [1, 1]


def test_review_prompt_contains_full_list_local_time_and_required_json(monkeypatch):
    _setup_state(monkeypatch)
    prompts = []

    async def fake_model(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return {
            "summary": "Смешались задача и тревога.",
            "analysis": ["Фильтры требуют действия.", "У тревоги не указан срок."],
            "next_step": "Купи фильтры для фонтана.",
        }

    monkeypatch.setattr(thoughts.ai, "allm_json", fake_model)
    result = asyncio.run(thoughts._build_review([
        {"text": "Нужно купить фильтры для фонтана", "type": "practical_problem"},
        {"text": "Уже 12 часов, и я тревожусь, что ничего не успею", "type": "anxious_prediction"},
    ]))

    prompt = prompts[0]
    assert "Нужно купить фильтры для фонтана" in prompt
    assert "Уже 12 часов, и я тревожусь, что ничего не успею" in prompt
    assert "Текущая локальная дата: 2026-07-16" in prompt
    assert "Локальное время: 14:00" in prompt
    assert "Количество мыслей: 2" in prompt
    assert '"summary"' in prompt and '"analysis"' in prompt and '"next_step"' in prompt
    assert result["next_step"] == "Купи фильтры для фонтана."


def test_invalid_or_generic_model_response_uses_content_specific_fallback(monkeypatch):
    _setup_state(monkeypatch)

    async def generic_model(*_args, **_kwargs):
        return {
            "summary": "Есть несколько мыслей.",
            "analysis": ["Записать мысль и сменить обстановку."],
            "next_step": "Вернуться к текущему делу.",
        }

    monkeypatch.setattr(thoughts.ai, "allm_json", generic_model)
    result = asyncio.run(thoughts._build_review([
        {"text": "Нужно купить фильтры для фонтана", "type": "practical_problem"},
        {"text": "Кажется, ничего не успею", "type": "anxious_prediction"},
        {"text": "Уже договорились поехать на пляж", "type": "unknown"},
    ]))

    combined = " ".join([result["summary"], *result["analysis"], result["next_step"]])
    assert "фильтр" in combined.casefold()
    assert "ничего не успею" in combined.casefold()
    assert "пляж" in combined.casefold()
    assert "записать мысль" not in combined.casefold()
    assert "сменить обстановку" not in combined.casefold()
    assert result["next_step"] == "Купи фильтры для фонтана."


def test_formally_valid_but_content_free_next_step_uses_fallback(monkeypatch):
    _setup_state(monkeypatch)

    async def generic_model(*_args, **_kwargs):
        return {
            "summary": "Смешались дела и тревога.",
            "analysis": ["Есть одна задача."],
            "next_step": "Выбери одно важное дело.",
        }

    monkeypatch.setattr(thoughts.ai, "allm_json", generic_model)
    result = asyncio.run(thoughts._build_review([
        {"text": "Нужно купить фильтры для фонтана", "type": "practical_problem"},
    ]))

    assert result["next_step"] == "Купи фильтры для фонтана."


def test_leave_for_later_saves_review_and_returns_to_thoughts(monkeypatch):
    repo, settings_state, _fixed_now = _setup_state(monkeypatch)
    repo.items = [{
        "id": "1", "text": "Купить фильтры", "type": "practical_problem",
        "status": "open", "date": "2026-07-16",
    }]
    settings_state[("42", "_thoughts_review_cache")] = {
        "id": "review-1", "date": "2026-07-16", "created_at": "now",
        "thought_ids": ["1"],
        "result": {
            "summary": "Есть одна задача.",
            "analysis": ["Фильтры требуют действия."],
            "next_step": "Купи фильтры.",
        },
    }
    bot = FakeBot()
    q = FakeQuery()

    asyncio.run(thoughts.handle_callback(bot, "42", q, "thought_review_later"))

    assert repo.items[0]["status"] == "later"
    assert repo.reviews.items[0]["result"]["next_step"] == "Купи фильтры."
    assert settings_state[("42", "_thoughts_evening_closed_date")] == "2026-07-16"
    assert settings_state[("42", "_thoughts_review_cache")] == {}
    assert q.message.deleted is True
    assert bot.sent[0]["text"].startswith("😮‍💨 Мысли")
    assert "• Купить фильтры" in bot.sent[0]["text"]
    labels = [button.text for row in bot.sent[0]["reply_markup"].inline_keyboard for button in row]
    assert labels == ["🧐 Разобрать мысли"]


def test_clear_requires_confirmation_then_closes_only_current_list(monkeypatch):
    repo, settings_state, _fixed_now = _setup_state(monkeypatch)
    repo.items = [
        {"id": "current", "text": "Ответить", "type": "practical_problem", "status": "open", "date": "2026-07-16"},
        {"id": "after-review", "text": "Добавлено позже", "type": "unknown", "status": "open", "date": "2026-07-16"},
        {"id": "old", "text": "Старая запись", "type": "unknown", "status": "done", "date": "2026-07-15"},
    ]
    settings_state[("42", "_thoughts_review_cache")] = {
        "id": "review-1", "date": "2026-07-16", "thought_ids": ["current"],
        "result": {
            "summary": "Есть задача.",
            "analysis": ["Ответ требует действия."],
            "next_step": "Ответь на сообщение.",
        },
    }
    bot = FakeBot()
    q = FakeQuery()

    asyncio.run(thoughts.handle_callback(bot, "42", q, "thought_review_clear"))
    assert q.message.text == "Очистить разобранные мысли?"
    confirmation_labels = [
        button.text for row in q.message.reply_markup.inline_keyboard for button in row
    ]
    assert confirmation_labels == ["❌ Да, очистить", "↩️ Отмена"]
    assert [len(row) for row in q.message.reply_markup.inline_keyboard] == [1, 1]
    assert next(item for item in repo.items if item["id"] == "current")["status"] == "open"

    asyncio.run(thoughts.handle_callback(bot, "42", q, "thought_review_clear_yes"))

    assert not any(item["id"] == "current" for item in repo.items)
    assert next(item for item in repo.items if item["id"] == "after-review")["status"] == "open"
    assert next(item for item in repo.items if item["id"] == "old")["status"] == "done"
    event = repo.reviews.items[0]
    assert event["outcome"] == "cleared"
    assert event["record_count"] == 1
    assert "result" not in event and "thought_ids" not in event
    assert bot.sent[0]["text"] == (
        "😮‍💨 Мысли\n\n"
        "Голова немного свободнее.\n"
        "Можешь записать новую мысль, задачу или тревогу."
    )
    assert bot.sent[0]["reply_markup"] is None


def test_stale_clear_callback_never_deletes_without_cached_review(monkeypatch):
    repo, _settings, _fixed_now = _setup_state(monkeypatch)
    repo.items = [{
        "id": "new", "text": "Новая мысль", "type": "unknown",
        "status": "open", "date": "2026-07-16",
    }]
    bot = FakeBot()
    q = FakeQuery()

    asyncio.run(thoughts.handle_callback(bot, "42", q, "thought_review_clear_yes"))

    assert [item["id"] for item in repo.items] == ["new"]
    assert bot.sent[0]["text"].startswith("😮‍💨 Мысли")
    assert "• Новая мысль" in bot.sent[0]["text"]


def test_full_history_delete_is_absent_from_settings_and_legacy_callbacks_are_safe(monkeypatch):
    bot = FakeBot()
    asyncio.run(saved_items.send_notes(bot, "42"))
    labels = [button.text for row in bot.sent[0]["reply_markup"].inline_keyboard for button in row]
    assert "❌ Удалить историю мыслей" not in labels

    deleted = []
    monkeypatch.setattr(settings.store, "set_list", lambda *args: deleted.append(args))
    q = FakeQuery()
    asyncio.run(settings.handle_callback(bot, "42", "set_thought_history_delete", q=q))
    asyncio.run(settings.handle_callback(bot, "42", "set_thought_history_delete_yes", q=q))

    assert deleted == []
    assert bot.sent[-1]["text"].startswith("🎚️ Настройки")
