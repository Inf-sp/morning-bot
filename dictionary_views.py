"""Dictionary Telegram views extracted from the dictionary controller."""

async def _show_screen(
    bot, cid, text, entities=None, reply_markup=None, q=None, persistent_inline=False,
):
    """Навигация внутри словаря: редактирует текущее сообщение, если есть callback
    query, иначе (первый вход, текстовая команда) шлёт новое."""
    if q is not None:
        try:
            await q.message.edit_text(text, entities=entities, reply_markup=reply_markup)
            if persistent_inline:
                marker = getattr(bot, "mark_persistent_inline_message", None)
                if marker is not None:
                    marker(cid, getattr(q.message, "message_id", None))
            return
        except Exception:
            pass
    extra = {"persistent_inline": True} if persistent_inline else {}
    await bot.send_message(
        chat_id=cid,
        text=text,
        entities=entities,
        reply_markup=reply_markup,
        **extra,
    )


_DICT_ORIGIN_TO_BACK = {
    "menu": "m_learn",
    "mydata": "set_home",
    "learnset": "set_learning",
}
_DICT_BACK_TO_ORIGIN = {v: k for k, v in _DICT_ORIGIN_TO_BACK.items()}


async def send_dict(bot, cid, back="m_learn", q=None):
    c = _dict_counts(cid)
    nl_total = c["nl"]
    en_total = c["en"]
    msg = dict_ui.dict_overview(nl_total, en_total)
    origin = _DICT_BACK_TO_ORIGIN.get(back, "menu")
    rows = [
        [InlineKeyboardButton(f"🇳🇱 Нидерландский ({nl_total})", callback_data=f"a_dictlang_nl_from_{origin}")],
        [InlineKeyboardButton(f"🇬🇧 Английский ({en_total})", callback_data=f"a_dictlang_en_from_{origin}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ]
    await _show_screen(bot, cid, msg.text, msg.entities, InlineKeyboardMarkup(rows), q=q)

async def send_dict_lang(bot, cid, lang, back="m_learn", q=None, page=0):
    """Главный экран языкового словаря: категории вместо плоской сетки."""
    # Открытие словаря — быстрый UI-маршрут. Тяжёлые одноразовые AI-миграции
    # выполняет фоновая задача, здесь остаётся только локальное чтение/нормализация.
    entries = _dict_lang_entries(cid, lang)
    flag = "🇳🇱" if lang == "nl" else "🇬🇧"
    rows = []
    for category in _DICT_VISIBLE_CATEGORY_ORDER:
        index = _DICT_CATEGORY_ORDER.index(category)
        count = sum(1 for item in entries if _dictionary_category(item) == category)
        rows.append([InlineKeyboardButton(
            f"{category} · {count}", callback_data=f"a_dictcat_{lang}_{index}_0",
        )])
    rows.append([InlineKeyboardButton("✨ Подобрать новые слова", callback_data=f"a_dictseed_start_{lang}")])
    rows.append([InlineKeyboardButton("🆕 Добавить слово", callback_data=f"a_dictadd_smart_{lang}")])
    if entries:
        rows.append([InlineKeyboardButton(
            "✨ Проверить весь словарь", callback_data=f"a_dictcheckall_{lang}",
        )])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    if entries:
        text = f"{flag} Мой словарь · {len(entries)} слов"
    else:
        text = f"{flag} Мой словарь\n\nПока здесь нет слов."
    await _show_screen(bot, cid, text, None, InlineKeyboardMarkup(rows), q=q)


async def send_dict_category(bot, cid, lang, category_index, page=0, q=None):
    if not 0 <= category_index < len(_DICT_CATEGORY_ORDER):
        await send_dict_lang(bot, cid, lang, q=q)
        return
    category = _DICT_CATEGORY_ORDER[category_index]
    entries = [
        item for item in _dict_lang_entries(cid, lang)
        if _dictionary_category(item) == category
    ]
    flag = "🇳🇱" if lang == "nl" else "🇬🇧"
    if not entries:
        rows = [[
            InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"),
            InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
        ]]
        await _show_screen(
            bot, cid, f"{flag} {category}\n\nПока здесь нет записей.", None,
            InlineKeyboardMarkup(rows), q=q,
        )
        return
    page = max(0, min(int(page), len(entries) - 1))
    entry = entries[page]
    if _entry_needs_ai_refresh(entry):
        entry = await _refresh_dict_entry(cid, entry)
    msg = dict_ui.dict_category_entry(category, page, len(entries), entry)
    rows = []
    if len(entries) > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"a_dictcat_{lang}_{category_index}_{(page - 1) % len(entries)}"),
            InlineKeyboardButton(f"{page + 1} / {len(entries)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"a_dictcat_{lang}_{category_index}_{(page + 1) % len(entries)}"),
        ])
    rows.extend(_dict_tts_row(entry))
    word_id = str(entry.get("id") or "")
    if word_id:
        rows.append([InlineKeyboardButton(
            "✨ Проверить карточку", callback_data=f"a_dictcheck_{word_id}",
        )])
        rows.append([InlineKeyboardButton(
            delete_label("Удалить"),
            callback_data=f"a_dictcatdel_{lang}_{category_index}_{page}_{word_id}",
        )])
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    await _show_screen(
        bot, cid, msg.text, msg.entities, InlineKeyboardMarkup(rows),
        q=q, persistent_inline=True,
    )


async def check_dictionary_entry(bot, cid, word_id, q=None):
    """Принудительно пересобирает одну карточку и показывает исправленный результат."""
    entry = _entry_by_id(cid, word_id)
    if not entry:
        await send_dict_lang(bot, cid, _active_language_code(cid), q=q)
        return
    updated = await _refresh_dict_entry(cid, entry, force=True)
    if updated is entry or not updated:
        await bot.send_message(
            chat_id=cid, text="Не получилось проверить карточку. Попробую при общей проверке.",
            reply_markup=back_menu_keyboard(f"a_dictlang_{_dict_lang(entry)}"),
        )
        return
    updated = normalize_user_dictionary(cid)
    match = next((item for item in updated if str(item.get("id") or "") == str(word_id)), entry)
    msg = _dict_entry_message(match, status="updated")
    await _show_screen(
        bot, cid, msg.text, msg.entities, _dict_entry_view_kb(match, 0, ""),
        q=q, persistent_inline=True,
    )


async def request_dictionary_recheck(bot, cid, lang, q=None):
    """Сохраняет запрос полной проверки; фоновая задача заберёт его без ожидания UI."""
    code = lang if lang in ("nl", "en") else _active_language_code(cid)

    def change(profile):
        profile["dictionary_recheck_request"] = {
            "lang": code, "requested_at": datetime.now(config.TZ).isoformat(),
        }
        return profile, None

    store.mutate_profile(cid, change)
    await _show_screen(
        bot, cid,
        "⏳ Проверяю словарь в фоне\n\nМожно продолжать пользоваться ботом. Я сообщу, когда закончу.",
        None,
        back_menu_keyboard(f"a_dictlang_{code}"), q=q,
    )


async def process_requested_dictionary_rechecks(bot, cids, limit=1):
    """Обрабатывает не больше limit словарей за проход и присылает итог."""
    handled = 0
    for cid in cids:
        request = store.get_profile(cid).get("dictionary_recheck_request") or {}
        lang = request.get("lang")
        if lang not in ("nl", "en") or handled >= limit:
            continue
        before = [dict(item) for item in _dict_lang_entries(cid, lang)]
        started_at = datetime.now(config.TZ).isoformat()
        await rebuild_dictionary_entries(cid, force=True, lang=lang)
        after = [dict(item) for item in normalize_user_dictionary(cid) if _dict_lang(item) == lang]
        checked = sum(
            1 for item in after
            if str(item.get("dictionary_rechecked_at") or "") >= started_at
        )
        if before and checked == 0:
            continue
        before_by_id = {str(item.get("id") or ""): item for item in before}
        changed = moved = 0
        compared_fields = (
            "lang", "term", "translation", "article", "pos", "breakdown",
            "plural", "forms", "examples", "construction", "pronunciation",
            "essence", "insight", "exercise_ru", "exercise_answer",
        )
        for item in after:
            old = before_by_id.get(str(item.get("id") or ""))
            if not old:
                continue
            if any(old.get(field) != item.get(field) for field in compared_fields):
                changed += 1
            if _dictionary_category(old) != _dictionary_category(item):
                moved += 1

        def clear(profile):
            profile.pop("dictionary_recheck_request", None)
            return profile, None

        store.mutate_profile(cid, clear)
        handled += 1
        await bot.send_message(
            chat_id=cid,
            text=("✅ Словарь проверен\n\n"
                  f"Исправлено: {changed}\n"
                  f"Перенесено между категориями: {moved}\n"
                  f"Уже было правильно: {max(0, len(after) - changed)}"),
            reply_markup=back_menu_keyboard(f"a_dictlang_{lang}"),
        )
    return handled


async def send_dict_manage(bot, cid, lang, back="m_learn", q=None, page=0):
    """Список слов: тап открывает карточку
    с удалением) + приглашение написать слово текстом, чтобы добавить его."""
    store.pending_input[str(cid)] = f"dictadd_smart_{lang}"
    entries = _dict_lang_entries(cid, lang)
    flag = "🇳🇱" if lang == "nl" else "🇬🇧"
    lang_title = "нидерландского" if lang == "nl" else "английского"
    add_hint = (
        "Пришли слово для изучения — можно сразу несколько, каждое с новой строки.\n"
        "Я сам приведу в правильную форму, переведу и разберу."
    )
    if not entries:
        rows = [[InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")]]
        text = f"{flag} Словарь {lang_title} языка пока пуст.\n\n{add_hint}"
        await _show_screen(bot, cid, text, None, InlineKeyboardMarkup(rows), q=q)
        return
    total_pages = max(1, (len(entries) + _DICT_LIST_PAGE_SIZE - 1) // _DICT_LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _DICT_LIST_PAGE_SIZE
    chunk = entries[start:start + _DICT_LIST_PAGE_SIZE]
    word_buttons = []
    for item in chunk:
        word_id = str(item.get("id") or "")
        word_buttons.append(InlineKeyboardButton(
            normalize_term_case(_entry_term(item), _kind_of(_entry_term(item)))[:20],
            callback_data=f"a_dictviewid_{page}_{word_id}",
        ))
    word_rows = [word_buttons[i:i + 2] for i in range(0, len(word_buttons), 2)]
    nav_rows = []
    if total_pages > 1:
        next_page = page + 1 if page < total_pages - 1 else 0
        nav_rows.append([InlineKeyboardButton("▶️", callback_data=f"a_dictedit_{lang}_{next_page}")])
    rows = word_rows + nav_rows + [[InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")]]
    text = (
        f"{flag} Показаны {start + 1}–{start + len(chunk)} из {len(entries)}. "
        "Нажми на слово, чтобы посмотреть перевод, пример и удалить его.\n\n"
        f"{add_hint}"
    )
    await _show_screen(bot, cid, text, None, InlineKeyboardMarkup(rows), q=q)


async def send_dict_add_prompt(bot, cid, lang):
    """Включает ввод новой записи и явно просит написать её в чат."""
    store.pending_input[str(cid)] = f"dictadd_smart_{lang}"
    await bot.send_message(
        chat_id=cid,
        text=(
            "✏️ Напиши слово в чат.\n\n"
            "Я приведу его в правильную форму, переведу и добавлю в твой словарь."
        ),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"),
            InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
        ]]),
    )


def _dict_manage_kb(lang: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Добавить слово", callback_data=f"a_dictadd_smart_{lang}")],
        [InlineKeyboardButton("🎚️ Мой словарь", callback_data=f"a_dictlang_{lang}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_dict_search_prompt(bot, cid, lang, q=None):
    store.pending_input[str(cid)] = f"dictsearch_{lang}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictedit_{lang}"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")]])
    await _show_screen(bot, cid, "🔍 Введи слово для поиска.", None, kb, q=q)


def _dict_tts_row(entry):
    if entry.get("lang") == "nl" and entry.get("id"):
        return [[InlineKeyboardButton("🔊 Прослушать", callback_data=f"tts_word:{entry['id']}")]]
    return []


def _dict_search_kb(entry, term_key):
    lang = _dict_lang(entry)
    word_id = str(entry.get("id") or "")
    delete_row = ([[InlineKeyboardButton(delete_label("Удалить"), callback_data=f"a_dictdelid_{word_id}")]]
                  if word_id else [])
    return InlineKeyboardMarkup(_dict_tts_row(entry) + delete_row + [
        [InlineKeyboardButton("🎚️ Мой словарь", callback_data=f"a_dictlang_{lang}_keep")],
        *([[InlineKeyboardButton(
            "✨ Проверить карточку", callback_data=f"a_dictcheck_{word_id}",
        )]] if word_id else []),
        [InlineKeyboardButton("🔍 Искать ещё", callback_data=f"a_dictsearch_{lang}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}_keep"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def handle_dict_search(bot, cid, lang, query):
    """Ищет по подстроке термина в словаре, показывает карточку с кнопкой удаления."""
    query_norm = re.sub(r"\s+", " ", (query or "").strip()).casefold()
    if not query_norm:
        await bot.send_message(
            chat_id=cid,
            text="Пришли слово или часть фразы для поиска.",
            reply_markup=back_menu_keyboard(f"a_dictedit_{lang}"),
        )
        return
    words = [item for item in _ensure_dict(cid) if entry_is_dictionary_word(item)]
    match = None
    for item in words:
        if _dict_lang(item) != lang:
            continue
        term = _entry_term(item)
        if query_norm in term.casefold():
            match = item
            break
    if not match:
        await bot.send_message(
            chat_id=cid,
            text="Не нашла в словаре. Попробуй другое слово или посмотри весь список.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Мои слова", callback_data=f"a_dictedit_{lang}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
            ]),
        )
        return
    if _entry_needs_ai_refresh(match):
        match = await _refresh_dict_entry(cid, match)
    msg = _dict_entry_message(match, status="found")
    term_key = _dict_item_key(lang, "", _entry_term(match))[2]
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=_dict_search_kb(match, term_key), persistent_inline=True)


def _entry_by_id(cid, word_id):
    return next((item for item in _ensure_dict(cid) if str(item.get("id") or "") == str(word_id)), None)


_DICT_LIST_PAGE_SIZE = 10
_DICT_CATEGORY_ORDER = (
    "Прилагательные", "Глаголы", "Существительные", "Местоимения",
    "Наречия", "Предлоги",
)
_DICT_VISIBLE_CATEGORY_ORDER = _DICT_CATEGORY_ORDER


def _dictionary_category(entry):
    """Локально раскладывает одиночные слова по частям речи."""
    if not entry_is_dictionary_word(entry):
        return ""
    pos = canonical_part_of_speech(entry)
    aliases = {
        "adjective": "Прилагательные", "adj": "Прилагательные",
        "прилагательное": "Прилагательные", "bijvoeglijk naamwoord": "Прилагательные",
        "verb": "Глаголы", "глагол": "Глаголы", "werkwoord": "Глаголы",
        "noun": "Существительные", "существительное": "Существительные",
        "zelfstandig naamwoord": "Существительные",
        "pronoun": "Местоимения", "местоимение": "Местоимения", "voornaamwoord": "Местоимения",
        "adverb": "Наречия", "наречие": "Наречия", "bijwoord": "Наречия",
        "preposition": "Предлоги", "предлог": "Предлоги", "voorzetsel": "Предлоги",
    }
    if pos in aliases:
        return aliases[pos]
    return "Существительные"


def _dict_lang_entries(cid, lang):
    """Записи языка, отсортированные по категории и алфавиту."""
    entries = [
        w for w in _ensure_dict(cid)
        if _dict_lang(w) == lang and entry_is_dictionary_word(w)
    ]
    category_index = {label: index for index, label in enumerate(_DICT_CATEGORY_ORDER)}
    return sorted(entries, key=lambda w: (
        category_index[_dictionary_category(w)], _cap(_entry_term(w)).casefold(),
    ))


async def send_dict_entry_view(bot, cid, lang, page, term_key, q=None):
    """Карточка слова из списка — тот же вид, что при добавлении, плюс удаление."""
    entries = _dict_lang_entries(cid, lang)
    match = next((w for w in entries if _dict_entry_matches_key(w, lang, term_key)), None)
    if not match:
        await send_dict_lang(bot, cid, lang, page=page, q=q)
        return
    if _entry_needs_ai_refresh(match):
        match = await _refresh_dict_entry(cid, match)
    msg = _dict_entry_message(match, status="found")
    await _show_screen(
        bot, cid, msg.text, msg.entities, _dict_entry_view_kb(match, page, term_key),
        q=q, persistent_inline=True)


async def send_dict_entry_view_by_id(bot, cid, page, word_id, q=None):
    match = _entry_by_id(cid, word_id)
    if not match:
        await send_dict_lang(bot, cid, _active_language_code(cid), page=page, q=q)
        return
    if _entry_needs_ai_refresh(match):
        match = await _refresh_dict_entry(cid, match)
    msg = _dict_entry_message(match, status="found")
    await _show_screen(
        bot, cid, msg.text, msg.entities, _dict_entry_view_kb(match, page, ""),
        q=q, persistent_inline=True)
