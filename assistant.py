from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
import myday
import ze
import util

DOCTOR_INTRO = (
    "👩🏻‍⚕️ Врач\n\n"
    "Дам общую справочную информацию о здоровье и лекарствах. Это не диагноз и не назначение - "
    "при тревожных симптомах обратись к специалисту.\n\n"
    "Опиши, что беспокоит, или спроси про лекарство 👇"
)

def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

# универсальная клавиатура под ответом: [Продолжить][⭐][В меню]
def _ans_kb(cont_label="🔄 Продолжить", cont_cb="chat_retry"):
    rows = []
    if cont_label and cont_cb:
        rows.append([(cont_label, cont_cb)])
    rows.append([("⭐ В закладки", "as_fav")])
    rows.append([("⬅️ Назад", "m_close")])
    return _kb(rows)

def _recipe_kb():
    return _kb([
        [("📖 Полный рецепт", "as_food_full")],
        [("🔄 Ещё рецепт", "as_food")],
        [("⭐ В закладки", "as_fav")],
        [("⬅️ Назад", "m_close")],
    ])

def _back_kb():
    return _kb([[("⬅️ Назад", "m_close")]])


async def _send_html(bot, cid, text, reply_markup=None):
    """Одиночное сообщение в Telegram HTML с чисткой markdown и откатом на plain."""
    html = util.tg_html(text or "")
    try:
        await bot.send_message(chat_id=cid, text=html, parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        await bot.send_message(chat_id=cid, text=html, reply_markup=reply_markup)


async def _send(bot, cid, text, kb=None):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    store.last_answer[str(cid)] = text
    store.last_source.setdefault(str(cid), "Ассистент")
    html = util.tg_html(text)
    chunks = [html[i:i+4000] for i in range(0, len(html), 4000)]
    for i, c in enumerate(chunks):
        markup = (kb if kb is not None else _ans_kb()) if i == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id=cid, text=c, parse_mode="HTML", reply_markup=markup)
        except Exception:
            # если HTML невалиден - отправляем как обычный текст, без падения
            await bot.send_message(chat_id=cid, text=c, reply_markup=markup)




# ---------- Кулинарный радар ----------
def _gen_recipe(constraint):
    return ai.llm_json(
        f"Предложи 1 рецепт ({constraint}), 1 человек, электрическая плита. Компактно.\n"
        "Оформление полей в Telegram HTML: подзаголовки тегом <b>...</b>, пункты с маркера «• ». "
        "НИКАКОГО markdown - запрещены *, **, #, `. Заголовки <b>Ингредиенты</b> и <b>Приготовление</b>, пункты с новой строки «• ».\n"
        'JSON: {"name":"название","time":"X мин","servings":"N порц.",'
        '"short":"2-3 коротких предложения как готовить","full":"полный рецепт в Telegram HTML, БЕЗ повтора названия в начале: блок <b>Ингредиенты</b> со списком пунктов «• », затем <b>Приготовление</b> с пунктами «• »"}', 900)

def _recipe_card(d):
    return (f"🥘 <b>{util.esc(d.get('name',''))}</b>\n\n"
            f"⏱️ {util.esc(d.get('time',''))} • 🍽️ {util.esc(d.get('servings',''))}\n\n"
            f"{d.get('short','')}")

async def send_recipe(bot, cid, constraint="обычное блюдо"):
    await bot.send_message(chat_id=cid, text="Подбираю...")
    try:
        d = _gen_recipe(constraint)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("recipe", constraint)
    card = _recipe_card(d)
    store.last_source[str(cid)] = "Питание · Рецепт"
    store.last_answer[str(cid)] = card
    await _send_html(bot, cid, card, reply_markup=_recipe_kb())

async def send_recipe_full(bot, cid):
    d = store.last_recipe.get(str(cid))
    if not d:
        await bot.send_message(chat_id=cid, text="Сначала выбери рецепт."); return
    txt = f"📖 <b>{util.esc(d.get('name',''))}</b>\n\n{d.get('full','')}"
    store.last_answer[str(cid)] = txt
    await _send_html(bot, cid, txt, reply_markup=_recipe_kb())

