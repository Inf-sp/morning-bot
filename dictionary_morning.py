"""Ежедневная словарная практика и выбор материала дня."""

from datetime import datetime
import random as _r

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import store
import learning_dictionary as dictionary
from live_language import _generate_proverb
from ui import learning as learning_ui
from ui.constants import delete_label

_code = dictionary._code
_flag = lambda language: "🇳🇱" if _code(language) == "nl" else "🇬🇧"
_cap = dictionary._cap
_ensure_dict = dictionary._ensure_dict
_dict_lang = dictionary._dict_lang
_entry_term = dictionary._entry_term
_entry_translation = dictionary._entry_translation

WEEK_TRACK = {
    0: ("Свежая кровь", "Загрузка",
        "Прочитай вслух, покрути в голове. Больше ничего."),
    1: ("Первый повтор", "Эффект генерации",
        "Повтори вчерашнее. Посмотри на русский - вспомни перевод. Придумай ОДНО смешное предложение."),
    2: ("День разгрузки", "Микро-доза",
        "Повтори только фразы за понедельник. Слова не трогай. Есть силы - добавь 2 новых слова."),
    3: ("Проверка боем", "Активное вспоминание",
        "Повторяем всё за Пн и Ср. Закрой перевод рукой, вспоминай. Ошибся - отметь крестиком."),
    4: ("Финал недели", "Зачистка хвостов",
        "Повтори только слова, где вчера были крестики. Короткий спринт."),
    5: ("Легальный отдых", "Полный оффлайн",
        "Никакой учёбы. Мозгу нужен чистый отдых для переноса в долговременную память."),
    6: ("Легальный отдых", "Полный оффлайн",
        "Никакой учёбы. Дай мозгу отдохнуть - это часть процесса."),
}

def _chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def _morning_method_line(method, entries):
    if not entries:
        return "В словаре пока нет записей на этом языке. Сегодня можно добавить что-то через словарь."
    return method


def _entries_priority_sorted(pool):
    """Сортировка по приоритету: сначала никогда не показанные, потом давно
    показанные, потом невыученные — используется и для утренней подборки."""
    def _key(w):
        shown = w.get("last_shown_at")
        never_shown = 0 if not shown else 1
        not_known = 0 if w.get("status") != "known" else 1
        return (never_shown, not_known, shown or "")
    return sorted(pool, key=_key)


def _build_morning_word(cid, language):
    """Собирает карточку слова дня (без отправки) -> (MessageSpec, del_row[InlineKeyboardButton])."""
    import random as _r
    from datetime import datetime
    lang_code = _code(language)
    flag = _flag(language)
    wd = datetime.now(config.TZ).weekday()
    _title, _phase, method = WEEK_TRACK[wd]
    words = _ensure_dict(cid)
    pool = [w for w in words if _dict_lang(w) == lang_code and _entry_term(w) and _entry_translation(w)]
    if wd >= 5 or not pool:
        msg = learning_ui.morning_words(flag, method, is_read_aloud=method.startswith("Прочитай вслух"), empty_hint=True)
        return msg, []
    method = _morning_method_line(method, pool)
    ranked = _entries_priority_sorted(pool)
    top_n = ranked[:max(5, len(ranked) // 2)]
    chosen = _r.sample(top_n, min(5, len(top_n)))
    if not chosen:
        msg = learning_ui.morning_words(flag, method, is_read_aloud=method.startswith("Прочитай вслух"))
        return msg, []

    now_iso = datetime.now(config.TZ).isoformat()
    del_row = []
    lines = []
    for w in chosen:
        term = _cap(_entry_term(w))
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

    msg = learning_ui.morning_words(flag, method, is_read_aloud=method.startswith("Прочитай вслух"), words=lines)
    return msg, del_row


async def send_morning_word(bot, cid, language=None, with_kb=True):
    """11:00 - Daily Words: метод дня недели + порция из 5 записей словаря,
    без деления на слова и фразы — приоритет давно не показанным."""
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
    """11:00 - "Практика языка": слово дня и живая фраза активного языка одним сообщением."""
    import settings
    from ui.builder import MessageBuilder
    language = settings.study_lang(cid)
    word_msg, _del_row = _build_morning_word(cid, language)
    proverb_data = await _generate_proverb(language)
    proverb_msg = learning_ui.proverb_card(
        _flag(language), proverb_data["original"], proverb_data["analogs"],
        _cap(proverb_data["meaning"]), proverb_data["example"], proverb_data["example_ru"],
    )
    combined = MessageBuilder()
    combined.embed(word_msg)
    combined.embed(proverb_msg)
    msg = combined.build_stripped()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
