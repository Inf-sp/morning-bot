"""Ежедневная словарная практика и выбор материала дня."""

import hashlib
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import store
import learning_dictionary as dictionary
from dictionary_model import (
    CANONICAL_ENTRY_OVERRIDES,
    capitalize_initial,
    normalize_key,
    normalize_term_case,
)
from ui import learning as learning_ui
from ui.constants import delete_label

_code = dictionary._code


def _flag(language):
    return "🇳🇱" if _code(language) == "nl" else "🇬🇧"


_ensure_dict = dictionary._ensure_dict
_dict_lang = dictionary._dict_lang
_entry_term = dictionary._entry_term
_entry_translation = dictionary._entry_translation

def _chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def _entries_review_sorted(pool):
    """Сначала слова, которым уже пора повториться, затем давно показанные."""
    today = datetime.now(config.TZ).date().isoformat()

    def _key(w):
        due = str(w.get("srs_due_at") or "")
        due_now = 0 if not due or due <= today else 1
        return (due_now, due or "9999-12-31", str(w.get("last_shown_at") or ""))

    return sorted(pool, key=_key)


def build_daily_practice(cid, language, *, mark_shown=False):
    """Три стабильные записи дня из личного словаря с примерами и подсказкой."""
    lang_code = _code(language)
    words = _ensure_dict(cid)
    pool = [
        word for word in words
        if _dict_lang(word) == lang_code and _entry_term(word) and _entry_translation(word)
    ]
    if not pool:
        return {"flag": _flag(language), "entries": []}

    ordered = _entries_review_sorted(pool)
    # Записи с готовыми примерами полезнее для карточки дня. Внутри группы
    # недельная/SRS-очередь остаётся стабильной.
    ordered.sort(key=lambda word: 0 if word.get("examples") else 1)
    today = datetime.now(config.TZ).date().isoformat()
    base = int(hashlib.sha256(f"{cid}|{lang_code}".encode()).hexdigest()[:8], 16)
    offset = (base + datetime.now(config.TZ).date().toordinal()) % len(ordered)
    rotated = ordered[offset:] + ordered[:offset]
    chosen = rotated[:3]
    now_iso = datetime.now(config.TZ).isoformat()
    entries = []
    for word in chosen:
        term = capitalize_initial(
            normalize_term_case(_entry_term(word), dictionary._kind_of(_entry_term(word)))
        )
        translation = _entry_translation(word)
        override = CANONICAL_ENTRY_OVERRIDES.get(normalize_key(_entry_term(word)))
        if override:
            term, translation = override
        examples = word.get("examples") or []
        example = examples[0] if examples and isinstance(examples[0], dict) else {}
        entries.append({
            "term": term,
            "translation": translation,
            "example": str(example.get("text") or "").strip(),
            "example_translation": str(example.get("translation") or "").strip(),
            "breakdown": str(word.get("breakdown") or "").strip(),
        })
        if mark_shown:
            try:
                words[words.index(word)]["last_shown_at"] = now_iso
            except ValueError:
                pass
    if mark_shown:
        store.set_list(config.DICT_KEY, cid, words)

    first = entries[0]
    tip = (
        f"Свяжи «{first['term']}» с одной знакомой ситуацией и повтори пример вслух два раза."
    )
    generic = {"слово", "фраза", "выражение"}
    rule = next((
        entry["breakdown"] for entry in entries
        if entry.get("breakdown", "").casefold() not in generic
    ), "")
    return {"flag": _flag(language), "entries": entries, "tip": tip, "rule": rule}


def _build_morning_word(cid, language):
    """Собирает карточку повторения без нового учебного материала."""
    practice = build_daily_practice(cid, language, mark_shown=True)
    msg = learning_ui.morning_words(
        practice["flag"], entries=practice["entries"], tip=practice.get("tip"),
        rule=practice.get("rule"), empty_hint=not practice["entries"],
    )
    return msg, []


async def send_morning_word(bot, cid, language=None, with_kb=True):
    """11:00 — до трех ранее изученных слов и фраз для повторения."""
    import settings
    language = language or settings.study_lang(cid)
    msg, del_row = _build_morning_word(cid, language)
    rows = _chunks(del_row, 3) if with_kb else []
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=InlineKeyboardMarkup(rows) if rows else None,
    )


async def send_daily_practice(bot, cid, reply_markup=None):
    """11:00 — только повторение изученного, без блока «Живой язык»."""
    import settings
    language = settings.study_lang(cid)
    word_msg, _del_row = _build_morning_word(cid, language)
    await bot.send_message(
        chat_id=cid,
        text=word_msg.text,
        entities=word_msg.entities,
        reply_markup=reply_markup,
    )
