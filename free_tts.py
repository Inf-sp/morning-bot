"""Free Dutch speech synthesis for dictionary pronunciation."""

from __future__ import annotations

import html
import io
import re
import time

from gtts import gTTS

import api_usage

LANGUAGE = "nl"
VOICE = "gtts-nl"
RATE = "normal"

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


class FreeTTSError(RuntimeError):
    pass


def clean_spoken_text(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = _HTML_TAG_RE.sub(" ", text)
    text = _MARKDOWN_RE.sub("", text)
    text = _EMOJI_RE.sub("", text)
    text = _CONTROL_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def voice_name() -> str:
    return VOICE


def speech_rate() -> str:
    return RATE


def spoken_text(word: str, example: str = "") -> str:
    word = clean_spoken_text(word)
    example = clean_spoken_text(example)
    if not word:
        raise FreeTTSError("empty_word")
    return f"{word}. {example}".strip() if example else word


def synthesize(word: str, example: str = "") -> bytes:
    text = spoken_text(word, example)
    started = time.monotonic()
    try:
        output = io.BytesIO()
        gTTS(text=text, lang=LANGUAGE, tld="nl", slow=False, timeout=15).write_to_fp(output)
        audio = output.getvalue()
    except Exception as exc:
        api_usage.record_request(
            "gtts", ok=False, error=type(exc).__name__,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise FreeTTSError("synthesis_failed") from exc
    if not audio:
        raise FreeTTSError("empty_audio")
    api_usage.record_request(
        "gtts", ok=True, units={"characters": len(text)},
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    return audio
