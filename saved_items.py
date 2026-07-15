"""Сохранённые карточки, планы, любимое и пользовательские коллекции."""

import logging
import re
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import store
import util
from ui import settings as settings_ui
from ui.constants import delete_label, ui_label

_log = logging.getLogger(__name__)
# ===== СОХРАНЕНИЯ / ЛЮБИМЫЕ (notes.py) =====


def _mark_transient_edit(bot, cid, message):
    marker = getattr(bot, "mark_transient_message", None)
    if marker is not None:
        marker(cid, getattr(message, "message_id", None))

async def save_fav(bot, cid, q=None):
    # Берём оригинальный текст сообщения прямо из callback — entities уже структурированы
    # Telegram-ом (Message.entities/caption_entities), без похода через HTML-строку.
    txt, txt_entities = "", []
    if q is not None and q.message:
        txt = q.message.text or q.message.caption or ""
        txt_entities = list(q.message.entities or q.message.caption_entities or [])
    if not txt:
        txt = store.last_answer.get(str(cid), "")
        txt_entities = []
    if not txt:
        msg = settings_ui.nothing_to_save()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities); return
    source = store.last_source.get(str(cid), "Прочее")
    store.add_to_list(config.NOTES_KEY, cid, {
        "date": datetime.now(config.TZ).strftime("%d.%m"),
        "text": txt, "entities": util.entities_to_json(txt_entities),
        "source": source, "bucket": "fav",
    })
    msg = settings_ui.saved_to_later()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)

def _note_type(source):
    s = (source or "").lower()
    if "фильм" in s or "сериал" in s or "кино" in s:
        return ("movie", config.MOVIE_BLACKLIST_KEY, config.WATCHLIST_KEY, "Кино")
    if "книг" in s:
        return ("book", config.BOOK_BLACKLIST_KEY, config.BOOKS_KEY, "Книги")
    if "музык" in s or "концерт" in s:
        return ("music", config.MUSIC_DISLIKE_KEY, config.ARTISTS_KEY, "Артисты")
    if "путешеств" in s or "стран" in s:
        return ("travel", config.TRAVEL_DISLIKE_KEY, config.FAVCOUNTRIES_KEY, "Страны")
    return (None, None, None, None)

def _note_bucket(n):
    return n.get("bucket", "fav") if isinstance(n, dict) else "fav"

def _fav_group(source: str) -> str:
    s = (source or "").lower()
    if "фильм" in s or "сериал" in s or "кино" in s:
        return "movies"
    if "книг" in s:
        return "books"
    if "музык" in s or "концерт" in s:
        return "music"
    if "путешеств" in s or "стран" in s:
        return "travel"
    if "гардероб" in s or "образ" in s or "покупк" in s:
        return "wardrobe"
    if "питан" in s or "рецепт" in s or "ед" in s or "холодиль" in s:
        return "food"
    if "здоров" in s or "мотивац" in s or "врач" in s or "тревог" in s or "баланс" in s:
        return "health"
    return "other"

def _fav_group_meta():
    return [
        ("movies", ui_label("cinema", "Кино"), "фильмы и сериалы"),
        ("books", ui_label("books", "Книги"), "книги и списки к прочтению"),
        ("music", ui_label("music", "Музыка"), "музыка, артисты и концерты"),
        ("travel", ui_label("travel", "Поездки"), "страны и поездки"),
        ("food", ui_label("recipes", "Еда"), "рецепты и питание"),
        ("wardrobe", ui_label("wardrobe", "Гардероб"), "образы и покупки"),
        ("health", ui_label("health", "Здоровье"), "здоровье и мотивация"),
        ("other", "Прочее", "всё, что не попало в отдельную категорию"),
    ]

def _fav_group_info(key: str):
    for group_key, label, desc in _fav_group_meta():
        if group_key == key:
            return label, desc
    return "Прочее", "всё, что не попало в отдельную категорию"

