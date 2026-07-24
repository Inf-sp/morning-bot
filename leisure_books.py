"""Книжные рекомендации, замены, сохранение и любимые книги."""

import asyncio
import random
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import google_books
import recommendation_stoplist
import store
import tracking
from ui import leisure as leisure_ui
from ui.constants import save_toggle_label


def _item_text(item):
    if isinstance(item, dict):
        return str(item.get("value", "")).strip()
    return str(item or "").strip()


def _ensure_books(cid):
    return [_item_text(item) for item in store.get_list(config.BOOKS_KEY, cid)
            if _item_text(item)]


def _add_unique(key, cid, value):
    items = store.get_list(key, cid)
    if value and value.lower() not in {_item_text(item).lower() for item in items}:
        store.set_list(key, cid, [*items, value])


def _cached_book(cid):
    entry = (store._load(config.BOOK_RECO_CACHE_KEY) or {}).get(str(cid)) or {}
    item = entry.get("item")
    today = datetime.now(config.TZ).date().isoformat()
    if entry.get("date") != today or not isinstance(item, dict):
        return None
    title = _item_text(item)
    if not title or title.casefold() in _book_used(cid):
        return None
    return dict(item)


def _cache_book(cid, item):
    today = datetime.now(config.TZ).date().isoformat()

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[str(cid)] = {"date": today, "item": dict(item or {})}
        return data, None

    store.mutate_kv(config.BOOK_RECO_CACHE_KEY, mutate)


async def _ask_collect(bot, cid, kind):
    import leisure_collection
    return await leisure_collection._ask_collect(bot, cid, kind)


def content_recommend(kind, cid):
    import leisure_collection
    return leisure_collection.content_recommend(kind, cid)
def _book_cover(title, title_en=""):
    import requests
    timeout = 4.0
    remaining = tracking.remaining_action_seconds()
    if remaining is not None:
        if remaining <= 0.2:
            return None
        timeout = min(timeout, remaining)
    for q in [t for t in (title_en, title) if t]:
        try:
            r = requests.get("https://openlibrary.org/search.json",
                             params={"title": q, "limit": 1}, timeout=timeout)
            docs = r.json().get("docs", [])
            if docs and docs[0].get("cover_i"):
                return f"https://covers.openlibrary.org/b/id/{docs[0]['cover_i']}-L.jpg"
        except Exception:
            continue
    return None

def _book_text(it):
    return leisure_ui.book_text(it)

def _book_kb(i, saved=False, favorite=False):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Другая книга", callback_data=f"book_no_{i}")],
        [InlineKeyboardButton("❤️ Мои книги", callback_data="book_favorites"),
         InlineKeyboardButton(save_toggle_label(saved, "Сохранить"), callback_data=f"reco_{i}")],
        [InlineKeyboardButton("🎚️ Предпочтения", callback_data="book_prefs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def books_home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Подобрать книгу", callback_data="book_reco")],
        [InlineKeyboardButton("❤️ Мои книги", callback_data="book_favorites"),
         InlineKeyboardButton("💾 Сохранить", callback_data="book_saved")],
        [InlineKeyboardButton("🎚️ Предпочтения", callback_data="book_prefs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_books_home(bot, cid, q=None):
    await send_books_reco(bot, cid)


async def send_book_preferences(bot, cid, q=None):
    text = "🎚️ Предпочтения книг\n\nЖанры и формат книги можно настроить здесь."
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="a_read"),
                                InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")]])
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)

