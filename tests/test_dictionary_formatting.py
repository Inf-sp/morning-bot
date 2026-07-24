import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import learning_dictionary
import trainer
import trainer_engine
import trainer_exercises
from dictionary_model import display_term
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
        {"term": "Ondersteunen", "translation": "Поддерживать", "lang": "nl"},
        {"term": "Uitleggen", "translation": "Объяснять", "lang": "nl"},
    ]

    data = trainer_exercises.build_exercise(
        entry, other, trainer_engine.EXERCISE_CHOOSE_TRANSLATION,
    )

    assert data["correct"] == "Я думаю, это глупо"
    assert len(data["wrong"]) == 2
