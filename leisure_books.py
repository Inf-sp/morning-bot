"""Книжные рекомендации, замены и любимые книги."""

import asyncio
import random
from datetime import date, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import google_books
import recommendation_stoplist
import settings
import store
import tracking
from ui import leisure as leisure_ui


_BOOK_GENRES = [
    ("fantasy", "🧙 Фэнтези", "Fantasy"),
    ("scifi", "🚀 Фантастика", "Science fiction"),
    ("detective", "🔍 Детектив", "Mystery & Detective"),
    ("thriller", "😱 Триллер", "Thrillers"),
    ("romance", "💕 Романтика", "Romance"),
    ("history", "🏛 История", "History"),
    ("biography", "👤 Биографии", "Biography & Autobiography"),
    ("psychology", "🧠 Психология", "Psychology"),
]
_PREF_RECENCY = [("Новинки", "new"), ("Любые годы", "")]
_PREF_RATING = [("3.5", "3.5"), ("4.0", "4.0"), ("4.5", "4.5")]


def _item_text(item):
    if isinstance(item, dict):
        return str(item.get("value", "")).strip()
    return str(item or "").strip()


def _add_unique(key, cid, value):
    items = store.get_list(key, cid)
    if value and value.casefold() not in {_item_text(item).casefold() for item in items}:
        store.set_list(key, cid, [*items, value])


def _cached_book(cid):
    entry = (store._load(config.BOOK_RECO_CACHE_KEY) or {}).get(str(cid)) or {}
    item = entry.get("item")
    today = datetime.now(config.TZ).date().isoformat()
    if (entry.get("date") != today or not isinstance(item, dict)
            or entry.get("preferences") != _book_preferences(cid)):
        return None
    title = _item_text(item)
    if not title or title.casefold() in _book_used(cid):
        return None
    return dict(item)


def _cache_book(cid, item):
    today = datetime.now(config.TZ).date().isoformat()

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[str(cid)] = {
            "date": today,
            "item": dict(item or {}),
            "preferences": _book_preferences(cid),
        }
        return data, None

    store.mutate_kv(config.BOOK_RECO_CACHE_KEY, mutate)


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


def _book_preferences(cid):
    return {
        "recency": settings.get(cid, "book_recency", "") or None,
        "min_rating": _as_float(settings.get(cid, "book_min_rating", None)),
    }


def _as_float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _book_matches_preferences(item, cid):
    preferences = _book_preferences(cid)
    min_rating = preferences["min_rating"]
    rating = _as_float(item.get("rating"))
    if min_rating is not None and (rating is None or rating < min_rating):
        return False
    if preferences["recency"] == "new":
        year = str(item.get("year") or item.get("published_date") or "")[:4]
        if not year.isdigit() or int(year) < datetime.now(config.TZ).year - 1:
            return False
    return True


