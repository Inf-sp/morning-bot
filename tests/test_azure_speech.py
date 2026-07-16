import os
from types import SimpleNamespace

import pytest
import requests

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import azure_speech
import secure


def _configure(monkeypatch):
    monkeypatch.setattr(azure_speech.config, "AZURE_SPEECH_KEY", "azure-secret-key")
    monkeypatch.setattr(azure_speech.config, "AZURE_SPEECH_REGION", "westeurope")
    monkeypatch.setattr(azure_speech.config, "AZURE_SPEECH_VOICE", "nl-NL-MaartenNeural")
    monkeypatch.setattr(azure_speech.config, "AZURE_SPEECH_RATE", "-10%")
    monkeypatch.setattr(azure_speech.api_usage, "record_request", lambda *args, **kwargs: None)


def test_escape_xml_covers_all_sensitive_characters():
    assert azure_speech.escape_xml('&<>"\'') == "&amp;&lt;&gt;&quot;&apos;"


def test_synthesize_uses_ssml_endpoint_headers_pause_and_timeout(monkeypatch):
    _configure(monkeypatch)
    request = {}

    def fake_post(url, **kwargs):
        request.update(url=url, **kwargs)
        return SimpleNamespace(status_code=200, content=b"mp3-bytes")

    monkeypatch.setattr(azure_speech.requests, "post", fake_post)

    result = azure_speech.synthesize(
        "Vervangen", "Ik wil mijn oude telefoon vervangen.",
    )

    assert result == b"mp3-bytes"
    assert request["url"] == "https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1"
    assert request["timeout"] == 15
    assert request["headers"] == {
        "Ocp-Apim-Subscription-Key": "azure-secret-key",
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
        "User-Agent": "DM-Telegram-Bot",
    }
    ssml = request["data"].decode("utf-8")
    assert '<voice name="nl-NL-MaartenNeural">' in ssml
    assert '<prosody rate="-10%">Vervangen.<break time="700ms"/>' in ssml
    assert "Ik wil mijn oude telefoon vervangen." in ssml


def test_word_without_example_has_no_break(monkeypatch):
    _configure(monkeypatch)
    ssml = azure_speech.build_ssml("de afspraak", "")
    assert "De afspraak." in ssml
    assert "<break" not in ssml


def test_timeout_is_mapped_without_exposing_key(monkeypatch):
    _configure(monkeypatch)

    def fake_post(*args, **kwargs):
        raise requests.Timeout("azure-secret-key")

    monkeypatch.setattr(azure_speech.requests, "post", fake_post)
    with pytest.raises(azure_speech.AzureSpeechError) as exc:
        azure_speech.synthesize("vervangen")
    assert exc.value.code == "timeout"
    assert "azure-secret-key" not in str(exc.value)


def test_azure_key_is_redacted(monkeypatch):
    monkeypatch.setattr(azure_speech.config, "AZURE_SPEECH_KEY", "azure-speech-secret-key-123")
    assert "azure-speech-secret-key-123" not in secure.redact(
        "key=azure-speech-secret-key-123",
    )


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "invalid_key"), (403, "access_or_region"), (429, "quota_or_rate_limit"), (503, "azure_unavailable")],
)
def test_http_errors_have_safe_specific_codes(monkeypatch, status, code):
    _configure(monkeypatch)
    monkeypatch.setattr(
        azure_speech.requests,
        "post",
        lambda *args, **kwargs: SimpleNamespace(status_code=status, content=b"secret response"),
    )
    with pytest.raises(azure_speech.AzureSpeechError) as exc:
        azure_speech.synthesize("vervangen")
    assert exc.value.status == status
    assert exc.value.code == code
    assert "secret response" not in str(exc.value)
