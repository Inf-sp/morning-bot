"""Azure Speech REST client for Dutch dictionary pronunciation."""

from __future__ import annotations

import html
import re
import time

import requests

import api_usage
import config

LANGUAGE = "nl-NL"
DEFAULT_VOICE = "nl-NL-MaartenNeural"
DEFAULT_RATE = "-10%"
OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
TIMEOUT_SECONDS = 15

_REGION_RE = re.compile(r"^[A-Za-z0-9-]+$")
_VOICE_RE = re.compile(r"^[A-Za-z0-9-]+$")
_RATE_RE = re.compile(r"^[+-]?\d{1,3}%$")
_HTML_TAG_RE = re.compile(r"<[^>]*>")
_MARKDOWN_RE = re.compile(r"[*_~`]+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]+",
    flags=re.UNICODE,
)


class AzureSpeechError(RuntimeError):
    """Safe, key-free error raised by the Azure Speech client."""

    def __init__(self, code: str, *, status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(code)


def voice_name() -> str:
    value = str(config.AZURE_SPEECH_VOICE or "").strip()
    return value if _VOICE_RE.fullmatch(value) else DEFAULT_VOICE


def speech_rate() -> str:
    value = str(config.AZURE_SPEECH_RATE or "").strip()
    return value if _RATE_RE.fullmatch(value) else DEFAULT_RATE


def escape_xml(value: str) -> str:
    """Escape all XML-sensitive characters, including both quote types."""
    return html.escape(str(value or ""), quote=True).replace("&#x27;", "&apos;")


def clean_spoken_text(value: str) -> str:
    """Remove presentation markup without changing the lexical content."""
    text = html.unescape(str(value or ""))
    text = _HTML_TAG_RE.sub(" ", text)
    text = _MARKDOWN_RE.sub("", text)
    text = _EMOJI_RE.sub("", text)
    text = _CONTROL_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _sentence(value: str) -> str:
    value = clean_spoken_text(value)
    if value:
        value = value[:1].upper() + value[1:]
    if value and value[-1] not in ".!?…":
        value += "."
    return value


def build_ssml(word: str, example: str = "") -> str:
    spoken_word = _sentence(word)
    spoken_example = _sentence(example)
    if not spoken_word:
        raise AzureSpeechError("empty_word")
    body = escape_xml(spoken_word)
    if spoken_example:
        body += '<break time="700ms"/>' + escape_xml(spoken_example)
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="nl-NL">'
        f'<voice name="{escape_xml(voice_name())}">'
        f'<prosody rate="{escape_xml(speech_rate())}">{body}</prosody>'
        "</voice></speak>"
    )


def _error_code(status: int) -> str:
    if status == 401:
        return "invalid_key"
    if status == 403:
        return "access_or_region"
    if status == 429:
        return "quota_or_rate_limit"
    if status >= 500:
        return "azure_unavailable"
    return "azure_http_error"


def synthesize(word: str, example: str = "") -> bytes:
    key = str(config.AZURE_SPEECH_KEY or "").strip()
    region = str(config.AZURE_SPEECH_REGION or "").strip()
    if not key:
        raise AzureSpeechError("missing_key")
    if not region or not _REGION_RE.fullmatch(region):
        raise AzureSpeechError("invalid_region")

    endpoint = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
        "User-Agent": "DM-Telegram-Bot",
    }
    started = time.monotonic()
    try:
        response = requests.post(
            endpoint,
            headers=headers,
            data=build_ssml(word, example).encode("utf-8"),
            timeout=TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        api_usage.record_request(
            "azure_speech", ok=False, error="timeout",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise AzureSpeechError("timeout") from exc
    except requests.RequestException as exc:
        api_usage.record_request(
            "azure_speech", ok=False, error="network_error",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise AzureSpeechError("network_error") from exc

    latency_ms = int((time.monotonic() - started) * 1000)
    if response.status_code != 200:
        code = _error_code(response.status_code)
        api_usage.record_request(
            "azure_speech", ok=False, status_code=response.status_code,
            error=code, latency_ms=latency_ms,
        )
        raise AzureSpeechError(code, status=response.status_code)
    if not response.content:
        api_usage.record_request(
            "azure_speech", ok=False, status_code=200,
            error="empty_audio", latency_ms=latency_ms,
        )
        raise AzureSpeechError("empty_audio", status=200)

    api_usage.record_request(
        "azure_speech", ok=True, status_code=200, latency_ms=latency_ms,
    )
    return response.content
