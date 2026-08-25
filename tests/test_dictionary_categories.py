import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import learning_dictionary
import dictionary_import


def test_dictionary_migration_repairs_pos_from_grammar_breakdown(monkeypatch):
    words = [{
        "id": "word-1",
        "lang": "nl",
        "term": "Geschikt",
        "translation": "Подходящий",
        "pos": "noun",
        "breakdown": "прилагательное",
        "examples": [],
    }]
    saved = []
    monkeypatch.setattr(
        learning_dictionary.store, "get_list", lambda _key, _cid: [dict(words[0])],
    )
    monkeypatch.setattr(
        learning_dictionary.store, "set_list", lambda _key, _cid, value: saved.append(value),
    )

    result = learning_dictionary.normalize_user_dictionary("42")

    assert result[0]["pos"] == "прилагательное"
    assert learning_dictionary._dictionary_category(result[0]) == "Прилагательные"
    assert saved[0][0]["pos"] == "прилагательное"


def test_dictionary_migration_extracts_article_and_repairs_legacy_noun(monkeypatch):
    words = [{
        "id": "word-1", "lang": "nl", "term": "het huis",
        "translation": "дом", "pos": "глагол", "breakdown": "глагол",
        "examples": [],
    }]
    saved = []
    monkeypatch.setattr(learning_dictionary.store, "get_list", lambda *_args: words)
    monkeypatch.setattr(
        learning_dictionary.store, "set_list",
        lambda _key, _cid, value: saved.append(value),
    )

    result = learning_dictionary.normalize_user_dictionary("42")

    assert result[0]["term"] == "Huis"
    assert result[0]["article"] == "het"
    assert result[0]["pos"] == "существительное"
    assert result[0]["breakdown"] == "существительное · het-слово"
    assert learning_dictionary._dictionary_category(result[0]) == "Существительные"
    assert saved


def test_dictionary_format_migration_reclassifies_all_legacy_cards(monkeypatch):
    words = [
        {
            "id": "noun", "lang": "nl", "term": "Huis", "translation": "Дом",
            "pos": "глагол", "breakdown": "глагол", "srs_level": 4,
            "srs_due_at": "2026-08-30T10:00:00+02:00",
        },
        {
            "id": "adjective", "lang": "nl", "term": "Mooi",
            "translation": "Красивый", "pos": "noun", "breakdown": "существительное",
        },
    ]
    saved = []

    async def analyze(*_args, **_kwargs):
        return {"items": [
            {
                "pos": "существительное", "article": "het",
                "breakdown": "существительное · het-слово", "plural": "huizen",
            },
            {
                "pos": "прилагательное", "article": "",
                "breakdown": "прилагательное", "plural": "",
            },
        ]}

    monkeypatch.setattr(learning_dictionary.store, "get_list", lambda *_args: words)
    monkeypatch.setattr(
        learning_dictionary.store, "set_list",
        lambda _key, _cid, value: saved.append([dict(item) for item in value]),
    )
    monkeypatch.setattr(learning_dictionary.ai, "allm_json", analyze)

    asyncio.run(learning_dictionary.migrate_dict_entries_for_srs("42", "nl"))

    assert words[0]["article"] == "het"
    assert words[0]["pos"] == "существительное"
    assert words[0]["breakdown"] == "существительное · het-слово"
    assert words[0]["srs_level"] == 4
    assert words[1]["pos"] == "прилагательное"
    assert words[1]["article"] == ""
    assert all(
        item["dictionary_format_version"] == learning_dictionary._DICTIONARY_FORMAT_VERSION
        for item in words
    )
    assert saved


def test_dictionary_migration_rechecks_current_but_misclassified_vaststellen(monkeypatch):
    words = [{
        "id": "vaststellen", "lang": "nl", "term": "Vaststellen",
        "translation": "Устанавливать", "pos": "существительное",
        "breakdown": "выражение",
        "dictionary_format_version": learning_dictionary._DICTIONARY_FORMAT_VERSION - 1,
        "srs_level": 4, "srs_due_at": "2026-09-01T10:00:00+02:00",
    }]

    async def analyze(*_args, **_kwargs):
        return {"items": [{
            "pos": "глагол", "article": "", "breakdown": "глагол",
            "plural": "", "infinitive": "vaststellen",
        }]}

    monkeypatch.setattr(learning_dictionary.store, "get_list", lambda *_args: words)
    monkeypatch.setattr(learning_dictionary.store, "set_list", lambda *_args: None)
    monkeypatch.setattr(learning_dictionary.ai, "allm_json", analyze)

    asyncio.run(learning_dictionary.migrate_dict_entries_for_srs("42", "nl"))

    assert words[0]["pos"] == "глагол"
    assert words[0]["breakdown"] == "глагол"
    assert learning_dictionary._dictionary_category(words[0]) == "Глаголы"
    assert words[0]["srs_level"] == 4


def test_duplicate_word_replaces_wrong_category_with_fresh_analysis(monkeypatch):
    words = [{
        "id": "vaststellen", "lang": "nl", "term": "Vaststellen",
        "translation": "Устанавливать", "pos": "существительное",
        "breakdown": "выражение", "srs_level": 4,
        "srs_due_at": "2026-09-01T10:00:00+02:00",
    }]
    saved = []
    monkeypatch.setattr(dictionary_import.store, "ensure_list_ids", lambda *_args: words)
    monkeypatch.setattr(dictionary_import.store, "set_list", lambda _key, _cid, value: saved.append(value))

    status, entry = dictionary_import._save_normalized_dict_entry("42", {
        "lang": "nl", "term": "Vaststellen", "translation": "Устанавливать",
        "pos": "глагол", "article": "", "breakdown": "глагол",
        "examples": [{"text": "We stellen de oorzaak vast.", "translation": "Мы устанавливаем причину."}],
        "analysis_provider": "checked", "added_at": "2026-08-25T10:00:00+02:00",
    })

    assert status == "duplicate"
    assert entry["pos"] == "глагол"
    assert entry["breakdown"] == "глагол"
    assert learning_dictionary._dictionary_category(entry) == "Глаголы"
    assert entry["srs_level"] == 4
    assert saved
