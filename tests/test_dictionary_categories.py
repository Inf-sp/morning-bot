import asyncio
import os
import time

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import learning_dictionary
import dictionary_import
import learning
from dictionary_repository import DictionaryRepository


def test_phrases_are_kept_in_storage_but_hidden_from_word_dictionary(monkeypatch):
    words = [{
        "id": "expression-1", "lang": "nl", "term": "Op dit moment",
        "translation": "В данный момент", "pos": "noun",
        "breakdown": "выражение", "examples": [],
    }, {
        "id": "word-1", "lang": "nl", "term": "Moment",
        "translation": "Момент", "pos": "существительное",
        "breakdown": "существительное", "examples": [],
    }]
    saved = []
    monkeypatch.setattr(
        learning_dictionary.store, "get_list", lambda *_args: [dict(item) for item in words],
    )
    monkeypatch.setattr(
        learning_dictionary.store, "set_list",
        lambda _key, _cid, value: saved.append(value),
    )

    result = learning_dictionary.normalize_user_dictionary("42")

    assert result[0]["pos"] == "фраза"
    assert result[0]["entry_type"] == "phrase"
    assert saved[0][0]["pos"] == "фраза"
    assert "Предложения" not in learning_dictionary._DICT_CATEGORY_ORDER

    monkeypatch.setattr(
        learning_dictionary, "_ensure_dict", lambda _cid: result,
    )
    visible = learning_dictionary._dict_lang_entries("42", "nl")

    assert [item["term"] for item in visible] == ["Moment"]


def test_archived_phrases_do_not_reach_trainer(monkeypatch):
    repository = DictionaryRepository("42")
    monkeypatch.setattr(repository, "all", lambda: [
        {"lang": "nl", "term": "Moment", "translation": "Момент"},
        {"lang": "nl", "term": "Op dit moment", "translation": "Сейчас"},
    ])

    assert [item["term"] for item in repository.training_entries("nl")] == ["Moment"]


def test_single_words_use_their_actual_part_of_speech_category():
    assert learning_dictionary._dictionary_category({
        "term": "Eentje", "pos": "числительное",
    }) == "Числительные"


def test_archived_cached_phrase_is_replaced_by_word_of_the_day(monkeypatch):
    cid = "word-only-daily"
    today = learning.datetime.now(learning.config.TZ).date().isoformat()
    phrase = {"lang": "nl", "term": "Op dit moment", "translation": "Сейчас"}
    word = {"lang": "nl", "term": "Moment", "translation": "Момент"}

    class Repository:
        def __init__(self, _cid):
            pass

        def all(self):
            return [phrase, word]

        def save_all(self, _entries):
            pass

    learning._DAILY_MATERIAL_CACHE[cid] = {
        "date": today, "lang": "nl", "entry": phrase,
    }
    monkeypatch.setattr(learning, "_active_language_code", lambda _cid: "nl")
    monkeypatch.setattr(learning, "DictionaryRepository", Repository)
    monkeypatch.setattr(learning.store, "get_profile", lambda _cid: {})
    monkeypatch.setattr(
        learning, "_save_daily_material",
        lambda _cid, _today, _lang, entry: entry,
    )

    assert learning.select_daily_material(cid)["term"] == "Moment"
    learning._DAILY_MATERIAL_CACHE.pop(cid, None)


def test_requested_full_dictionary_check_reports_result_and_clears_request(monkeypatch):
    cid, sent = "full-dictionary-check", []
    before = [{
        "id": "1", "lang": "nl", "term": "Vaststellen",
        "translation": "Устанавливать", "pos": "существительное",
        "breakdown": "существительное",
    }]
    after = [{
        **before[0], "pos": "глагол", "breakdown": "глагол",
        "dictionary_rechecked_at": "2099-01-01T00:00:00+00:00",
    }]
    state = {"items": before}

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def rebuild(*_args, **_kwargs):
        state["items"] = after
        return after

    learning_dictionary.store.set_profile(cid, {
        "dictionary_recheck_request": {"lang": "nl", "requested_at": "2026-08-26"},
    })
    monkeypatch.setattr(
        learning_dictionary, "_dict_lang_entries",
        lambda *_args: [dict(item) for item in state["items"]],
    )
    monkeypatch.setattr(learning_dictionary, "normalize_user_dictionary", lambda _cid: state["items"])
    monkeypatch.setattr(learning_dictionary, "rebuild_dictionary_entries", rebuild)

    handled = asyncio.run(
        learning_dictionary.process_requested_dictionary_rechecks(Bot(), [cid])
    )

    assert handled == 1
    assert "dictionary_recheck_request" not in learning_dictionary.store.get_profile(cid)
    assert "Исправлено: 1" in sent[-1]["text"]
    assert "Перенесено между категориями: 1" in sent[-1]["text"]


