"""Книжные рекомендации, замены и любимые книги."""

import asyncio
import html
import logging
import random
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote_plus

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

import config
import google_books
import recommendation_stoplist
import inclusive_recommendations
import settings
import store
import tracking
from util import _MONTHS
from ui import leisure as leisure_ui
from leisure_collection import plain_label


_log = logging.getLogger(__name__)
_BOOK_DAILY_LOCK = threading.Lock()
_BOOK_BIRTHDAY_CACHE_VERSION = 2


_BOOK_GENRES = [
    ("fantasy", "Фэнтези", "Fantasy"),
    ("scifi", "Фантастика", "Science fiction"),
    ("detective", "Детектив", "Mystery & Detective"),
    ("thriller", "Триллер", "Thrillers"),
    ("romance", "Романтика", "Romance"),
    ("history", "История", "History"),
    ("biography", "Биографии", "Biography & Autobiography"),
    ("psychology", "Психология", "Psychology"),
]
_PREF_RECENCY = [("Новинки", "new"), ("Любые годы", "")]
_PREF_RATING = [("3.5", "3.5"), ("4.0", "4.0"), ("4.5", "4.5")]
_WEEKLY_SHOWCASE_VERSION = 4
_BOOK_PREMIERES_CACHE_VERSION = 2
_FAVORITE_BOOK_PAGE_SIZE = 8
_FAVORITE_BOOK_VIEW_TTL = 24 * 3600
_favorite_book_views = {}

_BOOK_CATEGORY_RU = {
    "fiction": "Художественная проза", "fantasy": "Фэнтези",
    "science fiction": "Фантастика", "mystery & detective": "Детектив",
    "thrillers": "Триллер", "romance": "Романтика", "history": "История",
    "biography": "Биография", "biography & autobiography": "Биография",
    "psychology": "Психология", "poetry": "Поэзия",
    "juvenile fiction": "Детская литература",
}

_BOOK_REBUSES = (
    {
        "emoji": "🧙‍♀️ ⚡ 🚂",
        "answer": "Гарри Поттер",
        "fact": "Рукопись Джоан Роулинг отклонили несколько издательств до первой публикации.",
    },
    {
        "emoji": "🐋 ⚓ 👨‍✈️",
        "answer": "Моби Дик",
        "fact": "Герман Мелвилл работал на китобойном судне и использовал этот опыт в романе.",
    },
    {
        "emoji": "🕳️ 🐇 👧",
        "answer": "Алиса в Стране чудес",
        "fact": "Льюис Кэрролл сначала рассказывал эту историю устно во время лодочных прогулок.",
    },
    {
        "emoji": "💍 🌋 🧙",
        "answer": "Властелин колец",
        "fact": "Толкин работал над этой историей больше десяти лет.",
    },
)
_BOOK_BIRTHDAY_FALLBACKS = {
    (8, 4): {
        "name": "Кнут Гамсун",
        "birth": "1859-08-04",
        "detail": "норвежский писатель и лауреат Нобелевской премии по литературе",
        "fact": "«Голод» стал литературным прорывом Гамсуна и одним из первых современных норвежских романов.",
    },
}
_PREMIERE_SUMMARIES = {
    "onyx storm": "Вайолет ищет союзников, пока война всё ближе к её дому.",
    "great big beautiful life": "Две писательницы соперничают за право рассказать историю затворницы с тёмным прошлым.",
    "the tenant": "Женщина снимает комнату в идеальном доме и замечает, что хозяева скрывают опасную тайну.",
    "atmosphere": "Астронавтка пытается совместить мечту о космосе с любовью, которую нельзя назвать вслух.",
}


def _item_text(item):
    if isinstance(item, dict):
        return str(item.get("value", "")).strip()
    return str(item or "").strip()


def _add_unique(key, cid, value):
    value = plain_label(value)
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
    title = str(item.get("title") or _item_text(item)).strip()
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
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_books"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def books_home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Подобрать новую книгу", callback_data="book_reco")],
        [InlineKeyboardButton("✍🏻 Премьеры", callback_data="book_premieres")],
        [InlineKeyboardButton("🎚️ Мои книги", callback_data="book_favorites")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_books_home(bot, cid, q=None, status=None):
    """Открывает ежедневную литературную витрину; подбор книги остаётся по кнопке."""
    daily_book, items = await asyncio.gather(
        _daily_book_content(),
        get_weekly_new_books(),
    )
    msg = leisure_ui.weekly_books_screen(_book_city(cid), daily_book, items)
    kb = books_home_keyboard()
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb,
                             disable_web_page_preview=True)
        return
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb,
                                      disable_web_page_preview=True)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb,
                           disable_web_page_preview=True)