async def _send_book_card(bot, cid, it, i, *, enrich=True):
    import saved_items
    if enrich:
        try:
            remaining = tracking.remaining_action_seconds()
            timeout = min(8.0, remaining - 0.5) if remaining is not None else 8.0
            if timeout <= 0.2:
                raise asyncio.TimeoutError
            it = await asyncio.wait_for(
                asyncio.to_thread(google_books.enrich_book, it), timeout=timeout)
        except Exception:
            it = dict(it or {})
    else:
        it = dict(it or {})
    msg = _book_text(it)
    kb = _book_kb(i, saved_items.is_note_saved(cid, it.get("title", "")))
    cover = it.get("cover_url")
    if not cover:
        try:
            remaining = tracking.remaining_action_seconds()
            timeout = min(4.5, remaining - 0.5) if remaining is not None else 4.5
            if timeout <= 0.2:
                raise asyncio.TimeoutError
            cover = await asyncio.wait_for(
                asyncio.to_thread(
                    _book_cover, it.get("title", ""), it.get("title_en", "")),
                timeout=timeout,
            )
        except Exception:
            cover = None
    if cover:
        try:
            await bot.send_photo(chat_id=cid, photo=cover, caption=msg.text, caption_entities=msg.entities, reply_markup=kb)
            return it
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
    return it

_FALLBACK_BOOKS = [
    {"title": "Мастер и Маргарита", "title_en": "The Master and Margarita", "year": "1967",
     "author": "Михаил Булгаков", "desc": "Сатира, мистика и история любви в одном романе.",
     "why": ["Многослойность: дьявол в Москве, Понтий Пилат и вечная любовь сразу",
             "Из тех книг, что перечитывают всю жизнь и каждый раз видят новое"],
     "plot": "Воланд со свитой устраивает хаос в советской Москве, а параллельно разворачивается роман Мастера о Пилате и история его любви к Маргарите.",
     "quote": "Рукописи не горят.",
     "hook": "Абсолютная классика, которую стоит прочесть хотя бы раз."},
    {"title": "1984", "title_en": "1984", "year": "1949",
     "author": "Джордж Оруэлл", "desc": "Главная антиутопия XX века о тотальной слежке.",
     "why": ["Предсказала мир, в котором мы во многом живём",
             "Меняет взгляд на свободу, правду и язык"],
     "plot": "Уинстон Смит живёт в государстве, где Большой Брат следит за каждым, и пытается сохранить способность думать самостоятельно.",
     "quote": "Война - это мир. Свобода - это рабство. Незнание - сила.",
     "hook": "Если не читал - это пробел, который точно стоит закрыть."},
    {"title": "Маленький принц", "title_en": "Le Petit Prince", "year": "1943",
     "author": "Антуан де Сент-Экзюпери", "desc": "Мудрая сказка для взрослых о главном.",
     "why": ["Читается за вечер, остаётся с тобой на годы",
             "Простыми словами о любви, дружбе и смысле"],
     "plot": "Лётчик в пустыне встречает мальчика с другой планеты, и через его рассказы открываются простые истины о том, что по-настоящему важно.",
     "quote": "Мы в ответе за тех, кого приручили.",
     "hook": "Тёплая книга, которую стоит прочитать всем."},
    {"title": "Убить пересмешника", "title_en": "To Kill a Mockingbird", "year": "1960",
     "author": "Харпер Ли", "desc": "Роман о справедливости и взрослении на юге США.",
     "why": ["Учит эмпатии без морализаторства",
             "Один из главных романов о совести и предрассудках"],
     "plot": "Девочка Скаут растёт в маленьком городке, где её отец-адвокат защищает несправедливо обвинённого, и взрослеет, сталкиваясь с миром взрослых.",
     "hook": "Книга из всех списков «обязательного к прочтению»."},
    {"title": "Сто лет одиночества", "title_en": "Cien años de soledad", "year": "1967",
     "author": "Габриэль Гарсиа Маркес", "desc": "Эталон магического реализма.",
     "why": ["Завораживающий язык и целый придуманный мир",
             "Семейная сага, которую считают одной из лучших книг века"],
     "plot": "История нескольких поколений семьи Буэндиа в вымышленном городке Макондо, где обыденное и волшебное переплетены.",
     "hook": "Если хочешь большую сильную книгу - начни с неё."},
    {"title": "Преступление и наказание", "title_en": "Crime and Punishment", "year": "1866",
     "author": "Фёдор Достоевский", "desc": "Психологический роман о вине и искуплении.",
     "why": ["Заглядывает в самые тёмные уголки разума",
             "Классика, которая держит как триллер"],
     "plot": "Студент Раскольников убивает старуху-процентщицу, проверяя свою теорию, и оказывается раздавлен муками совести.",
     "hook": "Достоевский, с которого стоит начать знакомство."},
]

