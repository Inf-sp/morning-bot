import pytest
from telegram import MessageEntity

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
def test_fallback_phrase_quiz_card_builds_valid_task():
    card = learning._fallback_phrase_quiz_card("Ik ben onderweg", "Я в пути", "нидерландский")

    assert card["correct"] == "onderweg"
    assert card["blank_phrase"] == "Ik ben ____"
    assert len(card["wrong"]) == 2
    assert "____" in card["blank_phrase"]


@pytest.mark.unit
def test_phrase_poll_question_is_formatted_with_entities():
    question, entities = learning._phrase_poll_question("Ik maak me zorgen om ____", "Я переживаю за тебя")

    assert question.startswith("Фраза-тренажёр\n\nIk maak me zorgen om ____")
    assert "Перевод: Я переживаю за тебя" in question
    assert "Выбери пропущенное слово" in question
    assert "Какое слово пропущено?" not in question
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in entities)


@pytest.mark.unit
def test_phrase_poll_explanation_shows_correct_answer():
    explanation = learning._phrase_poll_explanation(
        "Ik maak me zorgen om ____",
        "jou",
        "Ik maak me zorgen om jou",
        "Я переживаю за тебя",
    )

    assert "Ответ: jou" in explanation
    assert "Ik maak me zorgen om jou" in explanation


@pytest.mark.unit
def test_chat_dict_short_form_extracts_payload():
    payload, lang = learning._extract_chat_dict_add("В словарь Je hand opsteken (Поднять руку)")

    assert payload == "Je hand opsteken (Поднять руку)"
    assert lang == "nl"


@pytest.mark.unit
def test_chat_dict_short_form_does_not_capture_plain_dictionary_request():
    payload, lang = learning._extract_chat_dict_add("словарь нидерландский")

    assert payload is None
    assert lang is None


@pytest.mark.unit
def test_split_term_reads_parenthesized_russian_translation():
    term, ru = learning._split_term("Je hand opsteken (Поднять руку)")

    assert term == "Je hand opsteken"
    assert ru == "Поднять руку"


@pytest.mark.unit
def test_dict_add_confirmation_card_uses_entities():
    text, entities = learning._dict_add_confirmation_card([
        {"lang": "nl", "kind": "phrase", "word": "Je hand opsteken", "ru": "Поднять руку"},
    ])

    assert text.startswith("Добавлено в словарь")
    assert "Фраза добавлена в словарь нидерландских фраз." in text
    assert "Je hand opsteken - Поднять руку" in text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in entities)
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in entities)


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
