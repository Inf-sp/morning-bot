import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import dictionary_morning
import learning_dictionary
import settings
import trainer
import trainer_engine
import trainer_exercises
from dictionary_model import display_term, normalize_entry
from ui import learning as learning_ui
from ui.builder import MessageBuilder, MessageSpec
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


def test_money_investment_recall_uses_plausible_financial_distractors():
    data = trainer_exercises.build_exercise(
        {
            "term": "Geld beleggen",
            "translation": "Инвестировать деньги",
            "lang": "nl",
            "pos": "verb",
        },
        [],
        trainer_engine.EXERCISE_RECALL,
    )

    assert data["correct"] == "Geld beleggen"
    assert data["wrong"] == ["Geld uitgeven", "Geld sparen"]


def test_short_verb_translation_does_not_get_sentence_distractors():
    data = trainer_exercises.build_exercise(
        {
            "term": "Omgaan", "translation": "Справляться с",
            "lang": "nl", "pos": "verb",
        },
        [],
        trainer_engine.EXERCISE_CHOOSE_TRANSLATION,
    )

    assert data is not None
    assert all(len(option.split()) <= 3 for option in data["wrong"])


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


def test_daily_learning_notification_has_learning_and_home_buttons(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(settings, "study_lang", lambda _cid: "нидерландский")
    monkeypatch.setattr(
        dictionary_morning,
        "_build_morning_word",
        lambda *_args: (MessageSpec(text="🇳🇱 Слово дня"), []),
    )

    asyncio.run(settings._send_scheduled_notification(Bot(), "42", "daily_words"))

    keyboard = sent[0]["reply_markup"].inline_keyboard
    assert [[(button.text, button.callback_data) for button in row] for row in keyboard] == [
        [("🔕 Отключить уведомления", "set_notifpush_daily_words")],
        [("🧠 Обучение", "notify_learning")],
        [("#️⃣ Главная", "m_menu")],
    ]


def test_morning_word_shows_deep_dive_and_spoiler_answer():
    msg = learning_ui.morning_words("🇳🇱", entries=[{
        "term": "Immers", "pronunciation": "[и́ммерс]", "translation": "ведь · же",
        "essence": "Так напоминают о том, что собеседнику уже должно быть известно.",
        "examples": [
            {"text": "Het is immers weekend!", "translation": "Сейчас ведь выходные!", "context": "Когда спишь до обеда"},
            {"text": "Je bent immers mijn vriend.", "translation": "Ты же мой друг.", "context": "Когда просишь о помощи"},
        ],
        "memory_hook": "ИМею МЕРу — я ВЕДЬ разумный.",
        "usage_note": "Обычно immers стоит в середине предложения.",
        "exercise_ru": "Я ведь читаю книгу.",
        "exercise_answer": "Ik lees immers een boek.",
    }])

    assert "🇳🇱 Слово дня" in msg.text
    assert "Immers\n[и́ммерс] · ведь · же" in msg.text
    assert "В чём суть" in msg.text
    assert "Het is immers weekend! → Сейчас ведь выходные! (Когда спишь до обеда)" in msg.text
    assert "Крючок для памяти" in msg.text
    assert "🎯 Твоя очередь:" in msg.text
    assert "Ответ: Ik lees immers een boek." in msg.text
    spoilers = [entity for entity in msg.entities if entity.type == "spoiler"]
    assert len(spoilers) == 1


def test_daily_word_uses_only_single_words_without_repeating_pool(monkeypatch):
    words = [
        {"term": "Immers", "translation": "ведь", "lang": "nl"},
        {"term": "Inmiddels", "translation": "тем временем", "lang": "nl"},
        {"term": "Eigenlijk", "translation": "вообще-то", "lang": "nl"},
        {"term": "Laat maar", "translation": "забудь", "lang": "nl"},
    ]
    monkeypatch.setattr(dictionary_morning, "_ensure_dict", lambda _cid: words)
    monkeypatch.setattr(dictionary_morning.store, "set_list", lambda *_args: None)

    shown = [
        dictionary_morning.build_daily_practice("42", "nl", mark_shown=True)["entries"][0]["term"]
        for _ in range(3)
    ]

    assert set(shown) == {"Immers", "Inmiddels", "Eigenlijk"}
    assert "Laat maar" not in shown
    assert dictionary_morning.build_daily_practice("42", "nl", mark_shown=True)["entries"] == []


def test_daily_word_deep_dive_is_generated_once_and_saved_hidden(monkeypatch):
    words = [{
        "term": "Immers", "translation": "ведь", "lang": "nl",
        "breakdown": "наречие", "examples": [{"text": "Het is immers weekend.", "translation": "Ведь выходные."}],
    }]
    calls = []
    deep_dive = {
        "pronunciation": "[и́ммерс]", "translation": "ведь · же",
        "essence": "Напоминает об известной причине.",
        "examples": [
            {"text": "Het is immers weekend.", "translation": "Ведь выходные.", "context": "Объяснение"},
            {"text": "Je weet het immers.", "translation": "Ты же это знаешь.", "context": "Напоминание"},
        ],
        "memory_hook": "ИМ-МЕР-С — ВЕДЬ.", "usage_note": "Обычно стоит в середине.",
        "exercise_ru": "Ты же это знаешь.", "exercise_answer": "Je weet het immers.",
    }

    async def fake_allm_json(*_args, **_kwargs):
        calls.append(True)
        return deep_dive

    monkeypatch.setattr(dictionary_morning, "_ensure_dict", lambda _cid: words)
    monkeypatch.setattr(dictionary_morning.ai, "allm_json", fake_allm_json)
    monkeypatch.setattr(dictionary_morning.store, "set_list", lambda *_args: None)

    asyncio.run(dictionary_morning._prepare_deep_dive("42", "nl"))
    asyncio.run(dictionary_morning._prepare_deep_dive("42", "nl"))

    assert len(calls) == 1
    assert words[0]["daily_word_deep_dive"] == deep_dive


def test_daily_word_replaces_saved_placeholder_template(monkeypatch):
    placeholder = {
        "pronunciation": "[русская транскрипция с ударением]",
        "translation": "1-2 значения",
        "essence": "2-3 коротких предложения о смысле и ситуации употребления",
        "examples": [
            {"text": "...", "translation": "...", "context": "..."},
            {"text": "...", "translation": "...", "context": "..."},
        ],
        "memory_hook": "короткая яркая ассоциация",
        "usage_note": "один важный нюанс позиции, регистра или сочетаемости",
        "exercise_ru": "...", "exercise_answer": "...",
    }
    valid = {
        "pronunciation": "[бепе́ркен]", "translation": "ограничивать · сокращать",
        "essence": "Так говорят, когда уменьшают допустимый объём или ставят границу.",
        "examples": [
            {"text": "We moeten de kosten beperken.", "translation": "Нам нужно сократить расходы.", "context": "О бюджете"},
            {"text": "Ik beperk mijn schermtijd.", "translation": "Я ограничиваю время у экрана.", "context": "О привычках"},
        ],
        "memory_hook": "Представь барьер, который ограничивает пространство.",
        "usage_note": "Обычно употребляется с прямым дополнением.",
        "exercise_ru": "Я ограничиваю время у экрана.",
        "exercise_answer": "Ik beperk mijn schermtijd.",
    }
    words = [{
        "term": "Beperken", "translation": "ограничивать", "lang": "nl",
        "breakdown": "глагол", "daily_word_deep_dive": placeholder,
    }]
    calls = []

    async def generate(*_args, **_kwargs):
        calls.append(True)
        return valid

    monkeypatch.setattr(dictionary_morning, "_ensure_dict", lambda _cid: words)
    monkeypatch.setattr(dictionary_morning.ai, "allm_json", generate)
    monkeypatch.setattr(dictionary_morning.store, "set_list", lambda *_args: None)

    asyncio.run(dictionary_morning._prepare_deep_dive("42", "nl"))

    assert calls == [True]
    assert words[0]["daily_word_deep_dive"] == valid
