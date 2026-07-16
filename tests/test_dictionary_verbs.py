import asyncio
import logging
import os
from datetime import datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import dictionary_import


def _base_entry(term="vervangen"):
    return {
        "lang": "nl",
        "term": term,
        "article": "",
        "translation": "заменять",
        "breakdown": "глагол, инфинитив",
        "examples": [{"text": "Ik wil dit vervangen.", "translation": "Я хочу это заменить."}],
        "pos": "глагол",
        "forms": [],
    }


def _analysis(**changes):
    data = {
        "is_verb": True,
        "infinitive": "vervangen",
        "translations": ["заменять", "менять"],
        "past_singular": "verving",
        "past_participle": "vervangen",
        "auxiliary": "hebben",
        "perfect_form": "heeft vervangen",
        "verb_type": "strong",
        "example_nl": "Ik ga mijn oude telefoon vervangen.",
        "example_ru": "Я собираюсь заменить свой старый телефон.",
        "confidence": 0.98,
    }
    data.update(changes)
    return data


def test_strong_verb_is_enriched_and_rendered_compactly(monkeypatch):
    async def fake_request(_word):
        return _analysis()

    monkeypatch.setattr(dictionary_import, "_request_verb_analysis", fake_request)
    entry = asyncio.run(dictionary_import._enrich_dutch_verb(_base_entry("Vervangen"), "42"))
    message = dictionary_import._dict_entry_message(entry, status="added")

    assert entry["term"] == "vervangen"
    assert entry["translation"] == "заменять, менять"
    assert entry["past_singular"] == "verving"
    assert entry["perfect_form"] == "heeft vervangen"
    assert entry["analysis_provider"] == "app_llm"
    assert message.text == (
        "✅ Добавлено\n\n"
        "Vervangen → заменять, менять\n\n"
        "Формы: vervangen · verving · heeft vervangen\n"
        "Тип: сильный глагол\n\n"
        "💡 Пример: Ik ga mijn oude telefoon vervangen → "
        "Я собираюсь заменить свой старый телефон."
    )
    assert "Разбор:" not in message.text
    assert "auxiliary" not in message.text
    assert "confidence" not in message.text


def test_weak_verb_example_with_conjugated_stem_passes_validation(monkeypatch):
    async def fake_request(_word):
        return _analysis(
            infinitive="werken",
            translations=["работать"],
            past_singular="werkte",
            past_participle="gewerkt",
            perfect_form="heeft gewerkt",
            verb_type="weak",
            example_nl="Ik werk vandaag thuis.",
            example_ru="Сегодня я работаю дома.",
        )

    monkeypatch.setattr(dictionary_import, "_request_verb_analysis", fake_request)
    entry = asyncio.run(dictionary_import._enrich_dutch_verb(_base_entry("werken"), "42"))
    text = dictionary_import._dict_entry_message(entry).text

    assert "Формы: werken · werkte · heeft gewerkt" in text
    assert "Тип: слабый глагол" in text
    assert "💡 Пример: Ik werk vandaag thuis → Сегодня я работаю дома." in text


def test_verb_with_zijn_uses_is_perfect_form(monkeypatch):
    async def fake_request(_word):
        return _analysis(
            infinitive="vertrekken",
            translations=["уезжать", "отправляться"],
            past_singular="vertrok",
            past_participle="vertrokken",
            auxiliary="zijn",
            perfect_form="is vertrokken",
            example_nl="De trein is al vertrokken.",
            example_ru="Поезд уже уехал.",
        )

    monkeypatch.setattr(dictionary_import, "_request_verb_analysis", fake_request)
    entry = asyncio.run(dictionary_import._enrich_dutch_verb(_base_entry("vertrekken"), "42"))
    text = dictionary_import._dict_entry_message(entry).text

    assert entry["auxiliary"] == "zijn"
    assert "Формы: vertrekken · vertrok · is vertrokken" in text


def test_low_confidence_hides_forms_and_type_but_keeps_valid_example(monkeypatch):
    async def fake_request(_word):
        return _analysis(confidence=0.74)

    monkeypatch.setattr(dictionary_import, "_request_verb_analysis", fake_request)
    entry = asyncio.run(dictionary_import._enrich_dutch_verb(_base_entry(), "42"))
    text = dictionary_import._dict_entry_message(entry).text

    assert "Формы: не удалось проверить" in text
    assert "Тип:" not in text
    assert "💡 Пример:" in text


def test_missing_main_form_uses_unverified_forms_without_null(monkeypatch):
    async def fake_request(_word):
        return _analysis(past_singular=None)

    monkeypatch.setattr(dictionary_import, "_request_verb_analysis", fake_request)
    entry = asyncio.run(dictionary_import._enrich_dutch_verb(_base_entry(), "42"))
    text = dictionary_import._dict_entry_message(entry).text

    assert "Формы: не удалось проверить" in text
    assert "null" not in text.casefold()
    assert "None" not in text


def test_invalid_analysis_falls_back_and_logs_without_blocking(caplog, monkeypatch):
    async def fake_request(_word):
        return _analysis(auxiliary="hebben", perfect_form="is vervangen")

    monkeypatch.setattr(dictionary_import, "_request_verb_analysis", fake_request)
    with caplog.at_level(logging.WARNING):
        entry = asyncio.run(dictionary_import._enrich_dutch_verb(_base_entry(), "user-42"))
    text = dictionary_import._dict_entry_message(entry).text

    assert entry["verb_analysis_failed"] is True
    assert "Не удалось получить формы глагола." in text
    assert "Формы:" not in text
    assert "operation=dutch_verb_analysis" in caplog.text
    assert "user-42" in caplog.text
    assert "vervangen" in caplog.text