async def warm_books_home_cache(cid):
    """Готовит данные литературной витрины без персональной рекомендации."""
    await asyncio.gather(_daily_book_content(), get_weekly_new_books())
    return True


def _favorite_book_value(record):
    return str(record.get("value") or record.get("title") or record.get("name") or "").strip()


def _favorite_book_genre(item):
    categories = item.get("categories") or []
    if isinstance(categories, str):
        categories = [categories]
    first = next((str(value).strip() for value in categories if str(value).strip()), "")
    return _BOOK_CATEGORY_RU.get(first.casefold(), first or "Без жанра")


async def _favorite_book_records(cid):
    records = store.ensure_list_ids(config.FAVORITE_BOOKS_KEY, cid)
    semaphore = asyncio.Semaphore(6)

    async def enrich(record):
        value = _favorite_book_value(record)
        metadata = {"title": value}
        if value:
            async with semaphore:
                try:
                    metadata = await asyncio.wait_for(
                        asyncio.to_thread(google_books.enrich_book, metadata), timeout=5.0,
                    )
                except Exception:
                    pass
        metadata = _with_book_url(dict(metadata or {}))
        return {
            "id": str(record.get("id") or ""),
            "value": value,
            "title": str(metadata.get("title") or value).strip(),
            "genre": _favorite_book_genre(metadata),
            "book": metadata,
        }

    return list(await asyncio.gather(*(enrich(record) for record in records)))


def _new_favorite_book_view(cid, records):
    now = time.time()
    for token, view in list(_favorite_book_views.items()):
        if now - view.get("created_at", 0) > _FAVORITE_BOOK_VIEW_TTL:
            _favorite_book_views.pop(token, None)
    genres = {}
    for record in records:
        genres.setdefault(record["genre"], []).append(record)
    for items in genres.values():
        items.sort(key=lambda item: item["title"].casefold())
    genre_order = {label: index for index, (_key, label, _subject) in enumerate(_BOOK_GENRES)}
    genre_order.update({
        "Художественная проза": len(genre_order),
        "Поэзия": len(genre_order) + 1,
        "Детская литература": len(genre_order) + 2,
        "Без жанра": len(genre_order) + 3,
    })
    ordered = sorted(
        genres,
        key=lambda value: (genre_order.get(value, len(genre_order)), value.casefold()),
    )
    token = secrets.token_hex(3)
    view = {"cid": str(cid), "created_at": now,
            "genres": [(genre, genres[genre]) for genre in ordered]}
    _favorite_book_views[token] = view
    return token, view


def _favorite_book_view(cid, token):
    view = _favorite_book_views.get(token)
    if not view or view.get("cid") != str(cid):
        return None
    if time.time() - view.get("created_at", 0) > _FAVORITE_BOOK_VIEW_TTL:
        _favorite_book_views.pop(token, None)
        return None
    return view


