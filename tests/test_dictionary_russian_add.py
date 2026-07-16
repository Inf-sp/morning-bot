import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import dictionary_import
import learning_router


def test_add_word_command_extracts_russian_value(monkeypatch):
    monkeypatch.setattr(dictionary_import, "_active_language_code", lambda _cid: "nl")

    payload, lang = dictionary_import._extract_chat_dict_add(
        "Добавь слово Уверенность", "42"
    )

    assert payload == "Уверенность"
    assert lang == "nl"


def test_russian_value_is_translated_not_transliterated(monkeypatch):
    captured = {}

    async def fake_allm_json(prompt, *_args, **_kwargs):
        captured["prompt"] = prompt
        return {
            "ok": True,
            "lang": "nl",
            "term": "zekerheid",
            "article": "de",
            "translation": "уверенность",
            "breakdown": "существительное, de-слово",
            "examples": [],
            "pos": "существительное",
            "plural": "",
            "forms": [],
            "topic": "характер",
            "difficulty": "B1",
            "construction": "",
            "situation_type": "",
            "alt_translations": [],
            "usage": [],
            "needs_confirmation": False,
            "reason": "",
        }

    monkeypatch.setattr(dictionary_import.ai, "allm_json", fake_allm_json)

    entry = asyncio.run(
        dictionary_import._normalize_dict_entry_full(
            "Уверенность", "nl", source_text="Добавь слово Уверенность"
        )
    )

    assert entry["term"] == "zekerheid"
    assert entry["article"] == "de"
    assert entry["translation"] == "уверенность"
    assert "НИКОГДА не" in captured["prompt"]
    assert "de Uverenheid" in captured["prompt"]


def test_saved_word_actions_include_delete_and_dictionary():
    keyboard = dictionary_import._dict_saved_kb("nl", "zekerheid")

    assert keyboard.inline_keyboard[0][0].callback_data == "a_dictdel_nl_zekerheid"
    assert keyboard.inline_keyboard[1][0].text == "📖 Мой словарь"
    assert keyboard.inline_keyboard[1][0].callback_data == "a_dictlang_nl"
    assert [button.text for row in keyboard.inline_keyboard for button in row] == [
        "❌ Удалить", "📖 Мой словарь", "⬅️ Назад", "#️⃣ Меню",
    ]


def test_duplicate_word_actions_include_dictionary():
    keyboard = dictionary_import._dict_duplicate_kb("en", "confidence")

    assert keyboard.inline_keyboard[0][0].callback_data == "a_dictdel_en_confidence"
    assert keyboard.inline_keyboard[1][0].text == "📖 Мой словарь"
    assert keyboard.inline_keyboard[1][0].callback_data == "a_dictlang_en"
    assert [button.text for row in keyboard.inline_keyboard for button in row] == [
        "❌ Удалить", "📖 Мой словарь", "⬅️ Назад", "#️⃣ Меню",
    ]


def test_done_removes_buttons_but_keeps_saved_word_card():
    edits = []
    cid = "dictionary-done-user"

    class Query:
        message = SimpleNamespace(message_id=77)

        async def edit_message_reply_markup(self, **kwargs):
            edits.append(kwargs)

    dictionary_import.store.last_inline_message[cid] = 77

    handled = asyncio.run(
        learning_router.handle_action(
            SimpleNamespace(),
            cid,
            Query(),
            "dictdone",
            lambda _action: None,
        )
    )

    assert handled is True
    assert edits == [{"reply_markup": None}]
    assert cid not in dictionary_import.store.last_inline_message
