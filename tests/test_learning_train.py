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
    assert len(card["wrong"]) == 3
    assert "____" in card["blank_phrase"]
    assert card["test_full_phrase"] == "Ik ben onderweg"


@pytest.mark.unit
def test_fallback_phrase_quiz_card_prefers_last_content_token():
    card = learning._fallback_phrase_quiz_card(
        "Geld dat op je rekening staat",
        "Деньги, которые лежат на твоём счёте",
        "нидерландский",
    )

    assert card["correct"] == "staat"
    assert card["blank_phrase"] == "Geld dat op je rekening ____"
    assert card["construction"] == "staat"


@pytest.mark.unit
def test_phrase_poll_question_is_formatted_with_entities():
    question, entities = learning._phrase_poll_question("Ik maak me zorgen om ____", "Я переживаю за тебя")

    assert question.startswith("🧩 Проверь себя\n\nIk maak me zorgen om ____")
    assert "Перевод:" not in question
    assert "Я переживаю за тебя" not in question
    assert "Выбери подходящее слово:" in question
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

    assert explanation == "Ik maak me zorgen om jou → Я переживаю за тебя"
    assert "Ответ:" not in explanation


@pytest.mark.unit
def test_phrase_card_consistency_rejects_pattern_not_present():
    card = {
        "blank_phrase": "Deze auto is ____ duur.",
        "test_full_phrase": "Deze auto is bijzonder duur.",
        "correct": "bijzonder",
        "target_token": "bijzonder",
        "sentence_ru": "Эта машина необычно дорогая.",
        "construction": "bijzonder + прилагательное",
        "construction_meaning": "особенно, необычно + прилагательное",
        "self_check": {
            "translation_matches_learning_phrase": True,
            "pattern_present_in_learning_phrase": True,
            "target_token_role_ok": True,
            "learning_phrase_natural": True,
            "test_checks_same_rule": True,
            "test_is_new_not_copy": True,
            "no_mixed_meanings": True,
        },
    }

    assert not learning._phrase_card_is_consistent("Dat is bijzonder.", "Это необычно.", card)


@pytest.mark.unit
def test_phrase_start_card_or_fallback_uses_local_card_when_generation_failed():
    card = learning._phrase_start_card_or_fallback({}, "Ik ben onderweg", "Я в пути", "нидерландский")

    assert card["correct"] == "onderweg"
    assert card["blank_phrase"] == "Ik ben ____"
    assert len(card["wrong"]) == 3


@pytest.mark.unit
@pytest.mark.anyio
async def test_render_phrase_intro_shows_learning_translation_not_test_translation(monkeypatch):
    sent = []
    cid = "cid"

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def gen_card(*args, **kwargs):
        return {
            "blank_phrase": "Het boek dat op de plank ____",
            "test_full_phrase": "Het boek dat op de plank staat",
            "correct": "staat",
            "target_token": "staat",
            "wrong": ["ligt", "hangt", "zit"],
            "sentence_ru": "Книга, которая стоит на полке",
            "construction": "dat op ... staat",
            "construction_meaning": "что-то, что находится/стоит на чем-то",
            "short_rule": "dat op ... staat = что-то находится на чем-то",
            "other_forms": [],
        }

    monkeypatch.setattr(learning, "_train_phrases", lambda chat_id, language: [
        ("Geld dat op je rekening staat", "Деньги, которые лежат на твоём счёте"),
    ])
    monkeypatch.setattr(learning, "_train_words", lambda chat_id, language: [])
    monkeypatch.setattr(learning, "_gen_consistent_phrase_card", gen_card)
    monkeypatch.setitem(learning.store.train_state, cid, {"lang": "нидерландский", "used_phrases": []})

    await learning._render_phrase_quiz(Bot(), cid)

    assert sent
    assert "Geld dat op je rekening staat" in sent[0]["text"]
    assert "Перевод: Деньги, которые лежат на твоём счёте" in sent[0]["text"]
    assert "Книга, которая стоит на полке" not in sent[0]["text"]
    assert learning.store.train_state[cid]["sentence_ru"] == "Книга, которая стоит на полке"


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
def test_chat_dict_word_request_without_dictionary_word_extracts_payload():
    payload, lang = learning._extract_chat_dict_add("Добавь слово Toevoegen")

    assert payload == "Toevoegen"
    assert lang == "nl"


@pytest.mark.unit
def test_chat_dict_word_request_strips_service_words_and_lang_adjective():
    payload, lang = learning._extract_chat_dict_add("Нужно добавить английское слово apple")

    assert payload == "apple"
    assert lang == "en"


@pytest.mark.unit
def test_chat_dict_word_request_strips_polite_prefix():
    payload, lang = learning._extract_chat_dict_add("Пожалуйста добавь новое слово book")

    assert payload == "book"
    assert lang == "nl"


@pytest.mark.unit
def test_chat_dict_phrase_request_without_dictionary_word_extracts_payload():
    payload, lang = learning._extract_chat_dict_add("Добавь фразу Je hand opsteken")

    assert payload == "Je hand opsteken"
    assert lang == "nl"


@pytest.mark.unit
def test_chat_dict_question_is_not_treated_as_add_request():
    payload, lang = learning._extract_chat_dict_add("Какое слово добавить?")

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

    assert text.startswith("Словарь")
    assert "✅ Фраза добавлена в нидерландские фразы" in text
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

    assert "✅ Слово добавлено в нидерландские слова" in text
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
    assert "✅ Фраза уже есть в нидерландских фразах" in text
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
    assert "✅ Слово уже есть в нидерландских словах" in text
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
    assert "✅ Фраза уже есть в нидерландских фразах" in sent[0]["text"]
    assert any(e.type == MessageEntity.BLOCKQUOTE for e in sent[0]["entities"])


@pytest.mark.unit
@pytest.mark.anyio
async def test_add_words_batch_uses_parser_detected_language(monkeypatch):
    sent = []
    stored = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(learning, "_ensure_dict", lambda cid: [])
    monkeypatch.setattr(learning, "_parse_batch", lambda text, lang: [
        {"word": "apple", "ru": "яблоко", "lang": "en", "kind": "word"},
    ])
    monkeypatch.setattr(learning.store, "add_to_list", lambda *args: stored.append(args))

    await learning.add_words_batch(
        Bot(),
        "cid",
        "apple",
        "nl",
        detailed_confirmation=True,
    )

    assert stored
    assert stored[0][2]["lang"] == "en"
    assert stored[0][2]["kind"] == "word"
    assert "✅ Слово добавлено в английские слова" in sent[0]["text"]


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
