import pytest

import learning


@pytest.mark.unit
def test_should_train_new_word_is_30_percent_per_ten_rounds():
    rounds = [i for i in range(10) if learning._should_train_new_word(i)]

    assert rounds == [2, 5, 8]
    assert sum(learning._should_train_new_word(i) for i in range(100)) == 30


@pytest.mark.unit
def test_train_phrases_reads_only_phrase_entries(monkeypatch):
    monkeypatch.setattr(learning, "_ensure_dict", lambda cid: [
        {"lang": "nl", "kind": "phrase", "word": "Ik ben onderweg", "ru": "Я в пути"},
        {"lang": "nl", "kind": "word", "word": "Onderweg", "ru": "в пути"},
        {"lang": "en", "kind": "phrase", "word": "I am on my way", "ru": "Я в пути"},
    ])

    assert learning._train_phrases("cid", "нидерландский") == [("Ik ben onderweg", "Я в пути")]


@pytest.mark.unit
def test_game_recent_matches_aliases_and_translations():
    data = {"answer": "Sherlock Holmes", "aliases": ["Шерлок Холмс", "Sherlock Holmes"]}

    assert learning._game_is_recent(data, ["шерлок холмс"])
    assert learning._game_is_recent(data, ["Sherlock"])
    assert not learning._game_is_recent(data, ["Hercule Poirot"])


@pytest.mark.unit
def test_remember_game_answer_dedupes_aliases(monkeypatch):
    import store

    store.game_recent["game-test"] = ["Шерлок Холмс"]
    learning._remember_game_answer("game-test", {
        "answer": "Sherlock Holmes",
        "aliases": ["Шерлок Холмс", "Sherlock Holmes", "Sherlock"],
    })

    assert store.game_recent["game-test"].count("Шерлок Холмс") == 1
    assert store.game_recent["game-test"].count("Sherlock Holmes") == 1
