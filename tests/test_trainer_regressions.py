import asyncio
import os
from unittest.mock import patch

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import dictionary_import
import dictionary_repository
import trainer
import trainer_engine
import trainer_exercises
import trainer_grading
import trainer_session


def test_lazy_dictionary_refresh_preserves_existing_translation():
    stored = [{
        "lang": "nl",
        "term": "Wat balen",
        "translation": "Вот досада!",
        "examples": [],
    }]
    wrong_ai_entry = {
        "lang": "nl",
        "term": "Wat balen",
        "translation": "Что ты делаешь там?",
        "breakdown": "фраза",
        "examples": [{"text": "Wat balen!", "translation": "Что ты делаешь там?"}],
    }

    async def fake_normalize(*_args, **_kwargs):
        return wrong_ai_entry

    async def fake_enrich(entry, _cid):
        return entry

    with patch.object(dictionary_import, "_normalize_dict_entry_full", fake_normalize), \
         patch.object(dictionary_import, "_enrich_dutch_verb", fake_enrich), \
         patch.object(dictionary_import.store, "get_list", lambda *_args: stored), \
         patch.object(dictionary_import.store, "set_list", lambda _key, _cid, value: stored.__setitem__(slice(None), value)):
        asyncio.run(dictionary_import._refresh_dict_entry("42", stored[0]))

    assert stored[0]["translation"] == "Вот досада!"
    assert stored[0]["term"] == "Wat balen"


def test_trainer_refreshes_an_entry_with_an_incomplete_saved_example(monkeypatch):
    refreshed = {
        "lang": "nl",
        "term": "Gevolg",
        "translation": "Последствие",
        "pos": "noun",
        "examples": [{
            "text": "Dat kan ernstige gevolgen hebben.",
            "translation": "Это может иметь серьёзные последствия.",
        }],
    }

    class FakeRepository:
        def __init__(self, _cid):
            pass

        def training_entries(self, _language):
            return []

        def correction_for(self, _entry):
            return None

    async def fake_refresh(_cid, _entry):
        return refreshed

    monkeypatch.setattr(trainer, "DictionaryRepository", FakeRepository)
    monkeypatch.setattr(dictionary_import, "_refresh_dict_entry", fake_refresh)

    data = asyncio.run(trainer._build_exercise("42", {
        "entry": {
            "lang": "nl", "term": "Gevolg", "translation": "Последствие",
            "examples": [{"text": "Dat kan ernstige gevolgen hebben."}],
        },
        "exercise_type": trainer_engine.EXERCISE_CHOOSE_TRANSLATION,
    }))

    assert data["entry"]["examples"][0]["translation"] == "Это может иметь серьёзные последствия."


def test_starting_new_trainer_session_invalidates_old_polls():
    cid = "trainer-regression"
    trainer_session.finish(cid)
    trainer_session.start(cid, "nl", [])
    trainer_session.register_poll(cid, "old-poll")

    trainer_session.start(cid, "nl", [])

    assert trainer_session.take_poll_chat("old-poll") is None
    trainer_session.finish(cid)


def test_dictionary_repository_merges_duplicate_terms_without_losing_senses(monkeypatch):
    records = [{
        "lang": "nl", "term": "gaan", "translation": "идти",
    }, {
        "lang": "nl", "term": "Gaan", "translation": "ехать",
    }]

    class FakeRecords:
        def all(self):
            return records

        def save(self, value):
            records[:] = value

    monkeypatch.setattr(dictionary_repository, "UserListRepository", lambda *_args: FakeRecords())
    entries = dictionary_repository.DictionaryRepository("42").all()

    assert len(entries) == 1
    assert entries[0]["translation"] == "Идти; Ехать"


def test_recall_exercise_exposes_acceptable_answers():
    entry = {
        "term": "Wat doe je daar?",
        "translation": "Что ты делаешь там?",
        "lang": "nl",
        "forms": ["Wat doe je daar"],
    }

    data = trainer_exercises.build_exercise(
        entry, [], trainer_engine.EXERCISE_RECALL,
    )

    assert "Wat doe je daar" in data["acceptable_answers"]
    assert trainer_grading.grade_free_text(data, "Wat doe je daar").correct is True


def test_recall_quiz_does_not_offer_a_free_text_button():
    class FakeBot:
        async def send_poll(self, **kwargs):
            self.poll_kwargs = kwargs
            return type("Message", (), {"poll": None})()

    bot = FakeBot()
    asyncio.run(trainer._send_exercise(bot, "trainer-recall-button", {
        "exercise_type": trainer_engine.EXERCISE_RECALL,
        "term": "Beloven",
        "ru": "Обещать",
        "correct": "Beloven",
        "wrong": ["Gaan", "Zien"],
    }))

    labels = [button.text for row in bot.poll_kwargs["reply_markup"].inline_keyboard for button in row]
    assert "⌨️ Написать ответ" not in labels
    assert labels[-2:] == ["⬅️ Назад", "#️⃣ Главная"]


def test_exact_dutch_answer_cannot_be_downgraded_by_ai(monkeypatch):
    report = {
        "available": True,
        "text": "Ik ga naar huis.",
        "issues": [{"issue_type": "grammar", "replacements": []}],
    }

    monkeypatch.setattr(trainer.language_tool, "check_text", lambda *_args: report)

    async def reject(_prompt, *_args, **_kwargs):
        return {"acceptable": False, "explanation": "ошибка"}

    monkeypatch.setattr(trainer.ai, "allm_json", reject)
    grade, _report = asyncio.run(trainer._grade_dutch_written({
        "lang": "nl",
        "correct": "Ik ga naar huis.",
        "acceptable_answers": [],
        "hint_shown": False,
    }, "Ik ga naar huis."))

    assert grade.correct is True


def test_find_error_requires_an_explicit_verified_rule():
    entry = {
        "term": "seconde",
        "translation": "секунда",
        "lang": "nl",
        "examples": [{
            "text": "Ik wacht een seconde vandaag.",
            "translation": "Я жду секунду сегодня.",
        }],
    }

    assert trainer_exercises.build_exercise(
        entry, [], trainer_engine.EXERCISE_FIND_ERROR,
    ) is None