async def send_favorite_books(bot, cid, q=None):
    records = await _favorite_book_records(cid)
    token, view = _new_favorite_book_view(cid, records)
    msg = leisure_ui.favorite_books_home(len(records), [
        {"genre": genre, "titles": [item["title"] for item in items]}
        for genre, items in view["genres"]
    ])
    rows = [[InlineKeyboardButton(
        f"{genre} · {len(items)}", callback_data=f"bfg:{token}:{index}:0",
    )] for index, (genre, items) in enumerate(view["genres"])]
    rows.append([InlineKeyboardButton("🆕 Добавить книгу", callback_data="as_loveadd_books")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_books"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_favorite_book_genre(bot, cid, token, genre_index, page=0, q=None):
    view = _favorite_book_view(cid, token)
    if view is None or not 0 <= genre_index < len(view["genres"]):
        await send_favorite_books(bot, cid, q=q)
        return
    genre, items = view["genres"][genre_index]
    page = max(0, min(int(page), len(items) - 1))
    item = items[page]
    book = item["book"]
    msg = _book_text(book)
    rows = []
    if len(items) > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"bfg:{token}:{genre_index}:{(page - 1) % len(items)}"),
            InlineKeyboardButton(f"{page + 1}/{len(items)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"bfg:{token}:{genre_index}:{(page + 1) % len(items)}"),
        ])
    rows.append([InlineKeyboardButton(
        "❌ Удалить", callback_data=f"bfd:{token}:{item['id'][:8]}:{genre_index}:{page}",
    )])
    rows.append([InlineKeyboardButton("🆕 Добавить книгу", callback_data="as_loveadd_books")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="book_favorites"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    kb = InlineKeyboardMarkup(rows)
    cover = str(book.get("cover_url") or "").strip()
    if q is not None and cover:
        try:
            await q.edit_message_media(
                media=InputMediaPhoto(
                    media=cover, caption=msg.text, caption_entities=msg.entities,
                ),
                reply_markup=kb,
            )
            return
        except Exception:
            pass
    if cover:
        try:
            await bot.send_photo(
                chat_id=cid, photo=cover, caption=msg.text,
                caption_entities=msg.entities, reply_markup=kb,
            )
            return
        except Exception:
            pass
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=kb, disable_web_page_preview=True,
    )


async def _deliver_book_view(bot, cid, msg, markup, q=None):
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=markup)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=markup)


def _favorite_book_from_view(cid, token, short_id):
    view = _favorite_book_view(cid, token)
    if view is None:
        return None
    return next((item for _genre, items in view["genres"] for item in items
                 if item["id"].startswith(short_id)), None)


async def send_favorite_book_card(bot, cid, token, short_id, genre_index, page):
    item = _favorite_book_from_view(cid, token, short_id)
    if item is None:
        await send_favorite_books(bot, cid)
        return
    book = item["book"]
    msg = _book_text(book)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"bfd:{token}:{short_id}:{genre_index}:{page}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"bfg:{token}:{genre_index}:{page}"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    cover = str(book.get("cover_url") or "").strip()
    if cover:
        try:
            await bot.send_photo(chat_id=cid, photo=cover, caption=msg.text,
                                 caption_entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=kb, disable_web_page_preview=True)


