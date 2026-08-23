import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import learning_dictionary


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
