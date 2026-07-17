"""Ежедневная словарная практика и выбор материала дня."""

from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import store
import learning_dictionary as dictionary
from dictionary_model import normalize_term_case
from ui import learning as learning_ui
from ui.constants import delete_label

_code = dictionary._code
_flag = lambda language: "🇳🇱" if _code(language) == "nl" else "🇬🇧"
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


def _build_morning_word(cid, language):
    """Собирает карточку повторения без нового учебного материала."""
    lang_code = _code(language)
    flag = _flag(language)
    words = _ensure_dict(cid)
    pool = [w for w in words if _dict_lang(w) == lang_code and _entry_term(w) and _entry_translation(w)]
    review_pool = [w for w in pool if w.get("srs_history") or w.get("status") == "known"]
    if not review_pool:
        msg = learning_ui.morning_words(flag, empty_hint=True)
        return msg, []
    chosen = _entries_review_sorted(review_pool)[:5]
    if not chosen:
        msg = learning_ui.morning_words(flag, empty_hint=True)
        return msg, []

    now_iso = datetime.now(config.TZ).isoformat()
    del_row = []
    lines = []
    for w in chosen:
        term = normalize_term_case(_entry_term(w), dictionary._kind_of(_entry_term(w)))
        ru = _entry_translation(w)
        lines.append((term, ru))
        try:
            idx = words.index(w)
            words[idx]["last_shown_at"] = now_iso
            del_row.append(InlineKeyboardButton(delete_label(term[:20]), callback_data=f"worddel_{idx}"))
        except ValueError:
            pass
    try:
        store.set_list(config.DICT_KEY, cid, words)
    except Exception:
        pass

    msg = learning_ui.morning_words(flag, words=lines)
    return msg, del_row


async def send_morning_word(bot, cid, language=None, with_kb=True):
    """11:00 — до пяти ранее изученных слов и фраз для повторения."""
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


async def send_daily_practice(bot, cid):
    """11:00 — только повторение изученного, без блока «Живой язык»."""
    import settings
    language = settings.study_lang(cid)
    word_msg, _del_row = _build_morning_word(cid, language)
    await bot.send_message(chat_id=cid, text=word_msg.text, entities=word_msg.entities)
