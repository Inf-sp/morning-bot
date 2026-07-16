import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import dictionary_tts


class VoiceBot:
    def __init__(self):
        self.voices = []
        self.messages = []
        self.message_calls = []

    async def send_voice(self, **kwargs):
        self.voices.append(kwargs["voice"])
        return SimpleNamespace(voice=SimpleNamespace(file_id="telegram-file-id"))

    async def send_message(self, **kwargs):
        self.messages.append(kwargs["text"])
        self.message_calls.append(kwargs)


def _entry(example=True):
    item = {
        "id": "word-id",
        "lang": "nl",
        "term": "afspraak",
        "article": "de",
        "translation": "договорённость",
        "examples": [],
    }
    if example:
        item["examples"] = [{
            "text": "Ik heb morgen een afspraak.",
            "translation": "У меня завтра встреча.",
        }]
    return item


def test_spoken_payload_includes_article_and_never_translation():
    word, example = dictionary_tts.spoken_payload(_entry())
    assert word == "de afspraak"
    assert example == "Ik heb morgen een afspraak."
    assert "договорённость" not in word + example
    assert "завтра" not in word + example


def test_first_press_uploads_voice_and_second_uses_file_id(monkeypatch):
    bot = VoiceBot()
    cache = {}
    synth_calls = []
    dictionary_tts._locks.clear()
    monkeypatch.setattr(dictionary_tts, "_find_entry", lambda cid, word_id: _entry())
    monkeypatch.setattr(dictionary_tts, "_get_cached_file_id", lambda key: cache.get(key, ""))

    def save(key, word, example, file_id):
        cache[key] = file_id

    def synthesize(word, example):
        synth_calls.append((word, example))
        return b"mp3"

    monkeypatch.setattr(dictionary_tts, "_save_cached_file_id", save)
    monkeypatch.setattr(dictionary_tts.azure_speech, "synthesize", synthesize)

    asyncio.run(dictionary_tts.send_pronunciation(bot, "42", "word-id"))
    asyncio.run(dictionary_tts.send_pronunciation(bot, "42", "word-id"))

    assert synth_calls == [("de afspraak", "Ik heb morgen een afspraak.")]
    assert hasattr(bot.voices[0], "read")
    assert bot.voices[0].name == "pronunciation.mp3"
    assert bot.voices[1] == "telegram-file-id"
    assert bot.messages == []


def test_cache_record_has_provider_inputs_and_telegram_file_id(monkeypatch):
    captured = {}

    def mutate(key, mutator):
        data, result = mutator({})
        captured["key"] = key
        captured["data"] = data
        return result

    monkeypatch.setattr(dictionary_tts.storage_driver, "mutate", mutate)
    monkeypatch.setattr(dictionary_tts.config, "TTS_CACHE_KEY", "tts_cache.json")
    cache_key = dictionary_tts.make_cache_key(
        "vervangen", "Ik wil mijn oude telefoon vervangen.",
    )
    dictionary_tts._save_cached_file_id(
        cache_key,
        "vervangen",
        "Ik wil mijn oude telefoon vervangen.",
        "AwACAg-test",
    )

    record = captured["data"][cache_key]
    assert captured["key"] == "tts_cache.json"
    assert record["cacheKey"] == cache_key
    assert record["language"] == "nl-NL"
    assert record["voice"] == "nl-NL-MaartenNeural"
    assert record["rate"] == "-10%"
    assert record["word"] == "vervangen"
    assert record["example"] == "Ik wil mijn oude telefoon vervangen."
    assert record["telegramFileId"] == "AwACAg-test"
    assert record["createdAt"]


def test_word_without_example_is_still_spoken(monkeypatch):
    bot = VoiceBot()
    calls = []
    dictionary_tts._locks.clear()
    monkeypatch.setattr(dictionary_tts, "_find_entry", lambda cid, word_id: _entry(False))
    monkeypatch.setattr(dictionary_tts, "_get_cached_file_id", lambda key: "")
    monkeypatch.setattr(dictionary_tts, "_save_cached_file_id", lambda *args: None)
    monkeypatch.setattr(
        dictionary_tts.azure_speech,
        "synthesize",
        lambda word, example: calls.append((word, example)) or b"mp3",
    )

    asyncio.run(dictionary_tts.send_pronunciation(bot, "42", "word-id"))

    assert calls == [("de afspraak", "")]
    assert len(bot.voices) == 1


def test_azure_error_keeps_card_and_sends_short_message(monkeypatch):
    bot = VoiceBot()
    dictionary_tts._locks.clear()
    monkeypatch.setattr(dictionary_tts, "_find_entry", lambda cid, word_id: _entry())
    monkeypatch.setattr(dictionary_tts, "_get_cached_file_id", lambda key: "")

    def fail(*args):
        raise dictionary_tts.azure_speech.AzureSpeechError("invalid_key", status=401)

    monkeypatch.setattr(dictionary_tts.azure_speech, "synthesize", fail)
    asyncio.run(dictionary_tts.send_pronunciation(bot, "42", "word-id"))

    assert bot.voices == []
    assert bot.messages == ["Не удалось загрузить произношение. Попробуй ещё раз."]
    assert bot.message_calls[0]["preserve_previous_inline"] is True
