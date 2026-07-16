import asyncio
from datetime import datetime
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import secure
import settings
import thoughts
import thoughts_knowledge
from ui import thoughts as thoughts_ui


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


def _button_count(markup):
    return sum(len(row) for row in markup.inline_keyboard)


def _setup_state(monkeypatch):
    repo = FakeRepo()
    settings_state = {}
    fixed_now = datetime(2026, 7, 16, 14, 0, tzinfo=config.TZ)
    monkeypatch.setattr(thoughts, "_repo", lambda _cid: repo)
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


def test_home_is_compact_and_has_at_most_three_buttons(monkeypatch):
    _setup_state(monkeypatch)
    bot = FakeBot()

    asyncio.run(thoughts.send_home(bot, "42"))

    message = bot.sent[0]
    assert message["text"] == (
        "😮‍💨 Мысли\n\n"
        "Не держи всё в голове.\n"
        "Напиши мысль, задачу или тревогу одним сообщением.\n\n"
        "Сегодня записано: 0\n"
        "Осталось разобрать: 0"
    )
    assert message["transient"] is True
    assert _button_count(message["reply_markup"]) == 3


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
    shown = []

    async def classify(_text):
        return {
            "type": "practical_problem",
            "confidence": 0.87,
            "urgency": "medium",
            "can_be_actioned": True,
            "can_be_reviewed_later": True,
            "requires_safety_response": False,
        }

    async def show(_bot, _cid, record, **_kwargs):
        shown.append(record)

    monkeypatch.setattr(thoughts, "classify", classify)
    monkeypatch.setattr(thoughts, "_send_scenario", show)
    original = "  Нужно подготовиться к экзамену.  "

    asyncio.run(thoughts.capture(bot, "42", original))

    assert repo.items[0]["text"] == original
    assert repo.items[0]["type"] == "practical_problem"
    assert repo.items[0]["confidence"] == 0.87
    assert repo.items[0]["can_be_actioned"] is True
    assert bot.sent[0]["text"].startswith("✅ Сохранено")
    assert bot.sent[0]["transient"] is True
    assert shown[0]["id"] == repo.items[0]["id"]


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


def test_inbox_has_no_statistics_filters_or_clear_all(monkeypatch):
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
    assert labels == ["Разобрать мысли", "⬅️ Назад"]
    assert "статист" not in bot.sent[0]["text"].casefold()
    assert "очист" not in " ".join(labels).casefold()


def test_all_ordinary_scenario_keyboards_have_at_most_three_buttons():
    for kind in ("practical_problem", "anxious_prediction", "emotion", "unknown"):
        markup = thoughts._scenario_keyboard({"id": "abc", "type": kind})
        assert _button_count(markup) <= 3


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


def test_standard_ui_response_stays_under_word_limit():
    data = thoughts._fallback_scenario("practical_problem")
    message = thoughts_ui.scenario(**data)
    assert len(message.text.split()) <= 45


def test_model_line_breaks_cannot_create_extra_headings(monkeypatch):
    async def fake_model(*_args, **_kwargs):
        return {
            "title": "🎯 Начнём\nЕщё один заголовок",
            "body": "Одна строка\nи продолжение",
            "action": "Открой документ.",
            "question": "Что закончить первым?",
        }

    monkeypatch.setattr(thoughts.ai, "allm_json", fake_model)
    monkeypatch.setattr(
        thoughts.thoughts_knowledge,
        "retrieve",
        lambda *_args, **_kwargs: ["Короткий проверенный совет"],
    )
    record = {"text": "Нужно закончить отчёт", "type": "practical_problem"}

    result = asyncio.run(thoughts._build_scenario(record))

    assert "\n" not in result["title"]
    assert "\n" not in result["body"]


def test_scenario_has_at_most_two_body_paragraphs():
    message = thoughts_ui.scenario(
        "🎯 Начнём с малого",
        "Выбери один небольшой результат.",
        "Открой нужный документ.",
        "Что важнее закончить сегодня?",
    )

    assert message.text.split("\n\n") == [
        "🎯 Начнём с малого",
        "Выбери один небольшой результат.",
        "Открой нужный документ.\nЧто важнее закончить сегодня?",
    ]


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
    assert bot.sent[0]["text"].startswith("😮‍💨 Что в голове")
