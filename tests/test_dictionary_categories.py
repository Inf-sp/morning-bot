import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import learning_dictionary
import dictionary_import


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
            {"keep": False},
        ]}

    monkeypatch.setattr(learning_dictionary.store, "get_list", lambda *_args: words)
    monkeypatch.setattr(
        learning_dictionary.store, "set_list",
        lambda _key, _cid, value: saved.append([dict(item) for item in value]),
    )
    monkeypatch.setattr(learning_dictionary.ai, "allm_json", analyze)

    asyncio.run(learning_dictionary.rebuild_dictionary_entries("42"))

    assert [item["id"] for item in words] == ["verb", "english", "adjective"]
    assert words[0]["term"] == "Vaststellen"
    assert words[0]["pos"] == "глагол"
    assert words[0]["srs_level"] == 4
    assert words[1]["lang"] == "en"
    assert words[2]["term"] == "Koppig"
    assert words[2].get("article", "") == ""
    assert all(item["dictionary_rebuild_version"] == 2 for item in words)
    assert all(item["study_card_version"] == 1 for item in words)
    assert saved