async def send_leftovers(bot, cid, ingredients):
    await bot.send_message(chat_id=cid, text="Смотрю, что можно приготовить...")
    try:
        out = ai.llm(
            f"Есть продукты: {ingredients}. Предложи 3 простых рецепта только из них (+ базовые специи). "
            "Каждый: 🥘 Название • ⏱️ время, затем 1-2 строки как готовить, с переносами. Компактно, эмодзи. Без воды.", 800, 0.9)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_action[str(cid)] = ("leftovers", ingredients)
    await _send(bot, cid, out, kb=_recipe_kb())


# ---------- Идеи / СДВГ / Подбодрить / Карта ----------
def _gen_idea(cid):
    return ai.llm("Сгенерируй 1 свежую идею/мини-проект (дизайн, ИИ, фото, AR, путешествия). "
                  "Придумай ей короткое название. СТРОГО формат, без markdown:\n"
                  "💡 Идея дня\n\n{название проекта}\n\n{1-2 предложения описания}\n\n"
                  "Польза: {через запятую}\nНачать: {через запятую}\n\nПервый результат: {что получится}",
                  400, 1.0, ai.LEARN_ORDER)

def _gen_motiv(cid):
    return ai.llm(
        "Сгенерируй блок «Личная мотивация» для человека с СДВГ. Тепло, по-доброму, без воды и клише. "
        "СТРОГО формат, без markdown, эмодзи как навигация (не для украшения):\n\n"
        "🎯 Личная мотивация\n\n"
        "💬 {1 короткая поддерживающая фраза под настроение}\n\n"
        "🧠 Фокус сейчас:\n"
        "• {микро-техника на 1 минуту против прокрастинации}\n"
        "• {как удержать внимание}\n\n"
        "⚡ Один шаг:\n"
        "• {одно конкретное мелкое действие прямо сейчас}\n\n"
        "🌱 Напоминание:\n"
        "• {короткая мысль про прогресс, а не идеал}",
        500, 0.95, ai.LEARN_ORDER)


# ---------- роли ----------
def _role_system(role):
    if role == "state":
        return ("Ты спокойный помощник по состоянию, фокусу и мотивации (не психотерапевт). "
                "Выслушай, разложи ситуацию на 1-3 конкретных шага, поддержи коротко. Без воды, с эмодзи. "
                "Если звучит тяжело - мягко предложи специалиста.")
    if role == "doctor":
        return ("Ты помощник по здоровью. Дай разбор СТРОГО в формате, кратко, с эмодзи:\n"
                "👩🏻‍⚕️ Разбор симптомов\n\n📍 Основная жалоба:\n{коротко}\n\n🔎 На что похоже:\n{1-2 предложения}\n\n"
                "✅ Рекомендации:\n• пункт\n• пункт\n\n🚨 Срочно к врачу:\n{когда}\n\nИтог: {одно короткое предложение}\n\n"
                "Не ставь диагноз, это общая информация и не замена врача.")
    return "Ты полезный ассистент."

_MED_RE = ("лекарств", "таблет", "препарат", "доз", "мг ", " мг", "метилфенидат", "ибупрофен",
           "парацетамол", "антибиотик", "капл", "сироп", "мазь", "витамин", "пилюл", "concerta",
           "ritalin", "риталин", "медикамент", "побочк", "побочн", "как принимать")

def _is_med_question(text):
    t = (text or "").lower()
    return any(k in t for k in _MED_RE)

def _med_system():
    return ("Ты помощник по лекарствам. Дай СПРАВОЧНУЮ информацию о препарате СТРОГО в формате, кратко, с эмодзи:\n"
            "💊 {название и доза если есть}\n\n"
            "📍 Зачем:\n{коротко}\n\n"
            "⏱️ Когда работает:\n{через сколько и сколько держится}\n\n"
            "⚠️ Часто бывает:\n• побочка\n• побочка\n\n"
            "💡 Важно:\n• пункт\n• пункт\n\n"
            "🚨 К врачу если:\n• симптом\n• симптом\n\n"
            "Итог: {одно короткое предложение}\n\n"
            "Это общая справочная информация, не назначение. Дозы и схему определяет врач.")

