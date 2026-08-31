import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot
import learning_dictionary


def test_dictionary_maintenance_queues_legacy_cards_without_spending_ai(monkeypatch):
    normalized = []
    queued = []

    monkeypatch.setattr(bot.tracking, "has_active_actions", lambda: False)
    monkeypatch.setattr(bot.access, "get_allowed_cids", lambda: ["42"])
    monkeypatch.setattr(
        bot.dictionary, "normalize_user_dictionary",
        lambda cid: normalized.append(cid),
    )
    monkeypatch.setattr(
        bot.dictionary, "queue_dictionary_rebuild",
        lambda cid: queued.append(cid), raising=False,
    )
    monkeypatch.setattr(
        bot.dictionary, "rebuild_dictionary_entries",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("AI rebuild started")),
    )
    monkeypatch.setattr(
        bot.dictionary, "migrate_dict_entries_for_srs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("AI migration started")),
    )

    asyncio.run(bot.job_dictionary_maintenance(SimpleNamespace()))

    assert normalized == ["42"]
    assert queued == ["42"]


def test_version_two_card_is_rebuilt_into_the_current_uniform_style(monkeypatch):
    words = [{
        "id": "legacy-v2", "lang": "nl", "term": "Benadering",
        "translation": "Подход", "pos": "существительное",
        "breakdown": "существительное", "dictionary_rebuild_version": 2,
    }]
    calls = []

    async def analyze(*_args, **kwargs):
        calls.append("ai")
        assert kwargs["order"] == (
            "gemini", learning_dictionary.ai.GROQ_STANDARD,
            learning_dictionary.ai.GROQ_SIMPLE, "cf", "mistral", "openrouter",
        )
        assert kwargs["privacy_level"] == "public"
        assert kwargs["fallback_allowed"] is True
        response = {"items": [{
            "keep": True, "lang": "nl", "term": "Benadering",
            "translation": "Подход", "article": "de",
            "pos": "существительное",
            "breakdown": "существительное · de-слово",
            "plural": "benaderingen", "forms": [],
            "pronunciation": "[бена́деринг]",
            "essence": "Так называют способ приблизиться к задаче или решить её.",
            "insight": "Часто речь идёт именно о способе решения.",
            "examples": [
                {"text": "Deze benadering werkt goed.",
                 "translation": "Этот подход хорошо работает.",
                 "context": "О способе решения"},
                {"text": "We kiezen een andere benadering.",
                 "translation": "Мы выбираем другой подход.",
                 "context": "О новом плане"},
            ],
            "exercise_ru": "Мы выбираем другой подход.",
            "exercise_answer": "We kiezen een andere benadering.",
            "topic": "решения", "difficulty": "B1",
            "construction": "", "situation_type": "",
            "alt_translations": [],
        }]}
        assert kwargs["result_validator"](response)
        assert not kwargs["result_validator"]({"items": [{"term": "Benadering"}]})
        return response

    monkeypatch.setattr(learning_dictionary.store, "get_list", lambda *_args: words)
    monkeypatch.setattr(learning_dictionary.store, "set_list", lambda *_args: None)
    monkeypatch.setattr(learning_dictionary.ai, "allm_json", analyze)

    asyncio.run(learning_dictionary.rebuild_dictionary_entries("42"))

    assert calls == ["ai"]
    assert words[0]["dictionary_rebuild_version"] > 2
    assert words[0]["study_card_version"] == 1


