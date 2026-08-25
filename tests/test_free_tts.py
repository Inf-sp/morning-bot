import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import free_tts


def test_free_tts_builds_dutch_text_with_word_and_example(monkeypatch):
    captured = {}

    class FakeGTTS:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def write_to_fp(self, output):
            output.write(b"mp3-audio")

    monkeypatch.setattr(free_tts, "gTTS", FakeGTTS)
    monkeypatch.setattr(free_tts.api_usage, "record_request", lambda *_args, **_kwargs: None)

    audio = free_tts.synthesize("de afspraak", "Ik heb morgen een afspraak.")

    assert audio == b"mp3-audio"
    assert captured["lang"] == "nl"
    assert captured["tld"] == "nl"
    assert captured["text"] == "de afspraak. Ik heb morgen een afspraak."


def test_free_tts_cleans_markup_without_changing_dutch_text():
    assert free_tts.clean_spoken_text("🇳🇱 **de afspraak**") == "de afspraak"
