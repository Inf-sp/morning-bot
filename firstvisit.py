"""Первый вход в раздел: быстрый опрос для заполнения профиля пользователя.

Статусы онбординга — см. onboarding_status.
"""
import re
import store
import ai
import config
import secure
import onboarding_status as obs
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from ui import onboarding as onboarding_ui

# Разделы онбординга: wardrobe, learning, leisure, health, cooking.
# «Здоровье» (health) и «Готовка» (cooking) — независимые разделы; раньше это
# был единый `balance`.
_SECTIONS = obs.SECTIONS

# Секция -> ключ меню (для показа экрана раздела после опроса).
_SECTION_KEY = {
    "wardrobe": "m_wardrobe",
    "learning": "m_learn",
    "leisure":  "m_leisure",
    "health":   "m_balance",
    "cooking":  "m_food",
}

# Меню-ключ -> секция (обратный маппинг для роутинга входа в раздел).
MENU_KEY_TO_SECTION = {v: k for k, v in _SECTION_KEY.items()}


def _skip_kb(section: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⏭ Пропустить", callback_data=f"fv_skip_{section}")]]
    )


# Разделы с выбором тегов-чекбоксов вместо свободного текста.
# По образцу кухонь: заранее заданный список популярных вариантов + мультивыбор.
_TAG_OPTIONS = {
    "health": [
        ("sleep", "😴 Сон"),
        ("energy", "⚡ Энергия"),
        ("anxiety", "🌊 Тревожность"),
        ("habits", "🔁 Привычки"),
        ("sport", "🏃 Спорт"),
        ("nutrition", "🥗 Питание"),
    ],
    "leisure": [
        ("drama", "🎭 Драма"),
        ("comedy", "😂 Комедия"),
        ("scifi", "🚀 Фантастика"),
        ("thriller", "🔪 Триллер"),
        ("documentary", "🎬 Док"),
        ("rock", "🎸 Рок"),
        ("electronic", "🎧 Электроника"),
        ("classic", "🎻 Классика"),
        ("fiction", "📖 Худ. лит-ра"),
        ("nonfiction", "📚 Нон-фикшн"),
    ],
}

# Временный выбор тегов на время опроса: {cid: {section: set(keys)}}
_tag_selection: dict = {}


def _tag_labels(section: str, keys) -> list:
    return [label for key, label in _TAG_OPTIONS[section] if key in keys]


def _tags_kb(cid, section: str) -> InlineKeyboardMarkup:
    selected = _tag_selection.get(str(cid), {}).get(section, set())
    buttons = [
        InlineKeyboardButton(
            ("✅ " if key in selected else "⬜ ") + label,
            callback_data=f"fv_tag_{section}_{key}",
        )
        for key, label in _TAG_OPTIONS[section]
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("✅ Готово", callback_data=f"fv_tagdone_{section}")])
    if section == "leisure":
        # Помимо жанров-тегов можно ввести конкретные названия текстом.
        rows.append([InlineKeyboardButton("✍️ Ввести названия", callback_data="fv_leisure_text")])
    rows.append([InlineKeyboardButton("⏭ Пропустить", callback_data=f"fv_skip_{section}")])
    return InlineKeyboardMarkup(rows)


# ---------- проверка «нужен ли опрос» ----------

def _has_data(cid, section: str) -> bool:
    """Есть ли в разделе реальные данные (эвристика заполнённости)."""
    if section == "wardrobe":
        import settings as _s
        w = store.load_wardrobe(cid)
        has_wardrobe = bool(store.wardrobe_to_text(w).strip())
        has_style = bool(_s.get(cid, "style") or _s.get(cid, "body"))
        return has_wardrobe or has_style
    if section == "learning":
        import settings as _s
        has_lang = bool(store.get_learning_language(cid) or _s.get(cid, "study_lang"))
        has_level = store.has_level(cid, "нидерландский") or store.has_level(cid, "английский")
        return has_lang or has_level
    if section == "leisure":
        prof = store.get_profile(cid)
        return bool(
            prof.get("leisure_genres")
            or store.get_list(config.ARTISTS_KEY, cid)
            or store.get_list(config.WATCHLIST_KEY, cid)
            or store.get_list(config.BOOKS_KEY, cid)
        )
    if section == "health":
        prof = store.get_profile(cid)
        return bool(prof.get("health_focus") or prof.get("diet_prefs"))
    if section == "cooking":
        import settings as _s
        prof = store.get_profile(cid)
        return bool(_s.cuisines(cid) or prof.get("diet_prefs") or store.get_list(config.FRIDGE_KEY, cid))
    return False


def needs_setup(cid, section: str) -> bool:
    """True — раздел не настроен и опрос ещё не проходили (первый показ)."""
    status = obs.get(cid, section)
    if status != obs.NOT_STARTED:
        return False
    if _has_data(cid, section):
        # Данные уже есть — помечаем auto_configured, чтобы не спрашивать снова.
        obs.set_status(cid, section, obs.AUTO_CONFIGURED)
        return False
    return True


# ---------- показ опроса ----------

