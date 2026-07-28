import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import learning_dictionary
import trainer
import trainer_engine
import trainer_exercises
from dictionary_model import display_term, normalize_entry
from ui.builder import MessageBuilder
from ui.learning_entry import render_learning_entry


def test_startup_migration_capitalizes_legacy_terms_and_translations(monkeypatch):
    data = {
        "42": [
            {"term": "bewonderen", "translation": "bewondering"},
            {"term": "gevolg", "article": "het", "translation": "последствие"},
            {"word": "understand", "ru": "понимать"},
        ],
    }

    monkeypatch.setattr(learning_dictionary.store, "_load", lambda key: data if key == config.DICT_KEY else {})
    monkeypatch.setattr(learning_dictionary.store, "_save", lambda key, value: data.update(value))

    assert learning_dictionary.migrate_dict_caps() is True
    assert data["42"][0]["term"] == "Bewonderen"
    assert data["42"][0]["translation"] == "Восхищаться"
    assert data["42"][1]["term"] == "Gevolg"
    assert data["42"][1]["translation"] == "Последствие"
    assert data["42"][2]["word"] == "Understand"
    assert data["42"][2]["ru"] == "Понимать"


def test_article_card_keeps_natural_sentence_case_after_migration():
    assert display_term("Gevolg", "het") == "Het gevolg"

    builder = MessageBuilder()
    render_learning_entry(builder, {
        "term": "Gevolg", "article": "het", "translation": "Последствие",
        "pos": "существительное", "plural": "gevolgen",
    })

    assert "Het gevolg → Последствие" in builder.build().text


def test_known_dutch_phrases_keep_their_own_translation():
    entry = normalize_entry({
        "term": "Wat balen",
        "translation": "Что ты делаешь там?",
        "lang": "nl",
    })

    assert entry["term"] == "Wat balen"
    assert entry["translation"] == "Вот досада!"


def test_trainer_options_use_the_same_capitalized_format():
    options = trainer._options({
        "correct": "begrijpen",
        "wrong": ["bewonderen", "vervangen"],
    })

    assert set(options) == {"Begrijpen", "Bewonderen", "Vervangen"}


def test_trainer_options_keep_commas_inside_full_translation():
    options = trainer._options({
        "correct": "Я думаю, это глупо",
        "wrong": ["Поддерживать", "Объяснять"],
    })

    assert "Я думаю, это глупо" in options


def test_translation_exercise_keeps_full_answer_and_three_options():
    entry = {
        "term": "Ik vind het stom",
        "translation": "Я думаю, это глупо",
        "lang": "nl",
    }
    other = [
        {"term": "Ik steun je", "translation": "Я тебя поддерживаю", "lang": "nl"},
        {"term": "Ik leg het uit", "translation": "Я это объясняю", "lang": "nl"},
    ]

    data = trainer_exercises.build_exercise(
        entry, other, trainer_engine.EXERCISE_CHOOSE_TRANSLATION,
    )

    assert data["correct"] == "Я думаю, это глупо"
    assert len(data["wrong"]) == 2


def test_recall_uses_local_distractors_with_the_same_part_of_speech():
    entry = {
        "term": "Begeleiding", "translation": "Сопровождение",
        "lang": "nl", "pos": "noun",
    }
    unrelated_shapes = [
        {"term": "Voldoende", "translation": "Достаточный", "lang": "nl", "pos": "adjective"},
        {"term": "Ontspannen", "translation": "Расслабляться", "lang": "nl", "pos": "verb"},
    ]

    data = trainer_exercises.build_exercise(
        entry, unrelated_shapes, trainer_engine.EXERCISE_RECALL,
    )

    assert data is not None
    assert len(data["wrong"]) == 2
    assert set(data["wrong"]).issubset({"huis", "boek", "vriend", "trein"})


def test_translation_exercise_uses_full_local_distractors_for_a_phrase():
    entry = {
        "term": "Ik vind het stom", "translation": "Я думаю, это глупо",
        "lang": "nl",
    }
    word_entries = [
        {"term": "Ondersteunen", "translation": "Поддерживать", "lang": "nl"},
        {"term": "Uitleggen", "translation": "Объяснять", "lang": "nl"},
    ]

    data = trainer_exercises.build_exercise(
        entry, word_entries, trainer_engine.EXERCISE_CHOOSE_TRANSLATION,
    )

    assert data is not None
    assert all(len(option.split()) >= 4 for option in data["wrong"])


def test_phrase_quiz_uses_full_local_distractors_not_other_dictionary_entries():
    entry = {
        "term": "Ik ben op zoek naar een rode kitten voor een klein prijsje",
        "translation": "Я ищу красного котенка за низкую цену",
        "lang": "nl",
    }
    personal_dictionary = [
        {"term": "Wegens", "translation": "Из-за (как door, omdat)", "lang": "nl"},
        {"term": "Nieuw", "translation": "Новый [ни-ве]", "lang": "nl"},
    ]

    data = trainer_exercises.build_exercise(
        entry, personal_dictionary, trainer_engine.EXERCISE_CHOOSE_TRANSLATION,
    )

    assert data is not None
    assert len(data["wrong"]) == 2
    assert not set(data["wrong"]) & {item["translation"] for item in personal_dictionary}
    assert all(len(option.split()) >= 4 for option in data["wrong"])


def test_normalize_dictionary_merges_same_term_with_different_translations(monkeypatch):
    stored = [
        {
            "lang": "nl",
            "term": "bevelen",
            "translation": "Приказывать",
            "examples": [{"text": "Ik beveel hem te stoppen.", "translation": "Я приказываю ему остановиться."}],
        },
        {"lang": "nl", "term": "Bevelen", "translation": "Отдавать приказ"},
    ]

    monkeypatch.setattr(learning_dictionary.store, "get_list", lambda *_args: list(stored))
    monkeypatch.setattr(
        learning_dictionary.store,
        "set_list",
        lambda _key, _cid, entries: stored.__setitem__(slice(None), entries),
    )

    normalized = learning_dictionary.normalize_user_dictionary("dictionary-duplicates")

    assert len(normalized) == 1
    assert normalized[0]["translation"] == "Приказывать; Отдавать приказ"
    assert normalized[0]["examples"] == [{
        "text": "Ik beveel hem te stoppen.",
        "translation": "Я приказываю ему остановиться.",
    }]
