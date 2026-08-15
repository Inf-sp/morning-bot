import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import learning_data_quality


def _ok(text):
    return {
        "ok": True,
        "available": True,
        "text": text,
        "corrected_text": text,
        "issues": [],
    }


def _issue_report(text, original, replacement, issue_type="misspelling", rule_id="MORFOLOGIK_RULE_NL_NL"):
    offset = text.index(original)
    return {
        "ok": False,
        "available": True,
        "text": text,
        "corrected_text": text[:offset] + replacement + text[offset + len(original):],
        "issues": [{
            "offset": offset,
            "length": len(original),
            "original": original,
            "replacements": [replacement],
            "rule_id": rule_id,
            "issue_type": issue_type,
            "category": "Grammar" if issue_type == "grammar" else "Typographical",
        }],
    }


def test_legacy_entry_normalization_is_deterministic():
    normalized, changed = learning_data_quality.normalize_entry({
        "word": "  DE   Afspraak. ",
        "ru": " договорённость ",
        "language": "Dutch",
        "examples": [{"nl": "Ik heb morgen een afspraak!!", "ru": "У меня завтра встреча"}],
        "forms": " afspraak, afspraken ",
    })

    assert changed is True
    assert normalized["article"] == "de"
    assert normalized["term"] == "Afspraak"
    assert normalized["translation"] == "Договорённость"
    assert normalized["examples"] == [{
        "text": "Ik heb morgen een afspraak!",
        "translation": "У меня завтра встреча.",
    }]
    assert normalized["forms"] == ["afspraak", "afspraken"]
    assert "word" not in normalized and "ru" not in normalized


def test_new_dutch_entry_checks_foreign_fields_but_never_translation(monkeypatch):
    calls = []

    async def check(text, language, **kwargs):
        calls.append((text, language))
        if text == "vervanggen":
            return _issue_report(text, "vervanggen", "vervangen")
        return _ok(text)

    monkeypatch.setattr(learning_data_quality.language_tool, "check_text_retry", check)
    checked = asyncio.run(learning_data_quality.check_new_entry({
        "lang": "nl",
        "term": "vervanggen",
        "translation": "заменять",
        "examples": [{
            "text": "Ik wil mijn telefoon vervangen.",
            "translation": "Я хочу заменить телефон.",
        }],
    }))

    assert checked["term"] == "Vervangen"
    sent_text = " ".join(text for text, _language in calls)
    assert "заменять" not in sent_text
    assert "Я хочу" not in sent_text
    assert all(language == "nl-NL" for _text, language in calls)


def test_questionable_grammar_is_not_changed_automatically(monkeypatch):
    sentence = "Ik heb een auto gekocht gisteren."

    async def check(text, language, **kwargs):
        if text == sentence:
            return _issue_report(
                text,
                "een auto gekocht gisteren",
                "gisteren een auto gekocht",
                issue_type="grammar",
                rule_id="WORD_ORDER",
            )
        return _ok(text)

    monkeypatch.setattr(learning_data_quality.language_tool, "check_text_retry", check)
    entry, stats = asyncio.run(learning_data_quality.check_entry({
        "id": "entry-1",
        "lang": "nl",
        "term": "auto kopen",
        "translation": "купить машину",
        "examples": [{"text": sentence, "translation": "Я вчера купил машину."}],
    }))

    assert entry["examples"][0]["text"] == sentence
    assert stats["fixed_fields"] == 0
    assert "language_review_required" not in entry


def test_unavailable_language_tool_does_not_block_new_entry(monkeypatch):
    async def unavailable(text, language, **kwargs):
        return {"ok": False, "available": False, "text": text, "issues": []}

    monkeypatch.setattr(learning_data_quality.language_tool, "check_text_retry", unavailable)
    checked = asyncio.run(learning_data_quality.check_new_entry({
        "lang": "nl", "term": "vervangen", "translation": "заменять",
    }))

    assert checked["term"] == "Vervangen"
    assert "pending_language_check" not in checked
    assert "language_check_status" not in checked


def test_new_verb_does_not_check_the_same_example_twice(monkeypatch):
    calls = []

    async def check(text, language, **kwargs):
        calls.append((text, language))
        return _ok(text)

    monkeypatch.setattr(learning_data_quality.language_tool, "check_text_retry", check)
    asyncio.run(learning_data_quality.check_new_entry({
        "lang": "nl",
        "term": "wandelen",
        "translation": "гулять",
        "examples": [{"text": "Ik wandel elke dag.", "translation": "Я гуляю каждый день."}],
        "example_nl": "Ik wandel elke dag.",
        "forms": ["wandelde", "heeft gewandeld"],
    }))

    assert calls == [
        ("wandelen", "nl-NL"),
        ("Ik wandel elke dag.", "nl-NL"),
    ]