def _doctor_candidates(symptoms):
    data = ai.llm_json(
        f"Пользователь описал: {symptoms}\nДай 6 коротких справочных тезисов (общая информация о возможных "
        "причинах/состояниях при таких симптомах; НЕ диагноз). JSON: {\"items\": [\"тезис\", ...]}", 900)
    return [x for x in data.get("items", []) if isinstance(x, str) and x.strip()]

async def doctor_answer(bot, cid, symptoms):
    await bot.send_chat_action(chat_id=cid, action="typing")
    if _is_med_question(symptoms):
        prompt = f"{_med_system()}\n\nВопрос про лекарство: {symptoms}"
        try:
            out = ai.llm(prompt, 900, 0.4)
        except Exception as e:
            await bot.send_message(chat_id=cid, text=str(e)); return
        store.last_source[str(cid)] = "Здоровье · Лекарство"
        store.last_action[str(cid)] = ("role", "doctor", symptoms)
        await _send(bot, cid, out, kb=_ans_kb(None, None))
        return
    passages = []
    try:
        cands = _doctor_candidates(symptoms)
        ranked = ze.rerank(symptoms, cands, top_n=3)
        passages = [t for t, _ in ranked]
    except Exception:
        passages = []
    base = _role_system("doctor")
    if passages:
        ctx = "\n".join(f"- {p}" for p in passages)
        prompt = f"{base}\n\nНаиболее релевантные тезисы (по симптомам):\n{ctx}\n\nСимптомы: {symptoms}"
    else:
        prompt = f"{base}\n\nСимптомы: {symptoms}"
    try:
        out = ai.llm(prompt, 900, 0.5)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_source[str(cid)] = "Здоровье · Врач"
    store.last_action[str(cid)] = ("role", "doctor", symptoms)
    await _send(bot, cid, out, kb=_ans_kb(None, None))

async def handle_role(bot, cid, role, text):
    if role == "doctor":
        await doctor_answer(bot, cid, text); return
    await bot.send_chat_action(chat_id=cid, action="typing")
    try:
        out = ai.llm(_role_system(role) + "\n\nЗапрос пользователя:\n" + text, 1500, 0.7)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_action[str(cid)] = ("role", role, text)
    cont = ("🔄 Ещё совет", "chat_retry") if role == "state" else ("🔄 Продолжить", "chat_retry")
    await _send(bot, cid, out, kb=_ans_kb(*cont))


# ---------- избранное ----------
def _shorten(text):
    try:
        return ai.llm("Сожми до 1-3 строк, сохрани суть и важное, без воды:\n\n" + text, 200, 0.3).strip() or text[:300]
    except Exception:
        return text[:300]

async def save_fav(bot, cid):
    txt = store.last_answer.get(str(cid))
    if not txt:
        await bot.send_message(chat_id=cid, text="Нечего сохранять."); return
    short = _shorten(txt)
    source = store.last_source.get(str(cid), "Прочее")
    store.add_to_list(config.NOTES_KEY, cid, {"date": datetime.now(config.TZ).strftime("%d.%m"),
                                              "text": short, "source": source, "bucket": "fav"})
    await bot.send_message(chat_id=cid, text="⭐ Сохранено в закладки.")

def _top_cat(source):
    return (source or "Прочее").split(" · ")[0]

# тип закладки из source -> (blacklist_key, fav_setting_key, имя раздела настроек)
def _note_type(source):
    s = (source or "").lower()
    if "фильм" in s or "сериал" in s or "кино" in s:
        return ("movie", config.MOVIE_BLACKLIST_KEY, config.WATCHLIST_KEY, "Фильмы и сериалы")
    if "книг" in s:
        return ("book", config.BOOK_BLACKLIST_KEY, config.BOOKS_KEY, "Книги")
    if "музык" in s or "концерт" in s:
        return ("music", config.MUSIC_DISLIKE_KEY, config.ARTISTS_KEY, "Артисты")
    if "путешеств" in s or "стран" in s:
        return ("travel", config.TRAVEL_DISLIKE_KEY, config.FAVCOUNTRIES_KEY, "Страны")
    return (None, None, None, None)

