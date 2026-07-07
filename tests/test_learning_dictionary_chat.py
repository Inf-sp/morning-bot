import asyncio
import os

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")

import config
import learning


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


@pytest.fixture(autouse=True)
def isolated_dict_store(monkeypatch):
    mem = {}

    def load(key):
        return mem.get(key, {})

    def save(key, data):
        mem[key] = data

    monkeypatch.setattr(learning.store, "_load", load)
    monkeypatch.setattr(learning.store, "_save", save)
    learning.store.dict_pending_add.clear()
    learning.store.pending_input.clear()
    return mem


def test_extracts_natural_dictionary_commands():
    assert learning._extract_chat_dict_add("добавь в словарь de kater") == ("de kater", "nl")
    assert learning._extract_chat_dict_add("запомни слово bijzonder") == ("bijzonder", "nl")
    assert learning._extract_chat_dict_add("добавь английское слово figure out") == ("figure out", "en")
    assert learning._extract_chat_dict_add("добавь в обучение: Ik heb er zin in") == ("Ik heb er zin in", "nl")
    assert learning._extract_chat_dict_add("сохрани эту фразу в нидерландский") == ("", "nl")


def test_chat_add_saves_normalized_entry_with_metadata(monkeypatch, isolated_dict_store):
    async def normalize(payload, lang_hint="nl", source_text=""):
        return {
            "lang": "nl",
            "entry_type": "word",
            "kind": "word",
            "word": "de kater",
            "base_form": "de kater",
            "ru": "похмелье",
            "source_text": source_text,
            "added_at": "2026-07-08T12:00:00+02:00",
            "needs_confirmation": False,
        }

    monkeypatch.setattr(learning, "_normalize_chat_dict_entry", normalize)
    bot = FakeBot()

    handled = asyncio.run(learning.try_add_dict_from_chat(bot, "1", "добавь в словарь de kater"))

    assert handled is True
    saved = isolated_dict_store[config.DICT_KEY]["1"][0]
    assert saved["lang"] == "nl"
    assert saved["kind"] == "word"
    assert saved["entry_type"] == "word"
    assert saved["word"] == "de kater"
    assert saved["base_form"] == "de kater"
    assert saved["ru"] == "похмелье"
    assert saved["source_text"] == "добавь в словарь de kater"
    assert saved["added_at"]
    assert "📚 Добавлено в нидерландский словарь" in bot.messages[0]["text"]
    assert "de kater — похмелье" in bot.messages[0]["text"]
    assert "Появится в тренировках по нидерландскому." in bot.messages[0]["text"]


def test_chat_add_duplicate_does_not_create_second_entry(monkeypatch, isolated_dict_store):
    isolated_dict_store[config.DICT_KEY] = {
        "1": [{"lang": "nl", "kind": "word", "entry_type": "word", "word": "de kater", "ru": "похмелье"}]
    }

    async def normalize(payload, lang_hint="nl", source_text=""):
        return {
            "lang": "nl",
            "entry_type": "word",
            "kind": "word",
            "word": "de kater",
            "base_form": "de kater",
            "ru": "похмелье",
            "source_text": source_text,
            "added_at": "2026-07-08T12:00:00+02:00",
            "needs_confirmation": False,
        }

    monkeypatch.setattr(learning, "_normalize_chat_dict_entry", normalize)
    bot = FakeBot()

    asyncio.run(learning.try_add_dict_from_chat(bot, "1", "добавь kater"))

    assert len(isolated_dict_store[config.DICT_KEY]["1"]) == 1
    assert "📚 Уже есть в нидерландском словаре" in bot.messages[0]["text"]
    assert "Это слово уже используется в тренировках." in bot.messages[0]["text"]


def test_chat_add_ambiguous_entry_requires_confirmation(monkeypatch, isolated_dict_store):
    async def normalize(payload, lang_hint="nl", source_text=""):
        return {
            "lang": "nl",
            "entry_type": "word",
            "kind": "word",
            "word": "de kater",
            "base_form": "de kater",
            "ru": "похмелье",
            "source_text": source_text,
            "added_at": "2026-07-08T12:00:00+02:00",
            "needs_confirmation": True,
        }

    monkeypatch.setattr(learning, "_normalize_chat_dict_entry", normalize)
    bot = FakeBot()

    asyncio.run(learning.try_add_dict_from_chat(bot, "1", "добавь kater"))

    assert config.DICT_KEY not in isolated_dict_store
    assert learning.store.dict_pending_add["1"]["word"] == "de kater"
    assert "Ты имеешь в виду de kater — похмелье?" in bot.messages[0]["text"]

    asyncio.run(learning.confirm_pending_dict_add(bot, "1"))

    assert isolated_dict_store[config.DICT_KEY]["1"][0]["word"] == "de kater"
    assert "📚 Добавлено в нидерландский словарь" in bot.messages[1]["text"]
