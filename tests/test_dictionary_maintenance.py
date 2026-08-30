import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot
import learning_dictionary


def test_dictionary_maintenance_is_local_and_does_not_spend_ai(monkeypatch):
    normalized = []

    monkeypatch.setattr(bot.tracking, "has_active_actions", lambda: False)
    monkeypatch.setattr(bot.access, "get_allowed_cids", lambda: ["42"])
    monkeypatch.setattr(
        bot.dictionary, "normalize_user_dictionary",
        lambda cid: normalized.append(cid),
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
    assert "Пересборка продолжится автоматически" in sent[-1]["text"]
    assert "Не получилось" not in sent[-1]["text"]
