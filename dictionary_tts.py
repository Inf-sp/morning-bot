"""Telegram delivery and persistent file_id cache for dictionary TTS."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import datetime

import config
import free_tts
import secure
import storage_driver
import store
from dictionary_model import entry_language, entry_term
from ui.navigation import back_menu_keyboard

_log = logging.getLogger(__name__)
_locks: dict[str, asyncio.Lock] = {}


def _entry_example_nl(entry: dict) -> str:
    direct = str(entry.get("example_nl") or "").strip()
    if direct:
        return direct
    for example in entry.get("examples") or []:
        if isinstance(example, dict):
            text = str(example.get("text") or example.get("nl") or "").strip()
            if text:
                return text
    return ""


def spoken_payload(entry: dict) -> tuple[str, str]:
    """Return only the Dutch term/phrase and Dutch example; never translation."""
    word = str(entry_term(entry) or "").strip()
    article = str(entry.get("article") or "").strip()
    if article and not word.casefold().startswith(article.casefold() + " "):
        word = f"{article} {word}".strip()
    return free_tts.clean_spoken_text(word), free_tts.clean_spoken_text(_entry_example_nl(entry))


def make_cache_key(word: str, example: str) -> str:
    return "|".join((
        free_tts.LANGUAGE,
        free_tts.voice_name(),
        free_tts.speech_rate(),
        word.casefold(),
        example,
    ))


def _find_entry(cid, word_id: str) -> dict | None:
    for entry in store.ensure_list_ids(config.DICT_KEY, cid):
        if isinstance(entry, dict) and entry.get("id") == word_id:
            return entry
    return None


def _get_cached_file_id(cache_key: str) -> str:
    record = (storage_driver.load(config.TTS_CACHE_KEY) or {}).get(cache_key) or {}
    return str(record.get("telegramFileId") or "")


def _save_cached_file_id(cache_key: str, word: str, example: str, file_id: str) -> None:
    record = {
        "cacheKey": cache_key,
        "language": free_tts.LANGUAGE,
        "voice": free_tts.voice_name(),
        "rate": free_tts.speech_rate(),
        "word": word.casefold(),
        "example": example,
        "telegramFileId": file_id,
        "createdAt": datetime.now(config.TZ).isoformat(),
    }

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[cache_key] = record
        return data, None

    storage_driver.mutate(config.TTS_CACHE_KEY, mutate)


def _safe_log(error: Exception, word_id: str) -> None:
    status = None
    code = str(error) if isinstance(error, free_tts.FreeTTSError) else type(error).__name__
    payload = {
        "provider": "gtts",
        "status": status,
        "wordId": str(word_id),
        "error": secure.redact(code),
    }
    _log.warning("%s", json.dumps(payload, ensure_ascii=False))


async def _send_error(bot, cid) -> None:
    await bot.send_message(
        chat_id=cid,
        text="Не удалось загрузить произношение. Попробуй ещё раз.",
        reply_markup=back_menu_keyboard("m_learn"),
        preserve_previous_inline=True,
    )


async def send_pronunciation(bot, cid, word_id: str) -> None:
    entry = _find_entry(cid, word_id)
    if not entry or entry_language(entry) != "nl":
        await _send_error(bot, cid)
        return
    word, example = spoken_payload(entry)
    if not word:
        await _send_error(bot, cid)
        return
    cache_key = make_cache_key(word, example)

    cached_file_id = _get_cached_file_id(cache_key)
    if cached_file_id:
        try:
            await bot.send_voice(chat_id=cid, voice=cached_file_id)
        except Exception as error:
            _safe_log(error, word_id)
            await _send_error(bot, cid)
        return

    lock = _locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        cached_file_id = _get_cached_file_id(cache_key)
        if cached_file_id:
            try:
                await bot.send_voice(chat_id=cid, voice=cached_file_id)
            except Exception as error:
                _safe_log(error, word_id)
                await _send_error(bot, cid)
            return
        try:
            audio = await asyncio.to_thread(free_tts.synthesize, word, example)
            voice_file = io.BytesIO(audio)
            voice_file.name = "pronunciation.mp3"
            message = await bot.send_voice(chat_id=cid, voice=voice_file)
            file_id = str(getattr(getattr(message, "voice", None), "file_id", "") or "")
            if file_id:
                _save_cached_file_id(cache_key, word, example, file_id)
        except Exception as error:
            _safe_log(error, word_id)
            await _send_error(bot, cid)
