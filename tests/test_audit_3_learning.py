import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import learning_dictionary as ld


def _incomplete_current_card(word_id="w1"):
    return {
        "id": word_id, "lang": "nl", "term": "Benadering",
        "translation": "Подход", "pos": "существительное", "article": "de",
        "dictionary_rebuild_version": ld._DICTIONARY_REBUILD_VERSION,
    }


def test_rebuild_selects_incomplete_cards_of_current_version(monkeypatch):
    words = [_incomplete_current_card()]
    prompts = []

    async def analyze(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return {"items": []}

    monkeypatch.setattr(ld.store, "get_list", lambda *_args: words)
    monkeypatch.setattr(ld.store, "set_list", lambda *_args: None)
    monkeypatch.setattr(ld.ai, "allm_json", analyze)

    assert ld._pending_dictionary_rebuilds("cid") == words
    asyncio.run(ld.rebuild_dictionary_entries("cid", max_batches=1))

    assert len(prompts) == 1


def test_migration_stops_after_max_no_progress_attempts(monkeypatch):
    cid = "audit-migration-stuck"
    words = [_incomplete_current_card()]
    calls = []

    async def no_progress(_cid, **_kwargs):
        calls.append(_cid)
        return words

    monkeypatch.setattr(ld.store, "get_list", lambda *_args: words)
    monkeypatch.setattr(ld, "rebuild_dictionary_entries", no_progress)
    ld.store.set_profile(cid, {})
    assert ld.queue_dictionary_rebuild(cid) == 1

    for _ in range(ld._DICTIONARY_MIGRATION_MAX_ATTEMPTS):
        ld.store.mutate_profile(cid, lambda p: (
            p.get("dictionary_card_migration", {}).update(retry_after_at=0) or p, None,
        ))
        asyncio.run(ld.process_dictionary_rebuilds(object(), [cid]))

    profile = ld.store.get_profile(cid)
    assert len(calls) == ld._DICTIONARY_MIGRATION_MAX_ATTEMPTS
    assert "dictionary_card_migration" not in profile
    assert profile["dictionary_card_migration_stopped"]["pending"] == 1

    # Повторная постановка в очередь без новых карточек не возобновляет AI-вызовы.
    ld.queue_dictionary_rebuild(cid)
    asyncio.run(ld.process_dictionary_rebuilds(object(), [cid]))
    assert len(calls) == ld._DICTIONARY_MIGRATION_MAX_ATTEMPTS
    assert "dictionary_card_migration" not in ld.store.get_profile(cid)

    # Новая неполная карточка снова запускает миграцию.
    words.append(_incomplete_current_card("w2"))
    assert ld.queue_dictionary_rebuild(cid) == 2
    profile = ld.store.get_profile(cid)
    assert "dictionary_card_migration" in profile
    assert "dictionary_card_migration_stopped" not in profile


def test_failed_queued_dictionary_add_does_not_block_the_rest_of_the_queue(monkeypatch):
    import dictionary_import

    cid = "audit-queued-rotation"
    terms = []

    async def unavailable(term, *_args, **_kwargs):
        terms.append(term)
        raise dictionary_import.DictionaryAnalysisUnavailable()

    dictionary_import.store.set_profile(cid, {})
    dictionary_import._queue_dictionary_analysis(cid, "первое", "nl")
    dictionary_import._queue_dictionary_analysis(cid, "второе", "nl")
    monkeypatch.setattr(dictionary_import, "_normalize_dict_entry_full", unavailable)

    for _ in range(2):
        asyncio.run(dictionary_import.process_queued_dictionary_adds(object(), [cid], limit=1))

    assert terms == ["первое", "второе"]
    queue = dictionary_import.store.get_profile(cid)["dictionary_pending_analysis"]
    assert [item["term"] for item in queue] == ["первое", "второе"]


def test_game_guess_rejects_articles_and_unrelated_short_words():
    import learning_game

    names = ["de kat", "кошка", "cat", "kat", "poes"]
    assert learning_game._game_guess_correct("kat", names)
    assert learning_game._game_guess_correct("it is a cat", names)
    assert learning_game._game_guess_correct("De Kat", names)
    assert not learning_game._game_guess_correct("dog", names)
    assert not learning_game._game_guess_correct("de", names)
    assert not learning_game._game_guess_correct("paarden", names)


def test_dutch_review_ignores_non_dict_ai_payload(monkeypatch):
    import trainer

    async def listing(*_args, **_kwargs):
        return ["unexpected"]

    monkeypatch.setattr(trainer.ai, "allm_json", listing)

    assert asyncio.run(trainer._explain_dutch_review("Ik ga", {"issues": []})) == {}


def test_seed_keyboard_clamps_stale_page():
    import dictionary_seed_ui

    state = {"items": [{"word": f"W{i}", "ru": "x"} for i in range(3)], "page": 7}
    rows = dictionary_seed_ui.render_keyboard(state).inline_keyboard

    assert any(btn.callback_data == "a_dictseed_toggle_0" for row in rows for btn in row)
