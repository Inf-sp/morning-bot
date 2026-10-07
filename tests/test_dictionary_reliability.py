import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import ai
import config
import dictionary_import
import tracking

PLACEHOLDER = {
    "id": "old-id", "lang": "nl", "term": "Wazig", "translation": "", "breakdown": "слово",
    "examples": [], "analysis_pending": True, "added_at": "2026-10-01T10:00:00+02:00",
}
FULL = {
    "lang": "nl", "term": "Wazig", "translation": "Расплывчатый; туманный",
    "breakdown": "прилагательное", "pos": "прилагательное",
    "examples": [{"text": "Het beeld is wazig.", "translation": "Изображение размыто."}],
    "added_at": "2026-10-07T10:00:00+02:00", "status": "new",
}


def test_full_card_replaces_placeholder_keeping_its_id():
    cid = "placeholder-replace"
    dictionary_import.store.set_list(config.DICT_KEY, cid, [dict(PLACEHOLDER)])

    status, saved = dictionary_import._save_normalized_dict_entry(cid, dict(FULL))

    words = dictionary_import.store.get_list(config.DICT_KEY, cid)
    assert status == "added" and len(words) == 1
    assert saved["id"] == "old-id"
    assert saved["pos"] == "прилагательное" and saved["breakdown"] == "прилагательное"
    assert "analysis_pending" not in saved


def test_old_placeholders_are_requeued_and_shown_as_preparing():
    cid = "placeholder-requeue"
    dictionary_import.store.set_profile(cid, {})
    dictionary_import.store.set_list(config.DICT_KEY, cid, [dict(PLACEHOLDER)])

    dictionary_import._requeue_card_placeholders(cid)
    dictionary_import._requeue_card_placeholders(cid)

    queue = dictionary_import.store.get_profile(cid)["dictionary_pending_analysis"]
    assert [(item["term"], item["lang"]) for item in queue] == [("Wazig", "nl")]
    text = dictionary_import._dict_entry_message(dict(PLACEHOLDER), status="found").text
    assert text.startswith("⏳ Карточка «Wazig» готовится")
    assert "Разбор" not in text and "уточняется" not in text


def test_provider_timeout_lifts_short_interactive_cap(monkeypatch):
    seen = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append(timeout)
        raise ai.requests.exceptions.ConnectionError("offline")

    monkeypatch.setattr(ai.requests, "post", fake_post)
    for timeout_context in (None, 20):
        try:
            if timeout_context:
                with ai.provider_timeout(timeout_context):
                    ai._post("https://x", {}, {}, 30, "gemini", timeout_cap=5)
            else:
                ai._post("https://x", {}, {}, 30, "gemini", timeout_cap=5)
        except Exception:
            pass
    assert seen == [5.0, 20.0]


def test_long_dictionary_task_extends_action_budget():
    trace = tracking.start_action("budget-cid", "Ассистент", "text", budget_seconds=10)
    try:
        tracking.extend_action_budget(42, trace)
        assert tracking.remaining_action_seconds(trace) > 40
    finally:
        tracking.finish_action(trace)


def test_lowercases_only_capitalized_dutch_words():
    lower = dictionary_import._lower_dutch_initial
    assert lower("Wazig", "nl") == "wazig"
    assert lower("NATO", "nl") == "NATO"
    assert lower("Monday", "en") == "Monday"
