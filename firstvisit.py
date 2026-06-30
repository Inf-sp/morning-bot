"""Первый вход в раздел: быстрый опрос для заполнения профиля пользователя."""
import re
import store
import ai
import config
import secure
from util import esc
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

_SKIP_KB = {
    s: InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить", callback_data=f"fv_skip_{s}")]])
    for s in ("wardrobe", "learn", "leisure", "balance")
}

_PROMPTS = {
    "wardrobe": (
        "👕 <b>Настроим гардероб</b>\n\n"
        "Напиши в свободном виде:\n"
        "• Твой стиль одежды (минимализм, casual, streetwear…)\n"
        "• Любимые вещи или бренды\n"
        "• Размеры: одежда, обувь, брюки\n\n"
        "<i>Пример: Люблю минимализм и оверсайз. Uniqlo, Nike. "
        "Размер M, обувь EU 43, брюки W32 L32</i>"
    ),
    "learn": (
        "📚 <b>Настроим обучение</b>\n\n"
        "Какие языки изучаешь и какой у тебя уровень?\n\n"
        "<i>Пример: нидерландский A2, английский B1</i>"
    ),
    "leisure": (
        "🍿 <b>Расскажи о своих предпочтениях</b>\n\n"
        "Напиши в любом виде:\n"
        "• Любимые фильмы и сериалы\n"
        "• Любимые исполнители\n"
        "• Любимые книги\n\n"
        "<i>Пример:\n"
        "Фильмы: Паразиты, Эйфория, Настоящий детектив\n"
        "Музыка: The xx, Massive Attack, Portishead\n"
        "Книги: Дюна, Мастер и Маргарита</i>"
    ),
    "balance": (
        "🧠 <b>Немного о тебе</b>\n\n"
        "Расскажи о предпочтениях в еде и здоровье:\n"
        "• Диета или ограничения (без мяса, без глютена…)\n"
        "• Цели (энергия, здоровый вес, лучший сон…)\n"
        "• Что любишь или не ешь\n\n"
        "<i>Пример: не ем мясо, хочу больше энергии, "
        "люблю азиатскую кухню, аллергия на орехи</i>"
    ),
}

_SECTION_KEY = {
    "wardrobe": "m_wardrobe",
    "learn":    "m_learn",
    "leisure":  "m_leisure",
    "balance":  "m_balance",
}


def _mark(cid, section):
    prof = store.get_profile(cid)
    prof[f"_fv_{section}"] = True
    store.set_profile(cid, prof)


def needs_setup(cid, section: str) -> bool:
    """True — раздел не настроен и опрос ещё не проходили."""
    prof = store.get_profile(cid)
    if prof.get(f"_fv_{section}"):
        return False
    if section == "wardrobe":
        import settings as _s
        w = store.load_wardrobe(cid)
        has_wardrobe = bool(store.wardrobe_to_text(w).strip())
        has_style = bool(_s.get(cid, "style") or _s.get(cid, "body"))
        if has_wardrobe or has_style:
            _mark(cid, section)  # уже заполнен — помечаем, чтобы не проверять снова
            return False
        return True
    if section == "learn":
        import settings as _s
        has_lang = bool(_s.get(cid, "study_lang"))
        has_level = bool(store.get_level(cid, "нидерландский") or store.get_level(cid, "английский"))
        if has_lang or has_level:
            _mark(cid, section)
            return False
        return True
    if section == "leisure":
        has_any = (
            store.get_list(config.ARTISTS_KEY, cid)
            or store.get_list(config.WATCHLIST_KEY, cid)
            or store.get_list(config.BOOKS_KEY, cid)
        )
        if has_any:
            _mark(cid, section)
            return False
        return True
    if section == "balance":
        if prof.get("diet_prefs"):
            _mark(cid, section)
            return False
        return True
    return False


async def show_prompt(bot, cid, section: str):
    store.pending_input[str(cid)] = f"firstvisit_{section}"
    await bot.send_message(
        chat_id=cid,
        text=_PROMPTS[section],
        parse_mode="HTML",
        reply_markup=_SKIP_KB[section],
    )


async def skip(bot, cid, section: str):
    """Пользователь нажал «Пропустить» — помечаем и показываем раздел."""
    _mark(cid, section)
    store.pending_input.pop(str(cid), None)
    await _show_section(bot, cid, section)


async def handle_response(bot, cid, section: str, text: str):
    store.pending_input.pop(str(cid), None)
    _mark(cid, section)
    raw = secure.clamp(text)

    if section == "wardrobe":
        saved = await _save_wardrobe(cid, raw)
    elif section == "learn":
        saved = await _save_learn(cid, raw)
    elif section == "leisure":
        saved = await _save_leisure(cid, raw)
    elif section == "balance":
        saved = await _save_balance(cid, raw)
    else:
        saved = []

    if saved:
        lines = "\n".join(f"• {esc(s)}" for s in saved)
        await bot.send_message(
            chat_id=cid,
            text=f"✅ <b>Сохранено</b>\n\n{lines}",
            parse_mode="HTML",
        )
    await _show_section(bot, cid, section)


