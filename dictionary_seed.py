"""Мастер начального наполнения учебного словаря."""

from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import store
import learning_dictionary as dictionary
from dictionary_model import language_code as _code
from dictionary_seed_catalog import phrase_catalog, word_catalog
from dictionary_seed_state import SeedStateRepository
from dictionary_seed_ui import (
    LEVEL_LABELS,
    SEED_LEVELS as _SEED_LEVELS,
    render_keyboard as _seed_render_kb,
    render_text as _seed_render_text,
    level_keyboard as _seed_level_keyboard,
)
from ui.navigation import back_menu_keyboard

_DICT_SEED_LIMIT = 30
_dict_item_key = dictionary._dict_item_key
_dict_lang = dictionary._dict_lang
_dict_kind = dictionary._dict_kind
_w_field = dictionary._w_field
_ensure_dict = dictionary._ensure_dict
_cap = dictionary._cap
_refresh_dict_entry = dictionary._refresh_dict_entry
send_dict = dictionary.send_dict
send_dict_lang = dictionary.send_dict_lang

def _seed_dataset(lang, kind):
    if kind == "phrase":
        return phrase_catalog(lang)
    return word_catalog(lang)


def _seed_language(cid, lang=None):
    if lang in ("nl", "en"):
        code = lang
    else:
        import settings as _s
        code = _code(_s.study_lang(cid))
    language = "нидерландский" if code == "nl" else "английский"
    level = store.get_level(cid, language)
    if level not in _SEED_LEVELS:
        level = "medium"
    return code, language, level


def _seed_existing_keys(cid):
    return {
        _dict_item_key(_dict_lang(w), _dict_kind(w), _w_field(w, "word", "nl", "en"))
        for w in _ensure_dict(cid)
    }


def _seed_seen_keys(cid):
    return SeedStateRepository(cid).seen_keys()


def _seed_mark_seen(cid, items):
    keys = [_dict_item_key(item.get("lang"), item.get("kind"), item.get("word"))
            for item in items]
    SeedStateRepository(cid).mark_seen(keys)


def _seed_candidates(cid, lang, level, kind="word"):
    blocked = _seed_existing_keys(cid) | _seed_seen_keys(cid)
    out = []
    for word, ru, note in _seed_dataset(lang, kind).get(level, []):
        item = {"lang": lang, "word": _cap(word), "ru": ru, "kind": kind, "note": note}
        key = _dict_item_key(lang, kind, item["word"])
        if key not in blocked:
            out.append(item)
        if len(out) >= _DICT_SEED_LIMIT:
            break
    return out


def _seed_state_get(cid):
    return SeedStateRepository(cid).get()


def _seed_state_set(cid, st):
    SeedStateRepository(cid).set(st)


def _seed_state_clear(cid):
    SeedStateRepository(cid).clear()


async def send_seed_intro(bot, cid, lang=None):
    code, language, level = _seed_language(cid, lang)
    items = _seed_candidates(cid, code, level, "word")
    if not items:
        await send_dict_lang(bot, cid, code)
        return
    text = (
        "Для эффективного обучения сначала наполним ваш словарь.\n\n"
        f"Я подобрал слова уровня «{LEVEL_LABELS.get(level, level)}». Просмотрите список и отметьте те, "
        "которые хотите добавить."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Добавить свои слова", callback_data=f"a_dictadd_smart_{code}")],
        [InlineKeyboardButton("✨ Подобрать слова", callback_data=f"a_dictseed_start_{code}")],
    ])
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def offer_seed_for_level_change(bot, cid, language, level):
    code = _code(language)
    items = _seed_candidates(cid, code, level, "word")
    if not items:
        return
    level_label = LEVEL_LABELS.get(level, level)
    text = (
        f"📚 Уровень обновлён до «{level_label}»\n\n"
        f"Хотите добавить стартовые слова уровня «{level_label}»?\n"
        "Я покажу список, а вы отметите те, которые хотите добавить."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🆕 Добавить выбранные ({level_label})", callback_data=f"a_dictseed_start_{code}")],
        [InlineKeyboardButton("Позже", callback_data="a_dictseed_later")],
    ])
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def seed_later(bot, cid):
    _seed_state_clear(cid)
    await send_dict(bot, cid)