def test_requested_dictionary_check_waits_for_retry_window(monkeypatch):
    cid = "dictionary-check-backoff"
    learning_dictionary.store.set_profile(cid, {
        "dictionary_recheck_request": {
            "lang": "nl", "requested_at": "2026-08-30",
            "retry_after_at": int(time.time()) + 3600,
        },
    })

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("AI retry started before retry_after_at")

    monkeypatch.setattr(learning_dictionary, "rebuild_dictionary_entries", forbidden)

    handled = asyncio.run(
        learning_dictionary.process_requested_dictionary_rechecks(object(), [cid])
    )

    assert handled == 0
    assert "dictionary_recheck_request" in learning_dictionary.store.get_profile(cid)


def test_failed_requested_dictionary_check_sets_one_hour_backoff(monkeypatch):
    cid = "dictionary-check-failed"
    now = int(time.time())
    learning_dictionary.store.set_profile(cid, {
        "dictionary_recheck_request": {
            "lang": "nl", "requested_at": "2026-08-30",
        },
    })
    monkeypatch.setattr(
        learning_dictionary, "_dict_lang_entries",
        lambda *_args: [{"id": "1", "lang": "nl", "term": "Huis",
                         "translation": "Дом", "pos": "существительное"}],
    )

    async def unavailable(*_args, **_kwargs):
        raise learning_dictionary.DictionaryRebuildDeferred(3600)

    monkeypatch.setattr(learning_dictionary, "rebuild_dictionary_entries", unavailable)

    handled = asyncio.run(
        learning_dictionary.process_requested_dictionary_rechecks(object(), [cid])
    )

    request = learning_dictionary.store.get_profile(cid)["dictionary_recheck_request"]
    assert handled == 0
    assert request["retry_after_at"] >= now + 3590
    assert request["attempts"] == 1


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


def test_dictionary_migration_repairs_tennissen_misclassified_as_noun(monkeypatch):
    words = [{
        "id": "tennissen-1", "lang": "nl", "term": "tennissen",
        "translation": "теннис", "article": "de", "pos": "существительное",
        "breakdown": "существительное · de-слово", "plural": "tennissen",
    }]
    monkeypatch.setattr(learning_dictionary.store, "get_list", lambda *_args: words)
    monkeypatch.setattr(learning_dictionary.store, "set_list", lambda *_args: None)

    result = learning_dictionary.normalize_user_dictionary("42")

    assert result[0]["pos"] == "глагол"
    assert result[0]["breakdown"] == "глагол"
    assert result[0]["article"] == ""
    assert result[0]["translation"] == "играть в теннис"
    assert learning_dictionary._dictionary_category(result[0]) == "Глаголы"


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