def _note_bucket(n):
    """⭐ закладка ('fav') или ❤️ любимое ('love'). Старые заметки без поля - закладки."""
    return n.get("bucket", "fav") if isinstance(n, dict) else "fav"

async def note_delete_menu(bot, cid, i):
    """При удалении из закладок - спрашиваем, что сделать с элементом."""
    notes = store.get_list(config.NOTES_KEY, cid)
    if i >= len(notes):
        await send_notes(bot, cid); return
    n = notes[i]
    t = (n.get("text", "") if isinstance(n, dict) else str(n)).strip()
    typ, _, _, _ = _note_type(n.get("source", "") if isinstance(n, dict) else "")
    rows = []
    if typ:  # только для типизированных (фильм/книга/музыка/страна)
        rows.append([InlineKeyboardButton("🚫 В чёрный список", callback_data=f"as_noteblack_{i}")])
        rows.append([InlineKeyboardButton("❤️ В любимые", callback_data=f"as_notelove_{i}")])
    rows.append([InlineKeyboardButton("🗑 Просто удалить", callback_data=f"as_notedrop_{i}")])
    rows.append([InlineKeyboardButton("⬅️ Отмена", callback_data="as_notes")])
    await bot.send_message(chat_id=cid, text=f"Что сделать с «{t[:60]}»?",
                           reply_markup=InlineKeyboardMarkup(rows))

def _pop_note(cid, i):
    notes = store.get_list(config.NOTES_KEY, cid)
    if i >= len(notes):
        return None
    n = notes.pop(i)
    store.set_list(config.NOTES_KEY, cid, notes)
    return n

def _note_text(n):
    return (n.get("text", "") if isinstance(n, dict) else str(n)).strip()

async def note_to_blacklist(bot, cid, i):
    n = _pop_note(cid, i)
    if not n:
        await send_notes(bot, cid); return
    typ, black_key, _, cat = _note_type(n.get("source", "") if isinstance(n, dict) else "")
    t = _note_text(n)
    if black_key:
        store.add_to_list(black_key, cid, t)
        await bot.send_message(chat_id=cid, text=f"🚫 «{t[:50]}» - в чёрный список «{cat}». Больше не порекомендую.")
    else:
        await bot.send_message(chat_id=cid, text="Удалил из закладок.")
    await send_bucket(bot, cid, "fav")

async def note_to_love(bot, cid, i):
    n = _pop_note(cid, i)
    if not n:
        await send_notes(bot, cid); return
    typ, _, fav_key, cat = _note_type(n.get("source", "") if isinstance(n, dict) else "")
    t = _note_text(n)
    if fav_key:
        if typ == "travel":
            from util import country_flag
            store.add_to_list(fav_key, cid, {"name": t, "flag": country_flag(t)})
        else:
            store.add_to_list(fav_key, cid, t)
        await bot.send_message(chat_id=cid, text=f"❤️ «{t[:50]}» - в любимые, раздел «{cat}».")
    else:
        await bot.send_message(chat_id=cid, text="Удалил из закладок.")
    await send_bucket(bot, cid, "fav")

async def note_drop(bot, cid, i):
    n = _pop_note(cid, i)
    bucket = _note_bucket(n) if n else "fav"
    await bot.send_message(chat_id=cid, text="🗑 Удалил.")
    await send_bucket(bot, cid, bucket)