async def show_prompt(bot, cid, section: str):
    msg = onboarding_ui.firstvisit_prompt(section)
    if section in _TAG_OPTIONS:
        # Разделы с тегами: чекбоксы вместо свободного текста.
        _tag_selection.setdefault(str(cid), {})[section] = set()
        await bot.send_message(
            chat_id=cid,
            text=msg.text,
            entities=msg.entities,
            reply_markup=_tags_kb(cid, section),
        )
        return
    store.pending_input[str(cid)] = f"firstvisit_{section}"
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=_skip_kb(section),
    )


async def toggle_tag(bot, cid, section: str, key: str, q=None):
    """Переключить тег-чекбокс в опросе (health/leisure)."""
    if section not in _TAG_OPTIONS:
        return
    valid = {k for k, _ in _TAG_OPTIONS[section]}
    if key not in valid:
        return
    sel = _tag_selection.setdefault(str(cid), {}).setdefault(section, set())
    if key in sel:
        sel.discard(key)
    else:
        sel.add(key)
    if q is not None:
        try:
            await q.message.edit_reply_markup(reply_markup=_tags_kb(cid, section))
            return
        except Exception:
            pass


async def leisure_text_prompt(bot, cid):
    """Переход из тегов-жанров Досуга в текстовый ввод названий фильмов/музыки/книг.

    Отмеченные жанры сохраняются сразу (без merge-вопроса — на этом шаге раздел
    ещё в процессе настройки), затем запрашивается текст с названиями.
    """
    keys = _tag_selection.get(str(cid), {}).pop("leisure", set())
    labels = _tag_labels("leisure", keys)
    if labels:
        await _save_leisure(cid, ", ".join(labels), mode="add")
    store.pending_input[str(cid)] = "firstvisit_leisure_titles"
    msg = onboarding_ui.firstvisit_leisure_titles_prompt()
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=_skip_kb("leisure"),
    )


async def tags_done(bot, cid, section: str):
    """Кнопка «Готово» в опросе-тегах: собрать выбор и сохранить как ответ опроса."""
    if section not in _TAG_OPTIONS:
        return
    keys = _tag_selection.get(str(cid), {}).pop(section, set())
    labels = _tag_labels(section, keys)
    if not labels:
        # Ничего не выбрано — трактуем как пропуск.
        await skip(bot, cid, section)
        return
    raw = ", ".join(labels)
    await _apply_response(bot, cid, section, raw, mode="replace")


async def skip(bot, cid, section: str):
    """Пользователь нажал «Пропустить» — помечаем skipped (не блокирует возврат)."""
    obs.set_status(cid, section, obs.SKIPPED)
    store.pending_input.pop(str(cid), None)
    await _show_section(bot, cid, section)


# ---------- ответ на опрос ----------

async def handle_response(bot, cid, section: str, text: str):
    """Пользователь прислал текст опроса — сохраняем сразу."""
    store.pending_input.pop(str(cid), None)
    raw = secure.clamp(text)
    await _apply_response(bot, cid, section, raw, mode="replace")


async def _apply_response(bot, cid, section: str, raw: str, mode: str):
    """Сохранить ответ опроса в выбранном режиме и пометить раздел completed."""
    if section == "wardrobe":
        saved = await _save_wardrobe(cid, raw, mode)
    elif section == "learning":
        saved = await _save_learn(cid, raw, mode)
    elif section == "leisure":
        saved = await _save_leisure(cid, raw, mode)
    elif section == "leisure_titles":
        saved = await _save_leisure_titles(cid, raw, mode)
        section = "leisure"  # статус ставим на сам раздел
    elif section == "health":
        saved = await _save_health(cid, raw, mode)
    elif section == "cooking":
        saved = await _save_cooking(cid, raw, mode)
    else:
        saved = []

    obs.set_status(cid, section, obs.COMPLETED)

    if saved:
        msg = onboarding_ui.firstvisit_saved(saved)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    await _show_section(bot, cid, section)


# ---------- сохранение по разделам ----------

async def _save_wardrobe(cid, raw: str, mode: str) -> list:
    import settings as _s
    import wardrobe as _w
    saved = []
    zones_desc = "; ".join(f"{z}: {', '.join(subs)}" for z, subs in store.ZONE_SUBCATS.items())
    try:
        d = await ai.allm_json(
            f"Пользователь описал гардероб: {secure.wrap_untrusted(raw, 'гардероб')}\n"
            "Извлеки в JSON:\n"
            '{"style":"стиль одной фразой или пусто","body":"параметры тела одной строкой или пусто",'
            '"items":[{"zone":"","subcategory":"","name":"","color":"","color_secondary":"",'
            '"material":"","style":""}]}\n'
            f"Зоны и подкатегории (используй ТОЛЬКО эти значения): {zones_desc}",
            700, tier="cheap", module="firstvisit",
        )
    except Exception:
        d = {}
    if d.get("style"):
        _s.set_(cid, "style", str(d["style"])[:120])
        saved.append(f"Стиль: {d['style']}")
    if d.get("body"):
        _s.set_(cid, "body", str(d["body"])[:200])
        saved.append(f"Параметры: {d['body']}")
    raw_items = d.get("items") or []
    if isinstance(raw_items, list) and raw_items:
        norm = [_w.normalize_parsed_item(it) for it in raw_items]
        norm = [it for it in norm if it]
        if mode == "replace":
            # Полная замена гардероба: очищаем и пишем заново.
            store.reset_wardrobe(cid)
        if norm:
            store.add_wardrobe_items(cid, norm)
            saved.append(f"Вещей в шкафу: {len(norm)}")
    if not saved:
        _s.set_(cid, "style", raw[:120])
        saved.append("Стиль сохранён")
    return saved