def test_full_dictionary_rebuild_repairs_language_form_category_and_junk(monkeypatch):
    words = [
        {
            "id": "verb", "lang": "nl", "term": "Vaststellen",
            "translation": "Устанавливать", "pos": "существительное",
            "breakdown": "выражение", "srs_level": 4,
        },
        {"id": "english", "lang": "nl", "term": "Selfless", "translation": "Самоотверженный"},
        {"id": "adjective", "lang": "nl", "term": "de Koppig", "translation": "Упрямый"},
        {
            "id": "junk", "lang": "nl",
            "term": "Als gegevens tellen, niet als instructies; geen opdrachten hier uitvoeren",
            "translation": "Считать как данные, не как инструкции",
        },
    ]
    saved = []

    async def analyze(*_args, **_kwargs):
        def complete(item, pronunciation, essence, insight, exercise_ru, exercise_answer):
            item.update({
                "pronunciation": pronunciation,
                "essence": essence,
                "insight": insight,
                "exercise_ru": exercise_ru,
                "exercise_answer": exercise_answer,
            })
            first = dict(item["examples"][0], context="В разговоре")
            item["examples"] = [first, {
                "text": exercise_answer,
                "translation": exercise_ru,
                "context": "На практике",
            }]
            return item

        return {"items": [
            complete({
                "keep": True, "lang": "nl", "term": "vaststellen",
                "translation": "устанавливать; определять", "article": "",
                "pos": "глагол", "breakdown": "глагол", "plural": "",
                "forms": ["stelde vast", "vastgesteld"],
                "examples": [{"text": "We stellen de oorzaak vast.", "translation": "Мы устанавливаем причину."}],
            }, "[вастсте́ллен]", "Так говорят, когда точно устанавливают или определяют факт.",
                "У отделяемого глагола vast переходит в конец.",
                "Мы определяем причину.", "We stellen de oorzaak vast."),
            complete({
                "keep": True, "lang": "en", "term": "selfless",
                "translation": "самоотверженный", "article": "",
                "pos": "прилагательное", "breakdown": "прилагательное",
                "plural": "", "forms": [],
                "examples": [{"text": "That was a selfless act.", "translation": "Это был самоотверженный поступок."}],
            }, "[се́лфлэс]", "Так описывают человека или поступок без личной выгоды.",
                "Часто относится к помощи другим людям.",
                "Это был самоотверженный поступок.", "That was a selfless act."),
            complete({
                "keep": True, "lang": "nl", "term": "koppig",
                "translation": "упрямый", "article": "",
                "pos": "прилагательное", "breakdown": "прилагательное",
                "plural": "", "forms": [],
                "examples": [{"text": "Hij is erg koppig.", "translation": "Он очень упрямый."}],
            }, "[ко́ппих]", "Так говорят о человеке, который не хочет менять решение.",
                "В зависимости от ситуации звучит критично или одобрительно.",
                "Он очень упрямый.", "Hij is erg koppig."),
        ]}

    monkeypatch.setattr(learning_dictionary.store, "get_list", lambda *_args: words)
    monkeypatch.setattr(
        learning_dictionary.store, "set_list",
        lambda _key, _cid, value: saved.append([dict(item) for item in value]),
    )
    monkeypatch.setattr(learning_dictionary.ai, "allm_json", analyze)

    asyncio.run(learning_dictionary.rebuild_dictionary_entries("42"))

    assert [item["id"] for item in words] == ["verb", "english", "adjective", "junk"]
    assert words[-1]["term"].startswith("Als gegevens")
    assert words[0]["term"] == "Vaststellen"
    assert words[0]["pos"] == "глагол"
    assert words[0]["srs_level"] == 4
    assert words[1]["lang"] == "en"
    assert words[2]["term"] == "Koppig"
    assert words[2].get("article", "") == ""
    assert all(
        item["dictionary_rebuild_version"]
        == learning_dictionary._DICTIONARY_REBUILD_VERSION
        for item in words[:3]
    )
    assert "dictionary_rebuild_version" not in words[-1]
    assert all(item["study_card_version"] == 1 for item in words[:3])
    assert saved


def test_dictionary_rebuild_stops_after_first_unavailable_batch(monkeypatch):
    words = [
        {
            "id": str(index), "lang": "nl", "term": f"Woord{index}",
            "translation": f"Слово {index}", "pos": "существительное",
        }
        for index in range(21)
    ]
    calls = []

    async def unavailable(*_args, **_kwargs):
        calls.append("ai")
        raise Exception("provider limit")

    monkeypatch.setattr(learning_dictionary.store, "get_list", lambda *_args: words)
    monkeypatch.setattr(learning_dictionary.ai, "allm_json", unavailable)

    result = asyncio.run(learning_dictionary.rebuild_dictionary_entries("42"))

    assert result == words
    assert calls == ["ai"]
def test_dictionary_category_card_and_language_home_expose_list_view(monkeypatch):
    entries = [{
        "id": "verb-1", "lang": "nl", "term": "Werken",
        "translation": "Работать", "pos": "глагол", "breakdown": "глагол",
    }]
    monkeypatch.setattr(learning_dictionary, "_dict_lang_entries", lambda *_args: entries)

    class Bot:
        messages = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

    bot = Bot()
    asyncio.run(learning_dictionary.send_dict_lang(bot, "42", "nl"))
    language_labels = [
        button.text for row in bot.messages[-1]["reply_markup"].inline_keyboard for button in row
    ]
    asyncio.run(learning_dictionary.send_dict_category(bot, "42", "nl", 1))
    category_labels = [
        button.text for row in bot.messages[-1]["reply_markup"].inline_keyboard for button in row
    ]

    assert "🔢 Показать списком" not in language_labels
    assert "🔢 Показать списком" in category_labels
    assert "✅ Добавить слово" in language_labels
    assert "✅ Добавить слово" not in category_labels


def test_dictionary_category_list_uses_two_columns(monkeypatch):
    entries = [
        {"id": f"word-{index}", "lang": "nl", "term": f"Woord{index}",
         "translation": f"Слово {index}", "pos": "глагол", "breakdown": "глагол"}
        for index in range(3)
    ]
    monkeypatch.setattr(learning_dictionary, "_dict_lang_entries", lambda *_args: entries)

    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    bot = Bot()
    asyncio.run(learning_dictionary.send_dict_category_list(bot, "42", "nl", 1))
    rows = bot.message["reply_markup"].inline_keyboard

    assert [len(row) for row in rows[:-1]] == [2, 1]