def _pop_note(cid, i):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    if i >= len(notes_list):
        return None
    n = notes_list.pop(i)
    store.set_list(config.NOTES_KEY, cid, notes_list)
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
        msg = settings_ui.note_blacklisted(t, cat)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    else:
        msg = settings_ui.note_removed_from_later()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
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
        msg = settings_ui.note_moved_to_favorites(t, cat)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    else:
        msg = settings_ui.note_removed_from_later()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    await send_bucket(bot, cid, "fav")

async def note_drop(bot, cid, i):
    n = _pop_note(cid, i)
    bucket = _note_bucket(n) if n else "fav"
    msg = settings_ui.note_deleted()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    await send_bucket(bot, cid, bucket)

async def export_notes(bot, cid):
    import io, re as _re2
    _plain = lambda s: _re2.sub(r"<[^>]+>", "", s).strip()
    lines = ["Мои сохранения (DM)", ""]

    notes_list = store.get_list(config.NOTES_KEY, cid)
    fav = [n for n in notes_list if _note_bucket(n) == "fav"]
    lines.append("⏳ ВРЕМЕННЫЕ ЗАКЛАДКИ")
    if fav:
        for n in fav:
            t = _plain(n.get("text", "") if isinstance(n, dict) else str(n))
            d = n.get("date", "") if isinstance(n, dict) else ""
            src_full = n.get("source", "") if isinstance(n, dict) else ""
            src = src_full.split(" · ", 1)[1] if " · " in src_full else src_full
            tag = f" [{src}]" if src and src != "Прочее" else ""
            lines.append(f"- [{d}]{tag} {t}")
    else:
        lines.append("- пусто")
    lines.append("")

    plans = [n for n in notes_list if _note_bucket(n) == "plan"]
    lines.append(f"{ui_label('travel', '')} ПЛАНЫ ПОЕЗДОК")
    if plans:
        for n in plans:
            d = n.get("date", "") if isinstance(n, dict) else ""
            country = (n.get("country") or "") if isinstance(n, dict) else ""
            lines.append(f"- [{d}] {country}")
    else:
        lines.append("- пусто")
    lines.append("")

    lines.append("❤️ ЛЮБИМЫЕ")
    sections = [
        ("Мои страны", store.get_list(config.COUNTRIES_KEY, cid)),
        ("Мои музыканты", store.get_list(config.ARTISTS_KEY, cid)),
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
    rows = [
        [InlineKeyboardButton("🌍 Город", callback_data="set_city")],
        [InlineKeyboardButton(ui_label("broadcasts", "Уведомления"), callback_data="set_notif")],
        [InlineKeyboardButton("❤️ Любимое", callback_data="as_love")],
        [InlineKeyboardButton("⭐️ Сохранённое", callback_data="as_bucket_fav")],
        [InlineKeyboardButton("🔄 Обновить базу", callback_data="set_refresh_data")],
        [InlineKeyboardButton("📤 Экспорт данных", callback_data="as_export")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_menu"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ]
    msg = settings_ui.settings_home()
    await bot.send_message(chat_id=cid, entities=msg.entities,
        text=msg.text,
        reply_markup=InlineKeyboardMarkup(rows), transient=True)


async def send_mydata_leisure(bot, cid, back="m_leisure"):
    rows = [
        [InlineKeyboardButton(ui_label("cinema", "Кино"), callback_data="set_mydata_cinema")],
        [InlineKeyboardButton(ui_label("books", "Книги"), callback_data="set_mydata_books")],
        [InlineKeyboardButton(ui_label("music", "Музыка"), callback_data="set_mydata_music")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ]
    msg = settings_ui.mydata_section(
        f"{ui_label('leisure', 'Досуг')}",
        "Наполни любимое — рекомендации станут точнее.",
    )
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows), transient=True)


async def send_mydata_cinema(bot, cid):
    rows = [
        [InlineKeyboardButton("Любимое", callback_data="colr:cinema_favorites:set_mydata_leisure")],
        [InlineKeyboardButton("⭐️ Сохранённое", callback_data="colr:cinema_saved:set_mydata_leisure")],
        [InlineKeyboardButton("Смотрел", callback_data="colr:cinema_watched:set_mydata_leisure")],
        [InlineKeyboardButton("Скрытое", callback_data="colr:cinema_hidden:set_mydata_leisure")],
        [InlineKeyboardButton("Предпочтения", callback_data="movie_prefs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_mydata_leisure"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ]
    msg = settings_ui.mydata_section(f"{ui_label('cinema', 'Кино')}")
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows), transient=True)


async def send_mydata_books(bot, cid):
    rows = [
        [InlineKeyboardButton("Любимое", callback_data="colr:books_favorites:set_mydata_leisure")],
        [InlineKeyboardButton("⭐️ Сохранённое", callback_data="colr:books_saved:set_mydata_leisure")],
        [InlineKeyboardButton("Прочитано", callback_data="colr:books_read:set_mydata_leisure")],
        [InlineKeyboardButton("Скрытое", callback_data="colr:books_hidden:set_mydata_leisure")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_mydata_leisure"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ]
    msg = settings_ui.mydata_section(f"{ui_label('books', 'Книги')}")
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows), transient=True)


async def send_mydata_music(bot, cid):
    rows = [
        [InlineKeyboardButton("Любимые артисты", callback_data="colr:music_favorite_artists:set_mydata_leisure")],
        [InlineKeyboardButton("Скрытые артисты", callback_data="colr:music_hidden_artists:set_mydata_leisure")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_mydata_leisure"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ]
    msg = settings_ui.mydata_section(f"{ui_label('music', 'Музыка')}")
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows), transient=True)


async def send_food(bot, cid, q=None, back="m_food"):
    # Ленивый импорт сохраняет направление зависимостей: settings импортирует этот
    # модуль для экранов данных, поэтому импортировать settings на верхнем уровне нельзя.
    import settings as _settings
    cuisine_mark = " ✅" if _settings.cuisines(cid) else ""
    rows = [
        [InlineKeyboardButton(ui_label("products", "Продукты"), callback_data="set_fridge")],
        [InlineKeyboardButton(ui_label("recipes", "Рецепты"), callback_data="set_myrecipes")],
        [InlineKeyboardButton(f"{ui_label('cuisines', 'Кухни')}{cuisine_mark}", callback_data="set_cuisines")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ]
    msg = settings_ui.mydata_section(
        f"{ui_label('food', 'Готовка')}",
        "Продукты в холодильнике и сохранённые рецепты.",
    )
    text, entities = msg.text, msg.entities
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(text, entities=entities, reply_markup=kb)
            _mark_transient_edit(bot, cid, q.message)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, entities=entities,
                           reply_markup=kb, transient=True)


async def send_travel(bot, cid):
    rows = [
        [InlineKeyboardButton("🧳 Посещённые страны", callback_data="colr:travel_favorite_countries:set_travel")],
        [InlineKeyboardButton("⭐️ Сохранённые места", callback_data="colr:travel_saved_places:set_travel")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_travel"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ]
    msg = settings_ui.mydata_section(
        f"{ui_label('travel', 'Поездки')}",
        "Страны — для идей поездок. Места — то, что уже сохранил.",
    )
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows), transient=True)


async def send_plans(bot, cid):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    items = [(i, n) for i, n in enumerate(notes_list) if _note_bucket(n) == "plan"]
    if not items:
        msg = settings_ui.trips_empty()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="as_bucket_fav"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")]]))
        return
    rows = []
    for i, n in items:
        country = (n.get("country") or "Поездка") if isinstance(n, dict) else "Поездка"
        d = n.get("date", "") if isinstance(n, dict) else ""
        rows.append([InlineKeyboardButton(f"{ui_label('travel', '').strip()} {d} · {country}"[:40], callback_data=f"as_planview_{i}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_bucket_fav"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    msg = settings_ui.trips_home()
    await bot.send_message(chat_id=cid, entities=msg.entities,
        text=msg.text,
        reply_markup=InlineKeyboardMarkup(rows))

async def plan_view(bot, cid, i):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    if i >= len(notes_list) or _note_bucket(notes_list[i]) != "plan":
        await send_plans(bot, cid); return
    n = notes_list[i]
    text = n.get("text", "") if isinstance(n, dict) else str(n)
    entities = util.entities_from_json(n.get("entities") if isinstance(n, dict) else None)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(delete_label("Удалить план"), callback_data=f"as_plandel_{i}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="as_bucket_plan"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    chunks = util.chunk_text_with_entities(text, entities, 4000)
    for idx, (chunk_text, chunk_entities) in enumerate(chunks):
        markup = kb if idx == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id=cid, text=chunk_text, entities=chunk_entities, reply_markup=markup)
        except Exception:
            await bot.send_message(chat_id=cid, text=chunk_text, reply_markup=markup)

async def fav_view(bot, cid, i, back="as_bucket_fav", delete_cb=None):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    if i >= len(notes_list) or _note_bucket(notes_list[i]) != "fav":
        await send_bucket(bot, cid, "fav"); return
    n = notes_list[i]
    text = (n.get("text", "") if isinstance(n, dict) else str(n)).rstrip()
    body_entities = util.entities_from_json(n.get("entities") if isinstance(n, dict) else None)
    src = n.get("source", "") if isinstance(n, dict) else ""
    d = n.get("date", "") if isinstance(n, dict) else ""
    full = settings_ui.favorite_card(src, d, text, body_entities)
    typ, _, _, _ = _note_type(src)
    if typ:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❤️ В любимые", callback_data=f"as_notelove_{i}"),
             InlineKeyboardButton("Скрыть", callback_data=f"as_noteblack_{i}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
        ])
    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(delete_label("Удалить"), callback_data=delete_cb or f"fav_del_{i}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
        ])
    chunks = util.chunk_text_with_entities(full.text, full.entities, 4000)
    for idx, (chunk_text, chunk_entities) in enumerate(chunks):
        markup = kb if idx == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id=cid, text=chunk_text, entities=chunk_entities, reply_markup=markup)
        except Exception:
            await bot.send_message(chat_id=cid, text=chunk_text, reply_markup=markup)


async def fav_del(bot, cid, i):
    _pop_note(cid, i)
    await send_bucket(bot, cid, "fav")


async def fav_del_group(bot, cid, group, i):
    _pop_note(cid, i)
    await send_fav_group(bot, cid, group)


async def send_fav_group(bot, cid, group):
    notes_list = store.get_list(config.NOTES_KEY, cid)
    items = []
    for i, n in enumerate(notes_list):
        if _note_bucket(n) != "fav":
            continue
        src = n.get("source", "Прочее") if isinstance(n, dict) else "Прочее"
        if _fav_group(src) == group:
            items.append((i, n))

    label, desc = _fav_group_info(group)
    msg = settings_ui.later_group(label, desc)
    rows = []
    import re as _re
    _strip_html = lambda s: _re.sub(r"<[^>]+>", "", s).strip()
    for i, n in items:
        src = (n.get("source", "Прочее") if isinstance(n, dict) else "Прочее") or "Прочее"
        date = (n.get("date", "") if isinstance(n, dict) else "") or ""
        raw = (n.get("text", "") if isinstance(n, dict) else str(n)).strip()
        preview = _strip_html(raw)
        short = preview[:34] + ("…" if len(preview) > 34 else "")
        prefix = f"{date} · " if date else ""
        rows.append([InlineKeyboardButton(f"{prefix}{src} · {short}"[:60], callback_data=f"fav_viewg_{group}_{i}")])
    if items:
        rows.append([InlineKeyboardButton(delete_label("Убрать из сохранённого"), callback_data=f"as_clean_favgrp_{group}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_bucket_fav"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))


async def send_bucket(bot, cid, bucket):
    if bucket == "love":
        await send_love_home(bot, cid); return
    if bucket == "plan":
        await send_plans(bot, cid); return
    notes_list = store.get_list(config.NOTES_KEY, cid)
    items = [(i, n) for i, n in enumerate(notes_list) if _note_bucket(n) == "fav"]
    count = len(items)
    if not count:
        msg = settings_ui.later_home_empty()
        rows = [
            [InlineKeyboardButton(ui_label("travel", "Мои поездки"), callback_data="as_bucket_plan")],
            [InlineKeyboardButton(ui_label("cinema", "Кино"), callback_data="as_bucket_favgrp_movies"),
             InlineKeyboardButton(ui_label("books", "Книги"), callback_data="as_bucket_favgrp_books")],
            [InlineKeyboardButton(ui_label("music", "Музыка"), callback_data="as_bucket_favgrp_music"),
             InlineKeyboardButton(ui_label("travel", "Поездки"), callback_data="as_bucket_favgrp_travel")],
            [InlineKeyboardButton(ui_label("recipes", "Еда"), callback_data="as_bucket_favgrp_food"),
             InlineKeyboardButton(ui_label("wardrobe", "Гардероб"), callback_data="as_bucket_favgrp_wardrobe")],
            [InlineKeyboardButton(ui_label("health", "Здоровье"), callback_data="as_bucket_favgrp_health"),
             InlineKeyboardButton("Прочее", callback_data="as_bucket_favgrp_other")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="as_notes"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
        ]
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                               reply_markup=InlineKeyboardMarkup(rows)); return
    groups = {key: [] for key, _, _ in _fav_group_meta()}
    for idx, n in items:
        src = n.get("source", "Прочее") if isinstance(n, dict) else "Прочее"
        groups[_fav_group(src)].append((idx, n))

    msg = settings_ui.later_home()
    rows = []
    for key, label, desc in _fav_group_meta():
        if groups.get(key):
            rows.append([InlineKeyboardButton(f"{label} ({len(groups[key])})", callback_data=f"as_bucket_favgrp_{key}")])
    rows.append([InlineKeyboardButton(ui_label("travel", "Мои поездки"), callback_data="as_bucket_plan")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_notes"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows))


LOVE_SECTIONS = [
    (ui_label("cinema", "Кино"), "movies"),
    ("🧳 Посещённые страны", "countries"),
    (ui_label("music", "Мои музыканты"), "artists"),
    (ui_label("books", "Мои книги"), "books"),
    (ui_label("recipes", "Рецепты"), "recipes"),
]

async def send_love_home(bot, cid, back="m_notes"):
    rows = [[InlineKeyboardButton(title, callback_data=f"as_love_{key}")] for title, key in LOVE_SECTIONS]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    msg = settings_ui.favorites_home()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))

def _love_items(cid, key):
    if key == "movies":
        return list(store.get_list(config.WATCHLIST_KEY, cid))
    if key == "countries":
        cur = store.get_list(config.FAVCOUNTRIES_KEY, cid)
        return [c if isinstance(c, str) else c.get("name", "") for c in cur]
    if key == "artists":
        return list(store.get_list(config.ARTISTS_KEY, cid))
    if key == "books":
        return list(store.get_list(config.BOOKS_KEY, cid))
    return []

def _love_title(key):
    return {
        "movies": ui_label("cinema", "Мое кино"),
        "countries": "🧳 Посещённые страны",
        "artists": ui_label("music", "Мои музыканты"),
        "books": ui_label("books", "Мои книги"),
    }.get(key, "Любимые")

_HIDDEN_SUPPORTED = {"movies", "books", "artists", "countries"}
_LOVE_ADD_LABEL = {
    "movies": "🆕 Добавить фильм",
    "countries": "🆕 Добавить страну",
    "artists": "🆕 Добавить артиста",
    "books": "🆕 Добавить книгу",
}

async def send_love_section(bot, cid, key):
    if key == "recipes":
        import cooking
        import saved_recipes
        await saved_recipes.send_my_recipes(bot, cid, back="as_love")
        return
    items = _love_items(cid, key)
    title = _love_title(key)
    msg = settings_ui.favorite_section(title, items)
    rows = [[InlineKeyboardButton(
        _LOVE_ADD_LABEL.get(key, "🆕 Добавить объект"),
        callback_data=f"as_loveadd_{key}",
    )]]
    if items:
        rows.append([InlineKeyboardButton(delete_label("Убрать из любимого"), callback_data=f"as_loveclean_{key}")])
    if key in _HIDDEN_SUPPORTED:
        rows.append([InlineKeyboardButton("Скрытое", callback_data=f"as_lovehidden_{key}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_notes"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows))

def _love_key_of(key):
    return {"movies": config.WATCHLIST_KEY, "countries": config.FAVCOUNTRIES_KEY,
            "artists": config.ARTISTS_KEY, "books": config.BOOKS_KEY}.get(key)

async def love_add_start(bot, cid, key, origin="base"):
    prefix = "loveaddls" if origin == "leisure" else "loveadd"
    store.pending_input[str(cid)] = f"{prefix}_{key}"
    name = {"movies": "фильм или сериал", "countries": "страну",
            "artists": "артиста", "books": "книгу"}.get(key, "элемент")
    msg = settings_ui.favorite_add_prompt(name)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)

async def love_add_done(bot, cid, key, text, origin="base"):
    store_key = _love_key_of(key)
    items = [x.strip() for x in re.split(r"[,;\n]+", text or "") if x.strip()]
    if store_key and key == "countries":
        from util import country_flag
        for name in items:
            store.add_to_list(store_key, cid, {"name": name, "flag": country_flag(name)})
    elif store_key:
        for item in items:
            store.add_to_list(store_key, cid, item)
    if key == "artists" and items:
        import leisure_music
        leisure_music._kick_off_new_artist_concert_check(cid, items)
    import cleanup as _cl
    msg = settings_ui.favorite_added()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    ctx_prefix = "lvls" if origin == "leisure" else "lv"
    await _cl.open_cleanup(bot, cid, f"{ctx_prefix}_{key}",
                           back="set_mydata_leisure" if origin == "leisure" else "as_notes")


async def handle_notes_callback(bot, cid, q, data):
    """Роутер для callback'ов закладок/любимого (as_* и fav_*)."""
    if data == "as_fav":
        await save_fav(bot, cid, q); return
    if data == "as_notes":
        await send_notes(bot, cid); return
    if data == "as_bucket_fav":
        await send_bucket(bot, cid, "fav"); return
    if data.startswith("as_bucket_favgrp_"):
        await send_fav_group(bot, cid, data[len("as_bucket_favgrp_"):]); return
    if data.startswith("as_clean_favgrp_"):
        import cleanup
        await cleanup.open_cleanup(bot, cid, f"nb_{data[len('as_clean_favgrp_'):]}")
        return
    if data == "as_bucket_plan":
        await send_bucket(bot, cid, "plan"); return
    if data.startswith("as_planview_"):
        await plan_view(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_plandel_"):
        await note_drop(bot, cid, int(data.split("_")[-1])); return
    if data == "as_export":
        await export_notes(bot, cid); return
    if data.startswith("as_noteblack_"):
        await note_to_blacklist(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_notelove_"):
        await note_to_love(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("fav_viewg_"):
        group, idx = data[len("fav_viewg_"):].rsplit("_", 1)
        await fav_view(bot, cid, int(idx), back=f"as_bucket_favgrp_{group}", delete_cb=f"fav_delg_{group}_{idx}")
        return
    if data.startswith("fav_del_"):
        await fav_del(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("fav_delg_"):
        group, idx = data[len("fav_delg_"):].rsplit("_", 1)
        await fav_del_group(bot, cid, group, int(idx))
        return
    if data == "as_clean_fav":
        import cleanup
        await cleanup.open_cleanup(bot, cid, "nb"); return
    if data.startswith("ls_loveadd_"):
        await love_add_start(bot, cid, data[len("ls_loveadd_"):], origin="leisure"); return
    if data.startswith("as_loveclean_"):
        import cleanup
        await cleanup.open_cleanup(bot, cid, f"lv_{data[len('as_loveclean_'):]}", back="as_notes"); return
    if data.startswith("as_lovehidden_"):
        import cleanup
        await cleanup.open_cleanup(bot, cid, f"hid_{data[len('as_lovehidden_'):]}", back="as_notes"); return
    if data == "as_love":
        await send_love_home(bot, cid); return
    if data.startswith("as_loveadd_"):
        await love_add_start(bot, cid, data[len("as_loveadd_"):]); return
    if data.startswith("as_love_"):
        key = data[len("as_love_"):]
        if key == "recipes":
            await send_love_section(bot, cid, "recipes"); return
        import cleanup as _cl
        await _cl.open_cleanup(bot, cid, f"lv_{key}", back="as_notes"); return
