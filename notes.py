from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai


# ---------- сохранение в закладки ----------
def _shorten(text):
    try:
        return ai.llm("Сожми до 1-3 строк, сохрани суть и важное, без воды:\n\n" + text, 200, 0.3, tier="cheap").strip() or text[:300]
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

    # 2) Планы поездок
    plans = [n for n in notes if _note_bucket(n) == "plan"]
    lines.append("🧳 ПЛАНЫ ПОЕЗДОК")
    if plans:
        for n in plans:
            d = n.get("date", "") if isinstance(n, dict) else ""
            country = (n.get("country") or "") if isinstance(n, dict) else ""
            lines.append(f"- [{d}] {country}")
    else:
        lines.append("- пусто")
    lines.append("")

    # 3) Любимые (категорийные списки)
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
    n_plan = sum(1 for n in notes if _note_bucket(n) == "plan")
    rows = [
        [InlineKeyboardButton(f"⭐ Временные закладки ({n_fav})", callback_data="as_bucket_fav")],
        [InlineKeyboardButton(f"🧳 Планы ({n_plan})", callback_data="as_bucket_plan")],
        [InlineKeyboardButton("📤 Экспорт в файл", callback_data="as_export")],
    ]
    await bot.send_message(chat_id=cid, parse_mode="HTML",
        text="💾 <b>Мои сохранения</b>\n\nЗакладки, планы поездок, фильмы, книги и артисты.\n\nВыбери раздел 👇",
        reply_markup=InlineKeyboardMarkup(rows))

async def send_plans(bot, cid):
    """Вкладка «Планы» - сохранённые планы поездок (bucket=plan)."""
    notes = store.get_list(config.NOTES_KEY, cid)
    items = [(i, n) for i, n in enumerate(notes) if _note_bucket(n) == "plan"]
    if not items:
        await bot.send_message(chat_id=cid, text="🧳 <b>Планы</b>\n\nпусто", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="as_notes")]]))
        return
    rows = []
    for i, n in items:
        country = (n.get("country") or "План") if isinstance(n, dict) else "План"
        d = n.get("date", "") if isinstance(n, dict) else ""
        rows.append([InlineKeyboardButton(f"🧳 {d} · {country}"[:40], callback_data=f"as_planview_{i}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_notes")])
    await bot.send_message(chat_id=cid, parse_mode="HTML",
        text="🧳 <b>Планы</b>\n\nСохранённые планы поездок.\n\nВыбери план 👇",
        reply_markup=InlineKeyboardMarkup(rows))

async def plan_view(bot, cid, i):
    notes = store.get_list(config.NOTES_KEY, cid)
    if i >= len(notes) or _note_bucket(notes[i]) != "plan":
        await send_plans(bot, cid); return
    n = notes[i]
    text = n.get("text", "") if isinstance(n, dict) else str(n)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Удалить план", callback_data=f"as_plandel_{i}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="as_bucket_plan")],
    ])
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)

async def send_bucket(bot, cid, bucket):
    """fav - лента закладок; plan - планы поездок; love - меню под-разделов."""
    if bucket == "love":
        await send_love_home(bot, cid)
        return
    if bucket == "plan":
        await send_plans(bot, cid)
        return
    notes = store.get_list(config.NOTES_KEY, cid)
    items = [(i, n) for i, n in enumerate(notes) if _note_bucket(n) == "fav"]
    count = len(items)
    txt = (
        "⭐ <b>Временные закладки</b>\n\n"
        "Сюда попадает всё, что ты сохранил кнопкой «⭐ В закладки» — "
        "советы, образы, рецепты, цитаты. Удобно держать под рукой и удалять, когда уже не нужно.\n\n"
        f"Сохранено: <b>{count}</b>"
    )
    rows = []
    for i, n in items:
        t = n.get("text", "") if isinstance(n, dict) else str(n)
        d = n.get("date", "") if isinstance(n, dict) else ""
        src = n.get("source", "Прочее") if isinstance(n, dict) else "Прочее"
        label = f"{_top_cat(src)} · {t.strip()[:30]}"
        rows.append([InlineKeyboardButton(f"❌ {label}", callback_data=f"as_notedel_{i}")])
    if count > 2:
        rows.append([InlineKeyboardButton("🧹 Убрать несколько", callback_data="as_clean_fav")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_notes")])
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))


# ===== Любимые: под-разделы (страны/артисты/книги/одежда) =====
# каждый: (заголовок, тип для роутинга)
LOVE_SECTIONS = [
    ("🧳 Мои страны", "countries"),
    ("🎸 Мои артисты", "artists"),
    ("📖 Мои книги", "books"),
    ("🍳 Мои рецепты", "recipes"),
]

async def send_love_home(bot, cid):
    rows = [[InlineKeyboardButton(title, callback_data=f"as_love_{key}")] for title, key in LOVE_SECTIONS]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_home")])
    await bot.send_message(chat_id=cid, text="❤️ <b>Любимые</b>\n\nТвои топ-категории.\n\nВыбери раздел 👇",
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
            "books": "📖 Мои книги"}.get(key, "Любимые")

async def send_love_section(bot, cid, key):
    if key == "recipes":
        import balance
        await balance.send_my_recipes(bot, cid)
        return
    items = _love_items(cid, key)
    title = _love_title(key)
    lines = [f"<b>{title}</b>", "", (", ".join(items) if items else "пусто")]
    rows = [[InlineKeyboardButton(f"❌ {str(it)[:28]}", callback_data=f"as_lovedel_{key}_{i}")]
            for i, it in enumerate(items[:40])]
    rows.append([InlineKeyboardButton("📝 Добавить", callback_data=f"as_loveadd_{key}")])
    if items:
        rows.append([InlineKeyboardButton("🧹 Убрать несколько", callback_data=f"as_loveclean_{key}")])
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


# ---------- роутер кнопок закладок/любимого ----------
async def handle_callback(bot, cid, q, data):
    if data == "as_fav":
        await save_fav(bot, cid); return
    if data == "as_notes":
        await send_notes(bot, cid); return
    if data == "as_bucket_fav":
        await send_bucket(bot, cid, "fav"); return
    if data == "as_bucket_plan":
        await send_bucket(bot, cid, "plan"); return
    if data == "as_bucket_love":
        await send_bucket(bot, cid, "love"); return
    if data.startswith("as_planview_"):
        await plan_view(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_plandel_"):
        await note_drop(bot, cid, int(data.split("_")[-1])); return
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
    if data == "as_clean_fav":
        import cleanup
        await cleanup.open_cleanup(bot, cid, "nb"); return
    if data.startswith("as_loveclean_"):
        import cleanup
        await cleanup.open_cleanup(bot, cid, f"lv_{data[len('as_loveclean_'):]}"); return
    if data.startswith("as_lovedel_"):
        parts = data[len("as_lovedel_"):].rsplit("_", 1)
        await love_delete(bot, cid, parts[0], int(parts[1])); return
    if data.startswith("as_loveadd_"):
        await love_add_start(bot, cid, data[len("as_loveadd_"):]); return
    if data.startswith("as_love_"):
        await send_love_section(bot, cid, data[len("as_love_"):]); return