def test_dictionary_migration_keeps_progress_until_every_old_card_is_rebuilt(monkeypatch):
    cid = "dictionary-migration-progress"
    words = [
        {"id": str(index), "lang": "nl" if index < 3 else "en",
         "term": f"Word{index}", "translation": f"Слово {index}",
         "pos": "существительное", "dictionary_rebuild_version": 2}
        for index in range(4)
    ]
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    def complete(entry):
        entry.update({
            "dictionary_rebuild_version": learning_dictionary._DICTIONARY_REBUILD_VERSION,
            "study_card_version": 1,
            "pronunciation": "[сло́во]",
            "essence": "Так объясняется значение слова в обычной ситуации.",
            "insight": "У слова есть важный нюанс употребления.",
            "examples": [
                {"text": "A complete example.", "translation": "Полный пример.",
                 "context": "В разговоре"},
                {"text": "Another complete example.", "translation": "Другой пример.",
                 "context": "На практике"},
            ],
            "exercise_ru": "Это полное задание.",
            "exercise_answer": "This is a complete exercise.",
        })

    async def rebuild(_cid, *, lang=None, max_batches=None, **_kwargs):
        assert max_batches == 1
        batch = [item for item in words if item["lang"] == lang
                 and item.get("dictionary_rebuild_version") == 2][:3]
        for item in batch:
            complete(item)
        return words

    monkeypatch.setattr(learning_dictionary.store, "get_list", lambda *_args: words)
    monkeypatch.setattr(learning_dictionary, "rebuild_dictionary_entries", rebuild)
    learning_dictionary.store.set_profile(cid, {
        "dictionary_recheck_request": {"lang": "nl"},
    })

    assert learning_dictionary.queue_dictionary_rebuild(cid) == 4
    assert "dictionary_recheck_request" not in learning_dictionary.store.get_profile(cid)
    assert asyncio.run(
        learning_dictionary.process_dictionary_rebuilds(Bot(), [cid])
    ) == 1
    assert "dictionary_card_migration" in learning_dictionary.store.get_profile(cid)
    assert not sent

    assert asyncio.run(
        learning_dictionary.process_dictionary_rebuilds(Bot(), [cid])
    ) == 1
    assert "dictionary_card_migration" not in learning_dictionary.store.get_profile(cid)
    assert "Все карточки приведены к единому виду: 4" in sent[-1]["text"]


def test_dictionary_migration_clears_obsolete_request_for_an_empty_dictionary(monkeypatch):
    cid = "empty-dictionary-old-request"
    monkeypatch.setattr(learning_dictionary.store, "get_list", lambda *_args: [])
    learning_dictionary.store.set_profile(cid, {
        "dictionary_recheck_request": {"lang": "nl"},
    })

    assert learning_dictionary.queue_dictionary_rebuild(cid) == 0
    assert "dictionary_recheck_request" not in learning_dictionary.store.get_profile(cid)


def test_failed_dictionary_migration_attempt_still_respects_the_job_limit(monkeypatch):
    cids = ["migration-limit-1", "migration-limit-2"]
    words = {
        cid: [{"id": cid, "lang": "nl", "term": "Benadering",
               "translation": "Подход", "dictionary_rebuild_version": 2}]
        for cid in cids
    }
    calls = []

    async def unavailable(cid, **_kwargs):
        calls.append(cid)
        return words[cid]

    monkeypatch.setattr(
        learning_dictionary.store, "get_list",
        lambda _key, cid: words[cid],
    )
    monkeypatch.setattr(learning_dictionary, "rebuild_dictionary_entries", unavailable)
    for cid in cids:
        learning_dictionary.store.set_profile(cid, {})
        learning_dictionary.queue_dictionary_rebuild(cid)

    asyncio.run(learning_dictionary.process_dictionary_rebuilds(object(), cids, limit=1))

    assert calls == [cids[0]]


def test_failed_manual_rebuild_is_queued_without_a_dead_end_error(monkeypatch):
    cid = "rebuild-queued"
    old = {
        "id": "word-1", "lang": "nl", "term": "Benadering",
        "translation": "Подход", "breakdown": "существительное",
        "examples": [],
    }
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def unavailable(*_args, **_kwargs):
        return old

    monkeypatch.setattr(learning_dictionary, "_entry_by_id", lambda *_args: old)
    monkeypatch.setattr(learning_dictionary, "_refresh_dict_entry", unavailable)
    monkeypatch.setattr(learning_dictionary, "_dict_entry_message", lambda *_args, **_kwargs: SimpleNamespace(
        text="Benadering → Подход", entities=None,
    ))
    monkeypatch.setattr(learning_dictionary, "_dict_entry_view_kb", lambda *_args: None)
    monkeypatch.setattr(learning_dictionary, "_queue_dictionary_analysis", lambda *_args: True, raising=False)

    asyncio.run(learning_dictionary.check_dictionary_entry(Bot(), cid, "word-1"))

    assert sent
    assert "Карточка обновится автоматически" in sent[-1]["text"]
    assert "Можно продолжать листать словарь" in sent[-1]["text"]
    assert "Не получилось" not in sent[-1]["text"]