# ---------- сохранение по разделам ----------

async def _save_wardrobe(cid, raw: str) -> list:
    import settings as _s
    saved = []
    try:
        d = await ai.allm_json(
            f"Пользователь описал гардероб: {secure.wrap_untrusted(raw, 'гардероб')}\n"
            "Извлеки в JSON:\n"
            '{"style":"стиль одной фразой или пусто","body":"параметры тела одной строкой или пусто",'
            '"items":{"tops":["..."],"bottoms":["..."],"shoes":["..."],"outerwear":["..."]}}',
            600, tier="cheap", module="firstvisit",
        )
    except Exception:
        d = {}
    if d.get("style"):
        _s.set_(cid, "style", str(d["style"])[:120])
        saved.append(f"Стиль: {d['style']}")
    if d.get("body"):
        _s.set_(cid, "body", str(d["body"])[:200])
        saved.append(f"Параметры: {d['body']}")
    items = d.get("items") or {}
    if isinstance(items, dict):
        added = store.merge_wardrobe(
            {k: [str(v) for v in lst] for k, lst in items.items() if isinstance(lst, list)},
            cid,
        )
        if added:
            saved.append(f"Вещей в шкафу: {added}")
    if not saved:
        _s.set_(cid, "style", raw[:120])
        saved.append("Стиль сохранён")
    return saved


async def _save_learn(cid, raw: str) -> list:
    import settings as _s
    saved = []
    # Парсим "нидерландский A2, английский B1" и т.п.
    lang_map = {"нидерландский": "нидерландский", "nl": "нидерландский", "dutch": "нидерландский",
                "английский": "английский", "en": "английский", "english": "английский"}
    levels = {"a1", "a2", "b1", "b2", "c1", "c2"}
    text_low = raw.lower()
    detected_lang = None
    for alias, canonical in lang_map.items():
        if alias in text_low:
            detected_lang = canonical
            break
    level_found = next((lv.upper() for lv in levels if lv in text_low), None)
    if detected_lang:
        _s.set_(cid, "study_lang", detected_lang)
        saved.append(f"Язык: {detected_lang}")
    if detected_lang and level_found:
        store.set_level(cid, detected_lang, level_found)
        saved.append(f"Уровень: {level_found}")
    if not saved:
        # Не смогли распознать — сохраняем первый найденный язык как нидерландский
        _s.set_(cid, "study_lang", "нидерландский")
        saved.append("Язык обучения: нидерландский (по умолчанию)")
    return saved


async def _save_leisure(cid, raw: str) -> list:
    saved = []
    try:
        d = await ai.allm_json(
            f"Пользователь описал предпочтения в досуге: {secure.wrap_untrusted(raw, 'досуг')}\n"
            "Извлеки списки:\n"
            '{"movies":["название","..."],"artists":["имя","..."],"books":["книга","..."]}',
            500, tier="cheap", route="openai", module="firstvisit",
        )
    except Exception:
        d = {}
    def _split_raw(text, prefix_variants):
        """Fallback: ищем блок после ключевого слова."""
        low = text.lower()
        for pv in prefix_variants:
            idx = low.find(pv)
            if idx != -1:
                chunk = text[idx + len(pv):]
                end = min((low.find(p, idx + 1) for p in ["фильм", "музык", "книг", "исполни"]
                           if low.find(p, idx + 1) != -1), default=len(chunk))
                return [x.strip() for x in re.split(r"[,;\n]+", chunk[:end]) if x.strip()]
        return []

    movies = d.get("movies") if isinstance(d.get("movies"), list) else _split_raw(raw, ["фильм", "сериал"])
    artists = d.get("artists") if isinstance(d.get("artists"), list) else _split_raw(raw, ["музык", "исполни", "артист"])
    books = d.get("books") if isinstance(d.get("books"), list) else _split_raw(raw, ["книг"])

    if movies:
        store.set_list(config.WATCHLIST_KEY, cid, [str(m)[:80] for m in movies[:30]])
        saved.append(f"Фильмы ({len(movies)}): {', '.join(str(m) for m in movies[:3])}…")
    if artists:
        store.set_list(config.ARTISTS_KEY, cid, [str(a)[:80] for a in artists[:30]])
        saved.append(f"Артисты ({len(artists)}): {', '.join(str(a) for a in artists[:3])}…")
    if books:
        store.set_list(config.BOOKS_KEY, cid, [str(b)[:80] for b in books[:30]])
        saved.append(f"Книги ({len(books)}): {', '.join(str(b) for b in books[:3])}…")
    if not saved:
        saved.append("Предпочтения сохранены")
    return saved


async def _save_balance(cid, raw: str) -> list:
    prof = store.get_profile(cid)
    prof["diet_prefs"] = raw[:500]
    store.set_profile(cid, prof)
    return [f"Предпочтения в питании: {raw[:80]}…" if len(raw) > 80 else raw]


# ---------- показ раздела ----------

async def _show_section(bot, cid, section: str):
    import menu
    key = _SECTION_KEY.get(section, "m_leisure")
    text, kb = menu.menu_screen(key)
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb, parse_mode="HTML")
