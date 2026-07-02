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
def test_chat_dict_word_request_extracts_payload_in_any_order():
    payload, lang = learning._extract_chat_dict_add("Слово toevoegen (добавлять) добавить в словарь")

    assert payload == "toevoegen (добавлять)"
    assert lang == "nl"


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

    assert text.startswith("Словарь")
    assert "Фраза добавлена в словарь ✅" in text
    assert "Je hand opsteken - Поднять руку" in text
    assert "Теперь эта фраза будет попадаться в тренировках по нидерландскому" in text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in entities)
    status_offset = text.index("✅ Фраза добавлена")
    assert not any(e.type == MessageEntity.BOLD and e.offset == status_offset for e in entities)
    quote_offset = text.index("Je hand opsteken - Поднять руку")
    assert any(e.type == MessageEntity.BLOCKQUOTE and e.offset == quote_offset for e in entities)


@pytest.mark.unit
def test_dict_add_confirmation_card_for_word_uses_entities():
    text, entities = learning._dict_add_confirmation_card([
        {"lang": "nl", "kind": "word", "word": "Toevoegen", "ru": "добавлять"},
    ])

    assert "Слово добавлено в словарь ✅" in text
    assert "Toevoegen - добавлять" in text
    assert "Теперь это слово будет попадаться в тренировках по нидерландскому" in text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in entities)
    quote_offset = text.index("Toevoegen - добавлять")
    assert any(e.type == MessageEntity.BLOCKQUOTE and e.offset == quote_offset for e in entities)


@pytest.mark.unit
def test_dict_item_key_normalizes_case_and_spaces():
    assert learning._dict_item_key("nl", "phrase", "Je  hand opsteken") == learning._dict_item_key(
        "nl", "phrase", "je hand OPSTEKEN"
    )


@pytest.mark.unit
def test_dict_duplicate_confirmation_card_uses_entities():
    text, entities = learning._dict_duplicate_confirmation_card([
        {"lang": "nl", "kind": "phrase", "word": "Je hand opsteken", "ru": "Поднять руку"},
    ])

    assert text.startswith("Словарь")
    assert "🫪 Фраза уже есть в словаре" in text
    assert "Je hand opsteken - Поднять руку" in text
    assert "Повторно не добавляю" in text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in entities)
    quote_offset = text.index("Je hand opsteken - Поднять руку")
    assert any(e.type == MessageEntity.BLOCKQUOTE and e.offset == quote_offset for e in entities)


@pytest.mark.unit
def test_dict_duplicate_confirmation_card_for_word_uses_entities():
    text, entities = learning._dict_duplicate_confirmation_card([
        {"lang": "nl", "kind": "word", "word": "Toevoegen", "ru": "добавлять"},
    ])

    assert text.startswith("Словарь")
    assert "🫪 Слово уже есть в словаре" in text
    assert "Toevoegen - добавлять" in text
    assert "Повторно не добавляю" in text
    assert any(e.type == MessageEntity.BOLD and e.offset == 0 for e in entities)
    quote_offset = text.index("Toevoegen - добавлять")
    assert any(e.type == MessageEntity.BLOCKQUOTE and e.offset == quote_offset for e in entities)


@pytest.mark.unit
@pytest.mark.anyio
async def test_add_words_batch_skips_existing_duplicate(monkeypatch):
    sent = []
    stored = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(learning, "_ensure_dict", lambda cid: [
        {"lang": "nl", "kind": "phrase", "word": "Je hand opsteken", "ru": "Поднять руку"},
    ])
    monkeypatch.setattr(learning.store, "add_to_list", lambda *args: stored.append(args))

    await learning.add_words_batch(
        Bot(),
        "cid",
        "Je hand opsteken (Поднять руку)",
        "nl",
        detailed_confirmation=True,
    )

    assert stored == []
    assert sent
    assert "Фраза уже есть в словаре 🫪" in sent[0]["text"]
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in sent[0]["entities"])


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