def _book_kb(i):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Другая книга", callback_data=f"book_no_{i}")],
        [InlineKeyboardButton("🎭 По жанру", callback_data="book_genre_menu")],
        [InlineKeyboardButton("🎚️ Мои книги", callback_data="book_favorites")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def books_home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Подобрать книгу", callback_data="book_reco")],
        [InlineKeyboardButton("🎭 По жанру", callback_data="book_genre_menu")],
        [InlineKeyboardButton("🎚️ Мои книги", callback_data="book_favorites")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_books_home(bot, cid, q=None):
    items = await get_weekly_new_books()
    msg = leisure_ui.weekly_books_screen(items)
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=books_home_keyboard(),
    )


def _book_week_key() -> str:
    current = datetime.now(config.TZ).date()
    year, week, _weekday = current.isocalendar()
    return f"{year}-W{week:02d}"


def _weekly_book_cache_get():
    entry = store._load(config.BOOK_WEEKLY_CACHE_KEY)
    if (not isinstance(entry, dict) or entry.get("week") != _book_week_key()
            or entry.get("date") != datetime.now(config.TZ).date().isoformat()):
        return None
    items = entry.get("items")
    return [dict(item) for item in items] if isinstance(items, list) else []


def _weekly_book_cache_set(items):
    store._save(config.BOOK_WEEKLY_CACHE_KEY, {
        "week": _book_week_key(),
        "date": datetime.now(config.TZ).date().isoformat(),
        "items": [dict(item) for item in (items or []) if isinstance(item, dict)],
    })


def _released_this_week(value: str) -> bool:
    try:
        released = date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return False
    today = datetime.now(config.TZ).date()
    week_start = today - timedelta(days=today.weekday())
    return week_start <= released <= week_start + timedelta(days=6)


def _weekly_book_score(item):
    try:
        rating = float(item.get("rating") or 0)
        ratings_count = int(item.get("ratings_count") or 0)
    except (TypeError, ValueError):
        return None
    # Релиз может быть новым, но его пока никто не знает. В витрине нужны
    # только книги с уже заметным читательским откликом.
    if rating < 3.8 or ratings_count < 10:
        return None
    return rating * 100 + min(ratings_count, 5000) ** 0.5


async def get_weekly_new_books():
    cached = _weekly_book_cache_get()
    if cached is not None:
        return cached
    candidates = await asyncio.to_thread(google_books.search_new_releases, 20)
    ranked = []
    for item in candidates:
        score = _weekly_book_score(item)
        if score is None or not _released_this_week(item.get("published_date")):
            continue
        ranked.append((score, item))
    ranked.sort(key=lambda row: row[0], reverse=True)
    items = [dict(item) for _score, item in ranked[:4]]
    _weekly_book_cache_set(items)
    return items


def _book_genre_menu_kb():
    buttons = [InlineKeyboardButton(label, callback_data=f"book_g_{key}")
               for key, label, _subject in _BOOK_GENRES]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


async def send_book_genre_menu(bot, cid, q=None):
    text = "Выбери жанр — подберу книгу с хорошей оценкой читателей."
    kb = _book_genre_menu_kb()
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


def _book_preferences_kb(cid):
    preferences = _book_preferences(cid)
    recency = preferences["recency"] or ""
    rating = str(preferences["min_rating"] or "")
    return InlineKeyboardMarkup([
        *[[InlineKeyboardButton(("✅ " if recency == value else "") + label,
                                callback_data=f"bookpref_recency_{value or 'any'}")]
          for label, value in _PREF_RECENCY],
        *[[InlineKeyboardButton(("✅ " if rating == value else "") + f"⭐️ {label}",
                                callback_data=f"bookpref_rating_{value}")]
          for label, value in _PREF_RATING],
        [InlineKeyboardButton("⬅️ Назад", callback_data="book_favorites"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_book_preferences(bot, cid, q=None):
    text = "📌 Предпочтения книг\n\nВыбери новизну и минимальную оценку читателей."
    kb = _book_preferences_kb(cid)
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def toggle_book_preference(bot, cid, data, q=None):
    if data.startswith("bookpref_recency_"):
        value = data[len("bookpref_recency_"):]
        if value in {"new", "any"}:
            settings.set_(cid, "book_recency", "" if value == "any" else value)
    elif data.startswith("bookpref_rating_"):
        value = data[len("bookpref_rating_"):]
        if value in {rating for _label, rating in _PREF_RATING}:
            current = str(settings.get(cid, "book_min_rating", "") or "")
            settings.set_(cid, "book_min_rating", "" if current == value else value)
    await send_book_preferences(bot, cid, q)

async def _send_book_card(bot, cid, it, i, *, enrich=True):
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
    kb = _book_kb(i)
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
    """Названия книг, которые нельзя повторять: любимые, показанные и отклонённые."""
    used = set()
    for key in (config.FAVORITE_BOOKS_KEY,):
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

def _pick_good_book(items, cid, extra_skip=(), *, fallback=True):
    """Выбирает неиспользованную книгу, предпочитая высокие оценки читателей."""
    used = _book_used(cid) | {str(x).strip().lower() for x in extra_skip}
    candidates = []
    for index, it in enumerate(items or []):
        if not isinstance(it, dict):
            continue
        t = (it.get("title", "") or "").strip().lower()
        if t and t not in used and _book_matches_preferences(it, cid):
            try:
                rating = float(it.get("rating") or 0)
            except (TypeError, ValueError):
                rating = 0
            try:
                ratings_count = int(it.get("ratings_count") or 0)
            except (TypeError, ValueError):
                ratings_count = 0
            candidates.append((rating > 0 and ratings_count > 0, rating, ratings_count, -index, it))
    if candidates:
        candidates.sort(reverse=True, key=lambda row: row[:-1])
        return candidates[0][-1]
    return _fallback_book(cid, extra_skip=extra_skip) if fallback else None


def _book_genre(key):
    for genre_key, label, subject in _BOOK_GENRES:
        if genre_key == key:
            return label, subject
    return None, None


async def _enrich_book_candidates(items):
    """Добавляет оценки читателей к нескольким AI-кандидатам перед выбором."""
    candidates = [dict(item) for item in (items or []) if isinstance(item, dict)][:5]
    if not candidates:
        return []
    try:
        enriched = await asyncio.wait_for(
            asyncio.gather(*(
                asyncio.to_thread(google_books.enrich_book, item)
                for item in candidates
            )),
            timeout=5.0,
        )
        return [dict(item or {}) for item in enriched]
    except Exception:
        return candidates


async def _book_candidates(cid, category=None):
    if category:
        _label, subject = _book_genre(category.get("value"))
        if not subject:
            return []
        return await asyncio.to_thread(google_books.search_by_subject, subject)
    items = []
    for _ in range(2):
        try:
            data = await asyncio.to_thread(content_recommend, "book", str(cid))
            items = data.get("items", []) if isinstance(data, dict) else []
        except Exception:
            items = []
        if items:
            break
    return await _enrich_book_candidates(items)


async def get_current_book(cid):
    cached = _cached_book(cid)
    if cached:
        if not cached.get("rating") or not cached.get("ratings_count"):
            cached = await asyncio.to_thread(google_books.enrich_book, cached)
            _cache_book(cid, cached)
        return cached
    items = await _book_candidates(cid)
    it = _pick_good_book(items, cid)
    _cache_book(cid, it)
    return it


async def send_books_reco(bot, cid):
    it = await get_current_book(cid)
    store.last_recos[str(cid)] = {"kind": "book", "items": [it.get("title", "")]}
    store.last_source[str(cid)] = "Книги"
    store.last_answer[str(cid)] = it.get("title", "")
    prepared = await _send_book_card(bot, cid, it, 0, enrich=False)
    _cache_book(cid, prepared)


async def send_book_by_genre(bot, cid, genre_key):
    label, subject = _book_genre(genre_key)
    if not subject:
        await send_book_genre_menu(bot, cid)
        return
    category = {"kind": "genre", "value": genre_key, "label": label}
    items = await _book_candidates(cid, category)
    it = _pick_good_book(items, cid, fallback=False)
    if not it:
        genre_label = label.split(" ", 1)[-1]
        await bot.send_message(
            chat_id=cid,
            text=f"В жанре «{genre_label}» пока не нашёл подходящую книгу.",
            reply_markup=_book_genre_menu_kb(),
        )
        return
    rec = {"kind": "book", "items": [it.get("title", "")], "category": category}
    store.last_recos[str(cid)] = rec
    store.last_source[str(cid)] = "Книги"
    store.last_answer[str(cid)] = it.get("title", "")
    prepared = await _send_book_card(bot, cid, it, 0, enrich=False)
    _cache_book(cid, prepared)


async def book_dislike(bot, cid, i):
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        recommendation_stoplist.add(cid, "book", title, "hidden")
    rec = store.last_recos.get(str(cid), {"kind": "book", "items": []})
    category = rec.get("category")
    items = await _book_candidates(cid, category)
    it = _pick_good_book(
        items, cid, extra_skip=rec.get("items", []), fallback=not category,
    )
    if not it:
        await bot.send_message(
            chat_id=cid,
            text="В этом жанре пока не нашёл другой книги.",
            reply_markup=_book_genre_menu_kb(),
        )
        return
    rec["items"].append(it.get("title", ""))
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    prepared = await _send_book_card(bot, cid, it, ni, enrich=False)
    _cache_book(cid, prepared)

async def _advance_book(bot, cid):
    """Загрузить следующую рекомендацию книги и показать карточку."""
    rec = store.last_recos.get(str(cid), {"kind": "book", "items": []})
    category = rec.get("category")
    items = await _book_candidates(cid, category)
    it = _pick_good_book(
        items, cid, extra_skip=rec.get("items", []), fallback=not category,
    )
    if not it:
        await bot.send_message(
            chat_id=cid,
            text="В этом жанре пока не нашёл другой книги.",
            reply_markup=_book_genre_menu_kb(),
        )
        return
    rec["items"].append(it.get("title", ""))
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    prepared = await _send_book_card(bot, cid, it, ni, enrich=False)
    _cache_book(cid, prepared)

async def book_love(bot, cid, i, q=None):
    """Добавляет книгу в любимые без дублей и отражает состояние на карточке."""
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        _add_unique(config.FAVORITE_BOOKS_KEY, cid, title)
        if q is not None:
            await q.message.edit_reply_markup(reply_markup=_book_kb(i))
