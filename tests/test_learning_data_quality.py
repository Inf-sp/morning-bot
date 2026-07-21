import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import learning_data_quality
import settings
from ui import settings as settings_ui


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
    assert normalized["term"] == "afspraak"
    assert normalized["translation"] == "договорённость"
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

    assert checked["term"] == "vervangen"
    sent_text = " ".join(text for text, _language in calls)
    assert "заменять" not in sent_text
    assert "Я хочу" not in sent_text
    assert all(language == "nl-NL" for _text, language in calls)


def test_questionable_grammar_is_queued_without_automatic_change(monkeypatch):
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
    entry, _stats, reviews = asyncio.run(learning_data_quality.check_entry({
        "id": "entry-1",
        "lang": "nl",
        "term": "auto kopen",
        "translation": "купить машину",
        "examples": [{"text": sentence, "translation": "Я вчера купил машину."}],
    }))

    assert entry["examples"][0]["text"] == sentence
    assert entry["language_review_required"] is True
    assert reviews[0]["field"] == "examples.0.text"
    assert reviews[0]["suggestion"] == "Ik heb gisteren een auto gekocht."
    assert "WORD_ORDER" not in reviews[0]["reason"]


def test_unavailable_language_tool_marks_entry_for_next_refresh(monkeypatch):
    async def unavailable(text, language, **kwargs):
        return {"ok": False, "available": False, "text": text, "issues": []}

    monkeypatch.setattr(learning_data_quality.language_tool, "check_text_retry", unavailable)
    checked = asyncio.run(learning_data_quality.check_new_entry({
        "lang": "nl", "term": "vervangen", "translation": "заменять",
    }))

    assert checked["pending_language_check"] is True
    assert checked["language_check_status"] == "pending"


def test_refresh_merges_exact_duplicates_and_preserves_translations(monkeypatch):
    state = [{
        "id": "one", "lang": "nl", "term": "Vervangen.", "ru": "заменять",
    }, {
        "id": "two", "language": "nl", "word": " vervangen ", "translation": "менять",
    }]

    async def check(text, language, **kwargs):
        return _ok(text)

    monkeypatch.setattr(learning_data_quality.language_tool, "check_text_retry", check)
    monkeypatch.setattr(learning_data_quality.store, "get_list", lambda key, cid: list(state) if key == config.DICT_KEY else [])

    def save(key, cid, items):
        if key == config.DICT_KEY:
            state[:] = items

    monkeypatch.setattr(learning_data_quality.store, "set_list", save)
    first = asyncio.run(learning_data_quality.refresh_dictionary("42"))
    after_first = [dict(item) for item in state]
    second = asyncio.run(learning_data_quality.refresh_dictionary("42"))

    assert first["duplicates"] == 1
    assert second["duplicates"] == 0
    assert second["fixed"] == 0
    assert len(state) == 1
    assert state[0]["translation"] == "заменять; менять"
    assert state == after_first


def test_confirmed_review_applies_full_sentence_not_fragment(monkeypatch):
    entries = [{
        "id": "entry-1",
        "lang": "nl",
        "term": "auto kopen",
        "translation": "купить машину",
        "examples": [{"text": "Ik heb een auto gekocht gisteren."}],
        "language_review_required": True,
    }]
    reviews = [{
        "entryId": "entry-1",
        "field": "examples.0.text",
        "original": "Ik heb een auto gekocht gisteren.",
        "suggestion": "Ik heb gisteren een auto gekocht.",
        "reason": "Возможна грамматическая ошибка",
    }]

    def get_list(key, cid):
        return list(reviews) if key == config.LANGUAGE_REVIEW_KEY else list(entries)

    def set_list(key, cid, items):
        if key == config.LANGUAGE_REVIEW_KEY:
            reviews[:] = items
        elif key == config.DICT_KEY:
            entries[:] = items

    monkeypatch.setattr(learning_data_quality.store, "get_list", get_list)
    monkeypatch.setattr(learning_data_quality.store, "set_list", set_list)

    learning_data_quality.resolve_review("42", "apply")

    assert entries[0]["examples"][0]["text"] == "Ik heb gisteren een auto gekocht."
    assert entries[0]["language_review_required"] is False
    assert reviews == []


def test_database_refresh_summary_matches_required_format():
    message = settings_ui.database_refresh_result({
        "checked": 184,
        "fixed": 37,
        "duplicates": 8,
        "review": 5,
        "unchanged": 134,
    })
    assert message.text == (
        "✅ База обновлена\n"
        "Проверено: 184\n"
        "Исправлено: 37\n"
        "Объединено дубликатов: 8\n"
        "Требуют проверки: 5\n"
        "Без изменений: 134"
    )


def test_language_review_has_no_skip_button(monkeypatch):
    monkeypatch.setattr(learning_data_quality, "review_items", lambda _cid: [{
        "entryId": "entry-1",
        "original": "Ik ben opzoek naar een rode kitten voor een klein prijsje",
        "suggestion": "Ik ben op zoek naar een rode kitten voor een klein prijsje",
        "reason": "Проверь написание",
    }])

    class Bot:
        async def send_message(self, **kwargs):
            self.message = kwargs

    bot = Bot()
    asyncio.run(settings.send_language_review(bot, "42"))

    labels = [
        button.text
        for row in bot.message["reply_markup"].inline_keyboard
        for button in row
    ]
    assert labels == ["✅ Заменить", "❌ Удалить запись", "⬅️ Назад", "#️⃣ Главная"]


def test_database_in_order_summary_is_short():
    message = settings_ui.database_refresh_result({
        "checked": 10, "fixed": 0, "duplicates": 0, "review": 0,
    })
    assert message.text == "✅ База в порядке\nВсе записи уже соответствуют актуальному формату."