async def _save_learn(cid, raw: str, mode: str) -> list:
    import settings as _s
    saved = []
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
        store.set_learning_language(cid, "en" if detected_lang == "английский" else "nl")
        saved.append(f"Язык: {detected_lang}")
    if detected_lang and level_found:
        store.set_level(cid, detected_lang, level_found)
        saved.append(f"Уровень: {level_found}")
    if not saved:
        _s.set_(cid, "study_lang", "нидерландский")
        store.set_learning_language(cid, "nl")
        saved.append("Язык обучения: нидерландский (по умолчанию)")
    return saved


async def _save_leisure(cid, raw: str, mode: str) -> list:
    """Досуг: любимые жанры (теги). Названия фильмов/книг добавляются в самом разделе."""
    prof = store.get_profile(cid)
    new = raw[:300]
    if mode == "add" and prof.get("leisure_genres"):
        # Объединяем метки-жанры без дублей, сохраняя порядок.
        existing = [g.strip() for g in prof["leisure_genres"].split(",") if g.strip()]
        seen = {g.lower() for g in existing}
        for g in (x.strip() for x in new.split(",") if x.strip()):
            if g.lower() not in seen:
                seen.add(g.lower())
                existing.append(g)
        combined = ", ".join(existing)[:400]
    else:
        combined = new
    prof["leisure_genres"] = combined
    store.set_profile(cid, prof)
    return [f"Любимые жанры: {new}"]


def _merge_list(key, cid, new_items, mode, cap=30):
    """Списочное сохранение: add — merge без дублей, replace — перезапись."""
    new_items = [str(x)[:80] for x in new_items][:cap]
    if mode == "add":
        existing = store.get_list(key, cid)
        seen = {x.lower() for x in existing}
        merged = list(existing)
        for it in new_items:
            if it.lower() not in seen:
                seen.add(it.lower())
                merged.append(it)
        store.set_list(key, cid, merged[:cap])
    else:
        store.set_list(key, cid, new_items)


async def _save_leisure_titles(cid, raw: str, mode: str) -> list:
    """Досуг (текстовый ввод): извлекает названия фильмов/музыки/книг из текста."""
    saved = []
    try:
        d = await ai.allm_json(
            f"Пользователь описал предпочтения в досуге: {secure.wrap_untrusted(raw, 'досуг')}\n"
            "Извлеки списки:\n"
            '{"movies":["название","..."],"artists":["имя","..."],"books":["книга","..."]}',
            500, tier="cheap", route="gemini", module="firstvisit",
        )
    except Exception:
        d = {}

    def _split_raw(text, prefix_variants):
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
        _merge_list(config.WATCHLIST_KEY, cid, movies, mode)
        saved.append(f"Фильмы ({len(movies)}): {', '.join(str(m) for m in movies[:3])}…")
    if artists:
        _merge_list(config.ARTISTS_KEY, cid, artists, mode)
        saved.append(f"Музыканты ({len(artists)}): {', '.join(str(a) for a in artists[:3])}…")
    if books:
        _merge_list(config.BOOKS_KEY, cid, books, mode)
        saved.append(f"Книги ({len(books)}): {', '.join(str(b) for b in books[:3])}…")
    if not saved:
        saved.append("Предпочтения сохранены")
    return saved


async def _save_health(cid, raw: str, mode: str) -> list:
    """Здоровье: пользовательские фокус-цели как теги/текст, без медицинских выводов."""
    prof = store.get_profile(cid)
    new = raw[:500]
    if mode == "add" and prof.get("health_focus"):
        combined = f"{prof['health_focus']}; {new}"[:800]
    else:
        combined = new
    prof["health_focus"] = combined
    store.set_profile(cid, prof)
    return [f"Что отслеживаем: {new[:80]}…" if len(new) > 80 else new]


async def _save_cooking(cid, raw: str, mode: str) -> list:
    """Готовка: предпочтения в еде (diet_prefs)."""
    prof = store.get_profile(cid)
    new = raw[:500]
    if mode == "add" and prof.get("diet_prefs"):
        combined = f"{prof['diet_prefs']}; {new}"[:800]
    else:
        combined = new
    prof["diet_prefs"] = combined
    store.set_profile(cid, prof)
    return [f"Предпочтения в еде: {new[:80]}…" if len(new) > 80 else new]


# ---------- показ раздела ----------

async def _show_section(bot, cid, section: str):
    import menu
    key = _SECTION_KEY.get(section, "m_leisure")
    if key == "m_food":
        await menu.send_food_menu(bot, cid)
        return
    text, entities, kb = menu.menu_screen(key, cid)
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb, entities=entities)