async def export_notes(bot, cid):
    import io
    lines = ["Мои сохранения (DM)", ""]

    # 1) Временные закладки (лента notes с bucket=fav)
    notes = store.get_list(config.NOTES_KEY, cid)
    fav = [n for n in notes if _note_bucket(n) == "fav"]
    lines.append("⭐ ВРЕМЕННЫЕ ЗАКЛАДКИ")
    if fav:
        for n in fav:
            t = n.get("text", "") if isinstance(n, dict) else str(n)
            d = n.get("date", "") if isinstance(n, dict) else ""
            src_full = n.get("source", "") if isinstance(n, dict) else ""
            src = src_full.split(" · ", 1)[1] if " · " in src_full else src_full
            tag = f" [{src}]" if src and src != "Прочее" else ""
            lines.append(f"- [{d}]{tag} {t.strip()}")
    else:
        lines.append("- пусто")
    lines.append("")

    # 2) Любимые (категорийные списки)
    lines.append("❤️ ЛЮБИМЫЕ")
    sections = [
        ("Мои страны", store.get_list(config.COUNTRIES_KEY, cid)),
        ("Мои артисты", store.get_list(config.ARTISTS_KEY, cid)),
        ("Мои книги", store.get_list(config.BOOKS_KEY, cid)),
    ]
    any_love = False
    for name, items in sections:
        names = [i if isinstance(i, str) else i.get("name", "") for i in items]
        names = [x for x in names if x]
        if names:
            any_love = True
            lines.append(f"  {name}:")
            for x in names:
                lines.append(f"  - {x}")
    if not any_love:
        lines.append("- пусто")
    lines.append("")

    buf = io.BytesIO("\n".join(lines).encode("utf-8"))
    buf.name = "moi_sohraneniya.txt"
    await bot.send_document(chat_id=cid, document=buf, filename="moi_sohraneniya.txt",
                            caption="📤 Готово. Текст можно сохранить на ваше устройство.")

async def send_notes(bot, cid):
    notes = store.get_list(config.NOTES_KEY, cid)
    n_fav = sum(1 for n in notes if _note_bucket(n) == "fav")
    n_love = (len(store.get_list(config.COUNTRIES_KEY, cid))
              + len(store.get_list(config.ARTISTS_KEY, cid))
              + len(store.get_list(config.BOOKS_KEY, cid)))
    rows = [
        [InlineKeyboardButton(f"⭐ Временные закладки ({n_fav})", callback_data="as_bucket_fav")],
        [InlineKeyboardButton("❤️ Любимые", callback_data="as_bucket_love")],
        [InlineKeyboardButton("📤 Экспорт в файл", callback_data="as_export")],
    ]
    await bot.send_message(chat_id=cid, parse_mode="HTML",
        text="В этом разделе хранятся временные закладки и любимые (артисты, фильмы, книги и т).\n\n"
             "<b>Мои сохранения</b> - выбери раздел:",
        reply_markup=InlineKeyboardMarkup(rows))

async def send_bucket(bot, cid, bucket):
    """fav - лента закладок; love - меню под-разделов (страны/артисты/книги/одежда)."""
    if bucket == "love":
        await send_love_home(bot, cid)
        return
    notes = store.get_list(config.NOTES_KEY, cid)
    items = [(i, n) for i, n in enumerate(notes) if _note_bucket(n) == "fav"]
    if not items:
        await bot.send_message(chat_id=cid, text="⭐ <b>Временные закладки</b>\n\nпусто", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="as_notes")]]))
        return
    lines = ["⭐ <b>Временные закладки</b>", ""]
    rows = []
    for i, n in items:
        t = n.get("text", "") if isinstance(n, dict) else str(n)
        d = n.get("date", "") if isinstance(n, dict) else ""
        src = n.get("source", "Прочее") if isinstance(n, dict) else "Прочее"
        lines.append(f"• {d} · {_top_cat(src)}: {t.strip()}")
        rows.append([InlineKeyboardButton(f"❌ {d} {t.strip()[:22]}", callback_data=f"as_notedel_{i}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_notes")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))


# ===== Любимые: под-разделы (страны/артисты/книги/одежда) =====
# каждый: (заголовок, тип для роутинга)
LOVE_SECTIONS = [
    ("🧳 Мои страны", "countries"),
    ("🎸 Мои артисты", "artists"),
    ("📖 Мои книги", "books"),
    ("👕 Моя одежда", "wardrobe"),
]