def test_cached_analysis_is_reused_without_new_request(monkeypatch):
    cached = {
        **_base_entry(),
        "infinitive": "vervangen",
        "past_singular": "verving",
        "past_participle": "vervangen",
        "auxiliary": "hebben",
        "perfect_form": "heeft vervangen",
        "verb_type": "strong",
        "example_nl": "Ik ga mijn telefoon vervangen.",
        "example_ru": "Я собираюсь заменить телефон.",
        "analysis_confidence": 0.97,
        "analysis_provider": "app_llm",
        "analysis_updated_at": "2026-07-16T12:00:00+02:00",
    }
    monkeypatch.setattr(dictionary_import.store, "get_list", lambda _key, _cid: [cached])

    async def fail_request(_word):
        raise AssertionError("cached verb must not call the model")

    monkeypatch.setattr(dictionary_import, "_request_verb_analysis", fail_request)
    entry = asyncio.run(dictionary_import._enrich_dutch_verb(_base_entry(), "42"))

    assert entry["perfect_form"] == "heeft vervangen"
    assert entry["analysis_updated_at"] == "2026-07-16T12:00:00+02:00"


def test_request_uses_cohere_gemini_public_fallback_and_retries_timeout(monkeypatch):
    calls = []

    async def fake_allm_json(prompt, *_args, **kwargs):
        calls.append((prompt, kwargs))
        if len(calls) == 1:
            raise asyncio.TimeoutError
        return _analysis()

    monkeypatch.setattr(dictionary_import.ai, "allm_json", fake_allm_json)
    result = asyncio.run(dictionary_import._request_verb_analysis("vervangen"))

    assert result["is_verb"] is True
    assert len(calls) == 2
    assert calls[0][1]["order"] == ("cohere", "gemini", "github_models")
    assert calls[0][1]["fallback_allowed"] is True
    assert calls[0][1]["privacy_level"] == "public"
    assert '"word": "vervangen"' in calls[0][0]
    assert "user-42" not in calls[0][0]


def test_strict_schema_rejects_extra_properties():
    data = _analysis(extra="not allowed")

    result, reason = dictionary_import._validate_verb_analysis(data)

    assert result is None
    assert reason == "schema_keys"


def test_analysis_failure_still_saves_word_and_shows_safe_fallback(monkeypatch):
    saved = []
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def fake_normalize(*_args, **_kwargs):
        return {
            **_base_entry(),
            "added_at": datetime.now(dictionary_import.config.TZ).isoformat(),
            "status": "new",
            "last_shown_at": None,
        }

    async def fail_request(_word):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(dictionary_import, "_normalize_dict_entry_full", fake_normalize)
    monkeypatch.setattr(dictionary_import, "_request_verb_analysis", fail_request)
    monkeypatch.setattr(dictionary_import.store, "get_list", lambda _key, _cid: [])
    monkeypatch.setattr(
        dictionary_import.store, "add_to_list",
        lambda _key, _cid, entry: saved.append(dict(entry)),
    )

    asyncio.run(dictionary_import.add_dict_entry_from_chat(Bot(), "42", "vervangen", "nl"))

    assert saved[0]["term"] == "vervangen"
    assert saved[0]["verb_analysis_failed"] is True
    assert sent[0]["text"].startswith("✅ Добавлено")
    assert "Не удалось получить формы глагола." in sent[0]["text"]
    assert "provider unavailable" not in sent[0]["text"]


def test_non_verb_keeps_existing_flow_without_verb_request(monkeypatch):
    noun = {
        **_base_entry("huis"),
        "article": "het",
        "translation": "дом",
        "breakdown": "существительное, het-слово",
        "pos": "существительное",
    }

    async def fail_request(_word):
        raise AssertionError("noun must not request verb analysis")

    monkeypatch.setattr(dictionary_import, "_request_verb_analysis", fail_request)
    result = asyncio.run(dictionary_import._enrich_dutch_verb(noun, "42"))

    assert result == noun
    assert "Формы:" not in dictionary_import._dict_entry_message(result).text


def test_successful_analysis_fields_are_saved_in_existing_dictionary_record(monkeypatch):
    stored = []
    monkeypatch.setattr(dictionary_import.store, "get_list", lambda _key, _cid: [])
    monkeypatch.setattr(
        dictionary_import.store, "add_to_list",
        lambda _key, _cid, entry: stored.append(dict(entry)),
    )

    entry = {
        **_base_entry(),
        **{
            "infinitive": "vervangen",
            "past_singular": "verving",
            "past_participle": "vervangen",
            "auxiliary": "hebben",
            "perfect_form": "heeft vervangen",
            "verb_type": "strong",
            "example_nl": "Ik wil mijn telefoon vervangen.",
            "example_ru": "Я хочу заменить телефон.",
            "analysis_confidence": 0.98,
            "analysis_provider": "app_llm",
            "analysis_updated_at": "2026-07-16T12:00:00+02:00",
            "added_at": "2026-07-16T12:00:00+02:00",
        },
    }

    status, saved = dictionary_import._save_normalized_dict_entry("42", entry)

    assert status == "added"
    for key in (
        "infinitive", "past_singular", "past_participle", "auxiliary",
        "perfect_form", "verb_type", "example_nl", "example_ru",
        "analysis_confidence", "analysis_provider", "analysis_updated_at",
    ):
        assert stored[0][key] == saved[key] == entry[key]