def _book_used(cid):
    """Названия книг, которые нельзя повторять: любимые, знакомые, закладки, отклонённые."""
    used = set()
    for key in (config.BOOKS_KEY, config.READLIST_KEY):
        for x in store.get_list(key, cid):
            title = _item_text(x)
            if title:
                used.add(title.casefold())
    used.update(value.strip().lower() for value in recommendation_stoplist.values(cid, "book"))
    return used

def _fallback_book(cid, extra_skip=()):
    """Гарантированная рекомендация: популярная must-read книга, ещё не виденная пользователем."""
    used = _book_used(cid) | {str(x).strip().lower() for x in extra_skip}
    pool = [b for b in _FALLBACK_BOOKS if b["title"].lower() not in used] or _FALLBACK_BOOKS
    return random.choice(pool)

def _pick_good_book(items, cid, extra_skip=()):
    """Первая книга из items, которой ещё нет в списках/показанных; иначе - гарантированный фолбэк."""
    used = _book_used(cid) | {str(x).strip().lower() for x in extra_skip}
    for it in items or []:
        t = (it.get("title", "") or "").strip().lower()
        if t and t not in used:
            return it
    return _fallback_book(cid, extra_skip=extra_skip)

async def get_current_book(cid):
    cached = _cached_book(cid)
    if cached:
        return cached
    items = []
    for _ in range(2):
        try:
            data = await asyncio.to_thread(content_recommend, "book", str(cid))
            items = data.get("items", []) if isinstance(data, dict) else []
        except Exception:
            items = []
        if items:
            break
    it = _pick_good_book(items, cid)
    _cache_book(cid, it)
    return it


async def send_books_reco(bot, cid):
    it = await get_current_book(cid)
    title = it.get("title", "")
    store.last_recos[str(cid)] = {"kind": "book", "items": [it.get("title", "")]}
    store.last_source[str(cid)] = "Досуг · Книги"
    store.last_answer[str(cid)] = it.get("title", "")
    prepared = await _send_book_card(bot, cid, it, 0)
    _cache_book(cid, prepared)

async def book_dislike(bot, cid, i):
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        recommendation_stoplist.add(cid, "book", title, "hidden")
    try:
        data = await asyncio.to_thread(content_recommend, "book", str(cid))
        items = data.get("items", [])
    except Exception:
        items = []
    rec = store.last_recos.get(str(cid), {"kind": "book", "items": []})
    it = _pick_good_book(items, cid, extra_skip=rec.get("items", []))
    rec["items"].append(it.get("title", ""))
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    prepared = await _send_book_card(bot, cid, it, ni)
    _cache_book(cid, prepared)

async def _advance_book(bot, cid):
    """Загрузить следующую рекомендацию книги и показать карточку."""
    try:
        data = await asyncio.to_thread(content_recommend, "book", str(cid))
        items = data.get("items", [])
    except Exception:
        items = []
    rec = store.last_recos.get(str(cid), {"kind": "book", "items": []})
    it = _pick_good_book(items, cid, extra_skip=rec.get("items", []))
    rec["items"].append(it.get("title", ""))
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    prepared = await _send_book_card(bot, cid, it, ni)
    _cache_book(cid, prepared)

async def book_love(bot, cid, i, q=None):
    """Добавляет книгу в любимые без дублей и отражает состояние на карточке."""
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        _add_unique(config.BOOKS_KEY, cid, title)
        if q is not None:
            import saved_items
            await q.message.edit_reply_markup(
                reply_markup=_book_kb(i, saved=saved_items.is_note_saved(cid, title), favorite=True))