async def send_love_home(bot, cid):
    rows = [[InlineKeyboardButton(title, callback_data=f"as_love_{key}")] for title, key in LOVE_SECTIONS]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_notes")])
    await bot.send_message(chat_id=cid, text="❤️ <b>Любимые</b>\n\nВыбери раздел:",
                           parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

def _love_items(cid, key):
    """Возвращает список строк-названий для раздела (с авто-загрузкой дефолтов)."""
    if key == "countries":
        cur = store.get_list(config.COUNTRIES_KEY, cid)
        if not cur:
            cur = [c.strip() for c in config.VISITED.split(",") if c.strip()]
            store.set_list(config.COUNTRIES_KEY, cid, cur)
        return [c if isinstance(c, str) else c.get("name", "") for c in cur]
    if key == "artists":
        cur = store.get_list(config.ARTISTS_KEY, cid)
        if not cur:
            try:
                import json
                with open("artists.json", encoding="utf-8") as f:
                    cur = json.load(f)
                store.set_list(config.ARTISTS_KEY, cid, cur)
            except Exception:
                cur = []
        return list(cur)
    if key == "books":
        cur = store.get_list(config.BOOKS_KEY, cid)
        if not cur:
            try:
                import json
                with open("content.json", encoding="utf-8") as f:
                    cur = list(json.load(f).get("books", []))
                store.set_list(config.BOOKS_KEY, cid, cur)
            except Exception:
                cur = []
        return list(cur)
    return []

def _love_title(key):
    return {"countries": "🧳 Мои страны", "artists": "🎸 Мои артисты",
            "books": "📖 Мои книги", "wardrobe": "👕 Моя одежда"}.get(key, "Любимые")

async def send_love_section(bot, cid, key):
    if key == "wardrobe":
        import wardrobe
        await wardrobe.send_show(bot, cid)   # показ шкафа с его управлением
        return
    items = _love_items(cid, key)
    title = _love_title(key)
    lines = [f"<b>{title}</b>", ""]
    lines.append(", ".join(items) if items else "пусто")
    rows = [[InlineKeyboardButton(f"❌ {str(it)[:28]}", callback_data=f"as_lovedel_{key}_{i}")]
            for i, it in enumerate(items[:40])]
    rows.append([InlineKeyboardButton("➕ Добавить", callback_data=f"as_loveadd_{key}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_bucket_love")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))

def _love_key_of(key):
    return {"countries": config.COUNTRIES_KEY, "artists": config.ARTISTS_KEY,
            "books": config.BOOKS_KEY}.get(key)

async def love_delete(bot, cid, key, i):
    store_key = _love_key_of(key)
    if not store_key:
        await send_love_section(bot, cid, key); return
    items = store.get_list(store_key, cid)
    if i < len(items):
        items.pop(i)
        store.set_list(store_key, cid, items)
    await send_love_section(bot, cid, key)

async def love_add_start(bot, cid, key):
    store.pending_input[str(cid)] = f"loveadd_{key}"
    name = {"countries": "страну", "artists": "артиста", "books": "книгу"}.get(key, "элемент")
    await bot.send_message(chat_id=cid, text=f"Напиши {name} - добавлю в любимые.")

async def love_add_done(bot, cid, key, text):
    store_key = _love_key_of(key)
    if store_key:
        store.add_to_list(store_key, cid, text.strip())
    await bot.send_message(chat_id=cid, text="Добавлено.")
    await send_love_section(bot, cid, key)


_ONESHOT = {
    "as_idea": (_gen_idea, "🔁 Новая идея", "as_idea"),
    "as_motiv": (_gen_motiv, "🔄 Ещё", "as_motiv"),
}


# ---------- роутер кнопок ассистента ----------
async def handle_callback(bot, cid, q, data):
    # Кулинарный радар
    if data == "as_food":
        await send_recipe(bot, cid, "обычное блюдо"); return
    if data == "as_food_full":
        await send_recipe_full(bot, cid); return
    if data == "as_food_left":
        store.pending_input[str(cid)] = "leftovers"
        await bot.send_message(chat_id=cid, text="🥕 Напиши продукты, что есть дома (через запятую) - предложу 3 рецепта.",
                               reply_markup=_back_kb()); return
    # состояние-кнопки
    if data == "as_daycheck":
        await myday.send_daycheck(bot, cid); return
    # избранное
    if data == "as_fav":
        await save_fav(bot, cid); return
    if data == "as_notes":
        await send_notes(bot, cid); return
    if data == "as_bucket_fav":
        await send_bucket(bot, cid, "fav"); return
    if data == "as_bucket_love":
        await send_bucket(bot, cid, "love"); return
    if data == "as_export":
        await export_notes(bot, cid); return
    if data.startswith("as_notedel_"):
        await note_delete_menu(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_noteblack_"):
        await note_to_blacklist(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_notelove_"):
        await note_to_love(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_notedrop_"):
        await note_drop(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_lovedel_"):
        parts = data[len("as_lovedel_"):].rsplit("_", 1)
        await love_delete(bot, cid, parts[0], int(parts[1])); return
    if data.startswith("as_loveadd_"):
        await love_add_start(bot, cid, data[len("as_loveadd_"):]); return
    if data.startswith("as_love_"):
        await send_love_section(bot, cid, data[len("as_love_"):]); return
    # одноразовые
    if data in _ONESHOT:
        gen, lbl, cb = _ONESHOT[data]
        await bot.send_message(chat_id=cid, text="Секунду...")
        try:
            out = gen(cid)
        except Exception as e:
            await bot.send_message(chat_id=cid, text=str(e)); return
        store.last_action[str(cid)] = ("oneshot", data)
        store.last_source[str(cid)] = {"as_motiv": "Здоровье · Мотивация", "as_idea": "Идеи"}.get(data, "Ассистент")
        await _send(bot, cid, out, kb=_ans_kb(lbl, cb))
        return
    # роли
    if data == "as_doctor":
        store.pending_input[str(cid)] = "role_doctor"
        await bot.send_message(chat_id=cid, text=DOCTOR_INTRO, reply_markup=_back_kb()); return


# ---------- свободный чат ----------
_MED_WORDS = ("боль", "болит", "температур", "симптом", "врач", "таблет", "лекарств", "горло",
              "кашель", "тошнот", "давлен", "head", "сыпь", "простуд", "грипп", "живот")

async def chat_reply(bot, cid, text):
    store.last_action[str(cid)] = None
    store.last_source[str(cid)] = "Ассистент"
    await bot.send_chat_action(chat_id=cid, action="typing")
    hist = store.chat_history.get(str(cid), [])
    hist.append({"role": "user", "content": text})
    hist = hist[-10:]
    try:
        answer = ai.chat_chain(hist)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send_html(bot, cid, (answer or "").strip() or "Пусто, попробуй ещё раз.")
    store.last_answer[str(cid)] = answer
    if any(w in text.lower() for w in _MED_WORDS):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("👩🏻‍⚕️ Вопрос врачу", callback_data="as_doctor")]])
        await bot.send_message(chat_id=cid,
            text="👩🏻‍⚕️ Похоже на вопрос о здоровье. В разделе 🧠 Баланс → «Вопрос врачу» дам подробный структурированный разбор.",
            reply_markup=kb)


# ---------- «Продолжить» / «Ещё раз» ----------
async def retry(bot, cid):
    la = store.last_action.get(str(cid))
    if la and la[0] == "oneshot":
        gen, lbl, cb = _ONESHOT[la[1]]
        await bot.send_message(chat_id=cid, text="Ещё вариант...")
        try:
            out = gen(cid)
        except Exception as e:
            await bot.send_message(chat_id=cid, text=str(e)); return
        await _send(bot, cid, out, kb=_ans_kb(lbl, cb)); return
    if la and la[0] == "recipe":
        await send_recipe(bot, cid, la[1]); return
    if la and la[0] == "leftovers":
        await send_leftovers(bot, cid, la[1]); return
    if la and la[0] == "role":
        await handle_role(bot, cid, la[1], la[2]); return
    hist = list(store.chat_history.get(str(cid), []))
    if not hist:
        await bot.send_message(chat_id=cid, text="Нет предыдущего запроса."); return
    if hist[-1]["role"] == "assistant":
        hist = hist[:-1]
    await bot.send_chat_action(chat_id=cid, action="typing")
    nudge = hist + [{"role": "user", "content": "Продолжи мысль или дай более полезный вариант."}]
    try:
        answer = ai.chat_chain(nudge)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send(bot, cid, answer)