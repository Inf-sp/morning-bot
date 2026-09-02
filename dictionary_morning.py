"""Ежедневное «Слово дня»: выбор без повторов и глубокий разбор."""

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
    study_card_data,
    study_card_is_complete,
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
    """Стабильный порядок ещё не показанных слов."""

    def _key(w):
        return normalize_key(_entry_term(w))

    return sorted(pool, key=_key)


def _shown_at_sort_key(entry):
    """Старые и повреждённые даты считаются самыми давними, а не ломают рассылку."""
    try:
        return datetime.fromisoformat(
            str(entry.get("daily_word_shown_at") or "")
        ).timestamp()
    except (TypeError, ValueError):
        return float("-inf")


def build_daily_practice(cid, language, *, mark_shown=False):
    """Одно одиночное слово; после полного прохода начинает новый цикл."""
    lang_code = _code(language)
    words = _ensure_dict(cid)
    eligible = [
        word for word in words
        if (
            _dict_lang(word) == lang_code
            and _entry_term(word)
            and _entry_translation(word)
            and len(_entry_term(word).split()) == 1
        )
    ]
    ready = [word for word in eligible if study_card_is_complete(word)]
    # Полная карточка остаётся предпочтительной, но сохранённое слово с
    # переводом всё равно должно попасть в ежедневную рассылку.
    eligible = ready or eligible
    pool = [word for word in eligible if not word.get("daily_word_shown_at")]
    if not pool and eligible:
        pool = [min(eligible, key=_shown_at_sort_key)]
    if not pool:
        return {"flag": _flag(language), "entries": []}

    ordered = _entries_review_sorted(pool)
    day = datetime.now(config.TZ).date().isoformat()
    offset = int(hashlib.sha256(f"{cid}|{lang_code}|{day}".encode()).hexdigest()[:8], 16) % len(ordered)
    chosen = ordered[offset:offset + 1]
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
        entries.append({**word, **study_card_data(word),
            "term": term,
            "translation": translation,
        })
        if mark_shown:
            try:
                words[words.index(word)]["daily_word_shown_at"] = now_iso
            except ValueError:
                pass
    if mark_shown:
        store.set_list(config.DICT_KEY, cid, words)

    return {"flag": _flag(language), "entries": entries}


def _build_morning_word(cid, language):
    """Собирает карточку одного слова и отмечает его показ скрытым полем."""
    practice = build_daily_practice(cid, language, mark_shown=True)
    if not practice["entries"]:
        return learning_ui.morning_words(
            practice["flag"], entries=[], empty_hint=True,
        ), []
    msg = learning_ui.morning_words(
        practice["flag"], entries=practice["entries"], empty_hint=not practice["entries"],
    )
    return msg, []


async def send_morning_word(bot, cid, language=None, with_kb=True):
    """11:00 — одно ранее не показанное слово с глубоким разбором."""
    import settings
    language = language or settings.study_lang(cid)
    msg, del_row = _build_morning_word(cid, language)
    if msg is None:
        return False
    rows = _chunks(del_row, 3) if with_kb else []
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=InlineKeyboardMarkup(rows) if rows else None,
    )
    return True


async def send_daily_practice(bot, cid, reply_markup=None):
    """11:00 — одно слово с глубоким разбором, без блока «Живой язык»."""
    import settings
    language = settings.study_lang(cid)
    word_msg, _del_row = _build_morning_word(cid, language)
    if word_msg is None:
        return False
    await bot.send_message(
        chat_id=cid,
        text=word_msg.text,
        entities=word_msg.entities,
        reply_markup=reply_markup,
    )
    return True