async def seed_start(bot, cid, lang=None, kind="word", q=None):
    code, _language, level = _seed_language(cid, lang)
    items = _seed_candidates(cid, code, level, kind)
    if not items:
        text = (
            "📚 Словарь уже заполнен\n\n"
            "Для вашего уровня пока нет новых стартовых слов.\n"
            "Можно добавить свои слова вручную или перейти к фразам."
        )
        if q is not None:
            try:
                await q.message.edit_text(
                    text, reply_markup=back_menu_keyboard(f"a_dictlang_{code}"))
                return
            except Exception:
                pass
        await bot.send_message(
            chat_id=cid, text=text,
            reply_markup=back_menu_keyboard(f"a_dictlang_{code}"))
        return
    st = {
        "lang": code,
        "level": level,
        "kind": kind,
        "items": items,
        "selected": [],
        "page": 0,
        "created_at": datetime.now(config.TZ).isoformat(),
        "confirmed": False,
    }
    _seed_state_set(cid, st)
    text = _seed_render_text(st)
    kb = _seed_render_kb(st)
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def seed_toggle(bot, cid, idx, q=None):
    st = _seed_state_get(cid)
    items = st.get("items") or []
    if not (0 <= idx < len(items)):
        return
    selected = set(st.get("selected") or [])
    if idx in selected:
        selected.remove(idx)
    else:
        selected.add(idx)
    st["selected"] = sorted(selected)
    _seed_state_set(cid, st)
    if q is not None:
        try:
            await q.message.edit_text(_seed_render_text(st), reply_markup=_seed_render_kb(st))
        except Exception:
            await bot.send_message(chat_id=cid, text=_seed_render_text(st), reply_markup=_seed_render_kb(st))


async def seed_page(bot, cid, page, q=None):
    st = _seed_state_get(cid)
    if not st:
        return
    st["page"] = max(0, int(page))
    _seed_state_set(cid, st)
    if q is not None:
        try:
            await q.message.edit_text(_seed_render_text(st), reply_markup=_seed_render_kb(st))
        except Exception:
            await bot.send_message(chat_id=cid, text=_seed_render_text(st), reply_markup=_seed_render_kb(st))


def _seed_level_kb(cid, code):
    _l, _language, current = _seed_language(cid, code)
    return _seed_level_keyboard(code, current)


async def seed_choose_level(bot, cid, q=None):
    st = _seed_state_get(cid)
    code = st.get("lang") if st else None
    code = code or _seed_language(cid)[0]
    text = "📶 Выбери уровень слов для добавления."
    kb = _seed_level_kb(cid, code)
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def seed_set_level(bot, cid, lang, level, q=None):
    if level not in _SEED_LEVELS:
        return
    st = _seed_state_get(cid)
    kind = st.get("kind", "word") if st else "word"
    language = "нидерландский" if lang == "nl" else "английский"
    store.set_level(cid, language, level)
    await seed_start(bot, cid, lang, kind=kind, q=q)


async def seed_add_selected(bot, cid, q=None):
    st = _seed_state_get(cid)
    if not st:
        await bot.send_message(
            chat_id=cid, text="Подборка устарела. Открой словарь заново.",
            reply_markup=back_menu_keyboard("m_learn"))
        return
    if st.get("confirmed"):
        await bot.send_message(
            chat_id=cid, text="Эта подборка уже обработана.",
            reply_markup=back_menu_keyboard("m_learn"))
        return
    st["confirmed"] = True
    _seed_state_set(cid, st)
    selected = set(st.get("selected") or [])
    existing = _seed_existing_keys(cid)
    added = []
    for idx, item in enumerate(st.get("items") or []):
        if idx not in selected:
            continue
        key = _dict_item_key(item["lang"], item["kind"], item["word"])
        if key in existing:
            continue
        legacy = {k: item[k] for k in ("lang", "word", "ru", "kind") if item.get(k)}
        store.add_to_list(config.DICT_KEY, cid, legacy)
        existing.add(key)
        added.append(legacy)
    kind = st.get("kind", "word")
    lang = st.get("lang", "en")
    _seed_mark_seen(cid, added)
    _seed_state_clear(cid)
    # Сразу генерируем пример/разбор для тренажёра — та же ленивая миграция,
    # что при первом обращении к старой записи, но выполненная сейчас, а не
    # отложенная до первого показа в тренажёре.
    for legacy in added:
        await _refresh_dict_entry(cid, legacy)
    noun = "фраз" if kind == "phrase" else "слов"
    if added:
        terms = ", ".join(a.get("word", "") for a in added[:10])
        more = f" и ещё {len(added) - 10}" if len(added) > 10 else ""
        text = f"✅ Добавлено {len(added)} {noun}: {terms}{more}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 Начать обучение", callback_data=f"a_train_{lang}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
        ])
    else:
        text = "Ничего не отмечено — словарь не изменился."
        kb = back_menu_keyboard(f"a_dictlang_{lang}")
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
    else:
        await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