async def send_favorite_book_delete_confirmation(bot, cid, token, short_id, genre_index, page, q=None):
    item = _favorite_book_from_view(cid, token, short_id)
    if item is None:
        await send_favorite_books(bot, cid, q=q)
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"bfdok:{token}:{short_id}")],
        [InlineKeyboardButton("Отмена", callback_data=f"bfg:{token}:{genre_index}:{page}"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    await _deliver_book_view(
        bot, cid, leisure_ui.favorite_book_delete_confirmation(item["title"]), kb, q=q,
    )


async def delete_favorite_book(bot, cid, token, short_id, q=None):
    item = _favorite_book_from_view(cid, token, short_id)
    if item is not None:
        store.remove_from_list_by_ids(config.FAVORITE_BOOKS_KEY, cid, [item["id"]])
    _favorite_book_views.pop(token, None)
    await send_favorite_books(bot, cid, q=q)


async def warm_book_premieres_cache():
    """Обновляет общую книжную витрину только из ночного расписания."""
    await get_book_premieres(refresh=True)
    return True


def _daily_book_rebus(day):
    return dict(_BOOK_REBUSES[(day.timetuple().tm_yday - 216) % len(_BOOK_REBUSES)])


def _book_birthday_cache_get(day):
    data = store._load(config.BOOK_DAILY_CACHE_KEY)
    entry = data.get(day.isoformat()) if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return None
    if entry.get("version") != _BOOK_BIRTHDAY_CACHE_VERSION:
        return None
    birthday = entry.get("birthday")
    return dict(birthday) if isinstance(birthday, dict) else {}


def _book_birthday_cache_set(day, birthday):
    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[day.isoformat()] = {
            "version": _BOOK_BIRTHDAY_CACHE_VERSION,
            "ts": time.time(),
            "birthday": dict(birthday or {}),
        }
        return data, None

    store.mutate_kv(config.BOOK_DAILY_CACHE_KEY, mutate)


def _book_birthday_detail(role):
    value = str(role or "").casefold()
    if "поэт" in value or "poet" in value:
        return "поэт"
    if "писательниц" in value or "writer" in value or "author" in value:
        return "писатель"
    return "автор"


def _load_book_birthday(day):
    """Один проверенный литературный именинник на день для всех пользователей."""
    cached = _book_birthday_cache_get(day)
    if cached is not None:
        return cached
    with _BOOK_DAILY_LOCK:
        cached = _book_birthday_cache_get(day)
        if cached is not None:
            return cached
        fallback = _BOOK_BIRTHDAY_FALLBACKS.get((day.month, day.day))
        if fallback:
            _book_birthday_cache_set(day, fallback)
            return dict(fallback)
        query = """
            SELECT ?personLabel ?birth ?occupationLabel (wikibase:sitelinks(?person) AS ?sitelinks) WHERE {
              ?person wdt:P31 wd:Q5; wdt:P569 ?birth; wdt:P106 ?occupation.
              VALUES ?occupation { wd:Q49757 wd:Q36180 wd:Q482980 }
              FILTER(MONTH(?birth) = %d && DAY(?birth) = %d)
              SERVICE wikibase:label { bd:serviceParam wikibase:language \"ru,en\". }
            }
            ORDER BY DESC(?sitelinks)
            LIMIT 1
        """ % (day.month, day.day)
        try:
            response = requests.get(
                "https://query.wikidata.org/sparql",
                params={"query": query, "format": "json"},
                headers={"Accept": "application/sparql-results+json", "User-Agent": "morning-bot/1.0"},
                timeout=6,
            )
            response.raise_for_status()
            bindings = response.json().get("results", {}).get("bindings", [])
            if bindings:
                item = bindings[0]
                name = str((item.get("personLabel") or {}).get("value") or "").strip()
                if name:
                    birthday = {
                        "name": name,
                        "detail": _book_birthday_detail(
                            (item.get("occupationLabel") or {}).get("value")),
                    }
                    birth = str((item.get("birth") or {}).get("value") or "").strip()
                    if birth:
                        birthday["birth"] = birth
                    _book_birthday_cache_set(day, birthday)
                    return birthday
        except Exception as error:
            _log.info("book birthday lookup unavailable: %s", type(error).__name__)
        _book_birthday_cache_set(day, {})
        return {}


def _book_city(cid):
    settings_data = store.get_settings(cid)
    return str(settings_data.get("city") or config.DEFAULT_CITY.get("name") or "").strip()


def _premiere_summary(item):
    title = str((item or {}).get("title") or "").casefold().strip()
    known = _PREMIERE_SUMMARIES.get(title)
    if known:
        return known
    description = html.unescape(str((item or {}).get("description") or ""))
    description = re.sub(r"<[^>]+>", " ", description)
    description = re.sub(r"\s+", " ", description).strip()
    if not description:
        return ""
    sentence = re.split(r"(?<=[.!?])\s+", description, maxsplit=1)[0].strip()
    return sentence[:180].rstrip(" ,;:") or ""


def _book_showcase_url(item) -> str:
    """Return a stable Google Books destination for a weekly showcase item."""
    item = item or {}
    for key in ("info_link", "preview_link", "url"):
        url = str(item.get(key) or "").strip()
        if url.startswith(("https://", "http://")):
            return url

    query = " ".join(
        str(item.get(key) or "").strip()
        for key in ("title", "author")
        if str(item.get(key) or "").strip()
    )
    return f"https://books.google.com/books?q={quote_plus(query)}" if query else ""


def _with_book_url(item):
    result = dict(item or {})
    result["url"] = _book_showcase_url(result)
    return result


def _books_with_premiere_summaries(items):
    return [
        {
            **dict(item),
            "summary": _premiere_summary(item),
            "url": _book_showcase_url(item),
        }
        for item in (items or [])
        if isinstance(item, dict)
    ]


async def _daily_book_content():
    now = datetime.now(config.TZ)
    return {
        "rebus": _daily_book_rebus(now.date()),
        "birthday": await asyncio.to_thread(_load_book_birthday, now.date()),
    }

def _book_week_key() -> str:
    current = datetime.now(config.TZ).date()
    year, week, _weekday = current.isocalendar()
    return f"{year}-W{week:02d}"


def _weekly_book_cache_get():
    entry = store._load(config.BOOK_WEEKLY_CACHE_KEY)
    if (not isinstance(entry, dict) or entry.get("week") != _book_week_key()
            or entry.get("date") != datetime.now(config.TZ).date().isoformat()
            or entry.get("version") != _WEEKLY_SHOWCASE_VERSION):
        return None
    items = entry.get("items")
    # Пустая витрина не должна блокировать новый поиск на весь день: после
    # обновления логики она может заполниться новинками месяца или бестселлерами.
    return [dict(item) for item in items] if isinstance(items, list) and items else None


def _weekly_book_cache_set(items):
    store._save(config.BOOK_WEEKLY_CACHE_KEY, {
        "version": _WEEKLY_SHOWCASE_VERSION,
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


def _release_date(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _released_this_month(value: str) -> bool:
    released = _release_date(value)
    today = datetime.now(config.TZ).date()
    return bool(released and released.year == today.year and released.month == today.month)


def _released_recently(value: str, *, days=180) -> bool:
    released = _release_date(value)
    today = datetime.now(config.TZ).date()
    return bool(released and today - timedelta(days=days) <= released <= today)


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


def _monthly_book_score(item):
    """У свежей премьеры может быть мало оценок, но она всё равно нужна витрине."""
    try:
        rating = float(item.get("rating") or 0)
        ratings_count = int(item.get("ratings_count") or 0)
    except (TypeError, ValueError):
        return None
    if rating < 3.8 or ratings_count < 1:
        return None
    return rating * 100 + min(ratings_count, 5000) ** 0.5


def _showcase_items(rows, showcase):
    return [{**dict(item), "_showcase": showcase} for _score, item in rows[:4]]


def _fallback_book_showcase(candidates):
    monthly = []
    for item in candidates:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        if not _released_this_month(item.get("published_date")):
            continue
        score = _monthly_book_score(item) or 0
        monthly.append((score, item))
    monthly.sort(key=lambda row: (
        row[0], str(row[1].get("published_date") or ""),
    ), reverse=True)
    return _showcase_items(monthly, "month")


async def get_weekly_new_books():
    cached = _weekly_book_cache_get()
    if cached is not None:
        return cached
    candidates = await asyncio.to_thread(google_books.search_new_releases, 20)
    ranked = []
    for item in candidates:
        score = _weekly_book_score(item)
        if (score is None or not _released_this_week(item.get("published_date"))
                or not _released_this_month(item.get("published_date"))):
            continue
        ranked.append((score, item))
    ranked.sort(key=lambda row: row[0], reverse=True)
    items = [dict(item) for _score, item in ranked[:4]]
    if not items:
        items = _fallback_book_showcase(candidates)
    items = _books_with_premiere_summaries(items)
    _weekly_book_cache_set(items)
    return items


def _book_premieres_cache_get(today, *, allow_stale=False):
    data = store._load(config.BOOK_PREMIERES_CACHE_KEY)
    entry = data if isinstance(data, dict) else {}
    if entry.get("version") != _BOOK_PREMIERES_CACHE_VERSION:
        return None
    if entry.get("month") != today.strftime("%Y-%m"):
        return None
    try:
        expires = date.fromisoformat(str(entry.get("expires") or ""))
    except ValueError:
        return None
    items = entry.get("items")
    if (expires < today and not allow_stale) or not isinstance(items, list):
        return None
    return [dict(item) for item in items if isinstance(item, dict)]


def _book_premieres_cache_set(today, items):
    store._save(config.BOOK_PREMIERES_CACHE_KEY, {
        "version": _BOOK_PREMIERES_CACHE_VERSION,
        "month": today.strftime("%Y-%m"),
        "expires": (today + timedelta(days=7)).isoformat(),
        "items": [dict(item) for item in items if isinstance(item, dict)],
    })


def _book_premiere_genre(item):
    categories = [str(value).strip() for value in (item.get("categories") or []) if str(value).strip()]
    return categories[0].casefold() if categories else ""


async def get_book_premieres(*, refresh=False):
    """Свежие книги из Google Books; днём используется готовый недельный кэш."""
    today = datetime.now(config.TZ).date()
    cached = _book_premieres_cache_get(today)
    if cached is not None and (cached or not refresh):
        return cached
    if not refresh:
        return _book_premieres_cache_get(today, allow_stale=True) or []
    candidates = await asyncio.to_thread(google_books.search_new_releases, 40)
    month_items, recent_items, seen = [], [], set()
    for item in candidates:
        if not isinstance(item, dict) or not str(item.get("cover_url") or "").strip():
            continue
        title = str(item.get("title") or "").strip()
        if not title or title.casefold() in seen:
            continue
        prepared = _with_book_url({
            **item,
            "summary": _premiere_summary(item),
        })
        if _released_this_month(item.get("published_date")):
            month_items.append(prepared)
        elif _released_recently(item.get("published_date")):
            recent_items.append(prepared)
        else:
            continue
        seen.add(title.casefold())
    fresh = month_items or recent_items
    fresh.sort(key=lambda item: (
        str(item.get("published_date") or ""),
        float(item.get("rating") or 0),
        int(item.get("ratings_count") or 0),
    ), reverse=True)

    # Сначала по одной книге из разных категорий, затем заполняем список по датам.
    items, used_genres = [], set()
    for item in fresh:
        genre = _book_premiere_genre(item)
        if genre and genre in used_genres:
            continue
        items.append(item)
        if genre:
            used_genres.add(genre)
        if len(items) == 12:
            break
    if len(items) < 12:
        existing = {str(item.get("title") or "").casefold() for item in items}
        for item in fresh:
            if str(item.get("title") or "").casefold() in existing:
                continue
            items.append(item)
            if len(items) == 12:
                break
    if items:
        _book_premieres_cache_set(today, items)
    return items


async def send_book_premieres(bot, cid, *, status=None):
    today = datetime.now(config.TZ).date()
    month = f"{_MONTHS[today.month - 1].capitalize()} {today.year}"
    items = await get_book_premieres()
    if not items:
        # Ночной прогрев мог ещё не выполниться после запуска или смены месяца.
        # Пользовательский вход восстанавливает только отсутствующий кэш; готовая
        # витрина по-прежнему открывается без дополнительного запроса.
        items = await get_book_premieres(refresh=True)
    items = [
        item for item in items
        if str(item.get("cover_url") or "").strip()
    ][:7]
    period = month if all(_released_this_month(item.get("published_date")) for item in items) \
        else "Свежие новинки"
    msg = leisure_ui.book_premieres_screen(period, items)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_books"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    covers = [
        InputMediaPhoto(media=str(item.get("cover_url") or "").strip())
        for item in items
    ]
    if len(covers) >= 2:
        try:
            await bot.send_media_group(
                chat_id=cid,
                media=covers,
                caption=msg.text,
                caption_entities=msg.entities,
            )
            return
        except Exception:
            pass
    if len(covers) == 1:
        try:
            await bot.send_photo(
                chat_id=cid,
                photo=covers[0].media,
                caption=msg.text,
                caption_entities=msg.entities,
                reply_markup=kb,
            )
            return
        except Exception:
            pass
    if covers:
        msg = leisure_ui.book_premieres_screen(month, [])
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb,
                             disable_web_page_preview=True)
        return
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb,
                           disable_web_page_preview=True)


def _book_genre_menu_kb():
    buttons = [InlineKeyboardButton(label, callback_data=f"book_g_{key}")
               for key, label, _subject in _BOOK_GENRES]
    rows = [[button] for button in buttons]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_books"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


async def send_book_genre_menu(bot, cid, q=None):
    text = "Выбери жанр — подберу книгу с хорошей оценкой читателей."
    kb = _book_genre_menu_kb()
    if q is not None:
        try:
            await q.message.edit_reply_markup(reply_markup=kb)
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
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_preferences"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_book_preferences(bot, cid, q=None):
    text = "📚 Книги\n\nВыбери новизну и минимальную оценку читателей."
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

async def _send_book_card(bot, cid, it, i, *, enrich=True, status=None):
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
    it = _with_book_url(it)
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
        it["cover_url"] = cover
        try:
            await bot.send_photo(chat_id=cid, photo=cover, caption=msg.text, caption_entities=msg.entities, reply_markup=kb)
            return it
        except Exception:
            pass
    if status is not None:
        await status.replace(
            msg.text,
            entities=msg.entities,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
        return it
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=kb,
        disable_web_page_preview=True,
    )
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

_GENRE_FALLBACKS = {
    "fantasy": [
        {"title": "Хоббит", "title_en": "The Hobbit", "year": "1937", "author": "Дж. Р. Р. Толкин", "desc": "Приключение, с которого удобно начать большое фэнтези.", "plot": "Бильбо Бэггинс отправляется с гномами вернуть сокровища, захваченные драконом."},
        {"title": "Волшебник Земноморья", "title_en": "A Wizard of Earthsea", "year": "1968", "author": "Урсула Ле Гуин", "desc": "Неспешное фэнтези о взрослении, силе слова и ответственности.", "plot": "Юный маг Гед выпускает в мир тень и должен встретиться с ней лицом к лицу."},
    ],
    "scifi": [
        {"title": "Дюна", "title_en": "Dune", "year": "1965", "author": "Фрэнк Герберт", "desc": "Большая фантастика о власти, экологии и религии.", "plot": "Семья Атрейдесов прибывает на пустынную планету, где решается судьба главного ресурса галактики."},
        {"title": "451° по Фаренгейту", "title_en": "Fahrenheit 451", "year": "1953", "author": "Рэй Брэдбери", "desc": "Короткая антиутопия о мире, где книги запрещены.", "plot": "Пожарный Гай Монтэг начинает сомневаться в своей работе по сожжению книг."},
    ],
    "detective": [
        {"title": "Убийство в „Восточном экспрессе“", "title_en": "Murder on the Orient Express", "year": "1934", "author": "Агата Кристи", "desc": "Классический детектив с замкнутым кругом подозреваемых.", "plot": "Эркюль Пуаро расследует убийство, совершённое в застрявшем поезде."},
        {"title": "Собака Баскервилей", "title_en": "The Hound of the Baskervilles", "year": "1902", "author": "Артур Конан Дойл", "desc": "Мрачная загадка на английских болотах.", "plot": "Шерлок Холмс и доктор Ватсон проверяют легенду о проклятии семьи Баскервилей."},
    ],
    "thriller": [
        {"title": "Исчезнувшая", "title_en": "Gone Girl", "year": "2012", "author": "Гиллиан Флинн", "desc": "Психологический триллер о браке, тайнах и ненадёжных рассказчиках.", "plot": "После исчезновения жены Ник Данн оказывается главным подозреваемым."},
        {"title": "Девушка с татуировкой дракона", "title_en": "The Girl with the Dragon Tattoo", "year": "2005", "author": "Стиг Ларссон", "desc": "Напряжённое расследование с семейной тайной.", "plot": "Журналист и хакерка ищут следы девушки, пропавшей много лет назад."},
    ],
    "romance": [
        {"title": "Гордость и предубеждение", "title_en": "Pride and Prejudice", "year": "1813", "author": "Джейн Остин", "desc": "Остроумный роман о чувствах, статусе и первом впечатлении.", "plot": "Элизабет Беннет и мистер Дарси проходят путь от взаимного раздражения к пониманию."},
        {"title": "Джейн Эйр", "title_en": "Jane Eyre", "year": "1847", "author": "Шарлотта Бронте", "desc": "Романтическая история с готическим настроением и сильной героиней.", "plot": "Сирота Джейн становится гувернанткой и влюбляется в хозяина поместья."},
    ],
    "history": [
        {"title": "Волчий зал", "title_en": "Wolf Hall", "year": "2009", "author": "Хилари Мантел", "desc": "Исторический роман об Англии времён Генриха VIII.", "plot": "Томас Кромвель поднимается при дворе на фоне религиозного и личного кризиса короля."},
        {"title": "Имя розы", "title_en": "The Name of the Rose", "year": "1980", "author": "Умберто Эко", "desc": "История Средневековья, монастырская тайна и интеллектуальный детектив.", "plot": "Монах Вильгельм расследует череду смертей в аббатстве XIV века."},
    ],
    "biography": [
        {"title": "Стив Джобс", "title_en": "Steve Jobs", "year": "2011", "author": "Уолтер Айзексон", "desc": "Большая биография основателя Apple без сглаживания противоречий.", "plot": "Книга прослеживает путь Джобса от первых компьютеров до возвращения в Apple."},
        {"title": "Становление", "title_en": "Becoming", "year": "2018", "author": "Мишель Обама", "desc": "Личная история о семье, карьере и публичной жизни.", "plot": "Мишель Обама рассказывает о детстве в Чикаго, учёбе и годах в Белом доме."},
    ],
    "psychology": [
        {"title": "Думай медленно… решай быстро", "title_en": "Thinking, Fast and Slow", "year": "2011", "author": "Даниэль Канеман", "desc": "Понятное введение в когнитивные ошибки и два режима мышления.", "plot": "Канеман объясняет, как быстрые интуитивные решения отличаются от медленного анализа."},
        {"title": "Человек в поисках смысла", "title_en": "Man's Search for Meaning", "year": "1946", "author": "Виктор Франкл", "desc": "Книга о поиске смысла в тяжёлых обстоятельствах.", "plot": "Психиатр Виктор Франкл соединяет личный опыт и основы логотерапии."},
    ],
}

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


_INCLUSIVE_BOOKS = (
    {"title": "Песнь Ахилла", "title_en": "The Song of Achilles", "year": "2011",
     "author": "Мадлен Миллер", "lgbt": True,
     "desc": "Мифологический роман о любви Ахилла и Патрокла накануне Троянской войны.",
     "plot": "Патрокл взрослеет рядом с Ахиллом, пока война не заставляет их выбирать между славой и близостью."},
    {"title": "Комната Джованни", "title_en": "Giovanni's Room", "year": "1956",
     "author": "Джеймс Болдуин", "lgbt": True,
     "desc": "Психологический роман о любви, страхе и принятии себя в Париже.",
     "plot": "Американец Дэвид пытается отрицать чувства к Джованни и сталкивается с ценой этого выбора."},
    {"title": "Кэрол", "title_en": "The Price of Salt", "year": "1952",
     "author": "Патриция Хайсмит", "lgbt": True,
     "desc": "Роман о двух женщинах, которые решаются на отношения вопреки давлению общества.",
     "plot": "Встреча Терез и Кэрол превращается в путешествие, где обеим приходится защищать право на собственную жизнь."},
    {"title": "Прошлой ночью в Телеграфном клубе", "title_en": "Last Night at the Telegraph Club",
     "year": "2021", "author": "Малинда Ло", "lgbt": True,
     "desc": "Исторический роман о первой любви в китайском квартале Сан-Франциско 1950-х.",
     "plot": "Лили открывает для себя квир-сообщество города в эпоху политической подозрительности."},
)


async def _inclusive_book_pick(cid, extra_skip=()):
    used = _book_used(cid) | {str(value).casefold() for value in extra_skip}
    for source in _INCLUSIVE_BOOKS:
        if source["title"].casefold() in used:
            continue
        item = dict(source)
        try:
            item = await asyncio.to_thread(google_books.enrich_book, item)
        except Exception:
            pass
        item["lgbt"] = True
        return item
    return None


def _record_book_recommendation(cid, item):
    inclusive = bool(item.get("lgbt")) or inclusive_recommendations.is_inclusive(
        "book", item.get("title"), item.get("title_en"),
    )
    item["lgbt"] = inclusive
    inclusive_recommendations.record(cid, "book", inclusive)


def _genre_fallback_book(cid, genre_key, extra_skip=()):
    """Локальный резерв сохраняет смысл выбранного жанра при пустом каталоге."""
    used = _book_used(cid) | {str(x).strip().lower() for x in extra_skip}
    pool = [
        item for item in _GENRE_FALLBACKS.get(genre_key, [])
        if item["title"].casefold() not in used
    ]
    return dict(random.choice(pool)) if pool else None

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


async def send_books_reco(bot, cid, status=None):
    it = (
        await _inclusive_book_pick(cid)
        if inclusive_recommendations.is_due(cid, "book") else None
    ) or await get_current_book(cid)
    _record_book_recommendation(cid, it)
    store.last_recos[str(cid)] = {"kind": "book", "items": [it.get("title", "")]}
    store.last_source[str(cid)] = "Книги"
    store.last_answer[str(cid)] = it.get("title", "")
    prepared = await _send_book_card(bot, cid, it, 0, enrich=False, status=status)
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
        it = _genre_fallback_book(cid, genre_key)
    if not it:
        genre_label = label.split(" ", 1)[-1]
        await bot.send_message(
            chat_id=cid,
            text=f"В жанре «{genre_label}» пока не нашёл подходящую книгу.",
            reply_markup=_book_genre_menu_kb(),
        )
        return
    _record_book_recommendation(cid, it)
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
    if not category and inclusive_recommendations.is_due(cid, "book"):
        it = await _inclusive_book_pick(cid, rec.get("items", [])) or it
    _record_book_recommendation(cid, it)
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
    if not category and inclusive_recommendations.is_due(cid, "book"):
        it = await _inclusive_book_pick(cid, rec.get("items", [])) or it
    _record_book_recommendation(cid, it)
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
