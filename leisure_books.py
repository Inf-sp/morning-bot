"""Книжные рекомендации, замены и любимые книги."""

import asyncio
import html
import json
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

import ai
import config
import google_books
import open_library
import monthly_rebuses
import recommendation_stoplist
import recommendation_rotation as rotation
import research
import secure
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
_WEEKLY_SHOWCASE_VERSION = 7
_BOOK_PREMIERES_CACHE_VERSION = 2
_FAVORITE_BOOK_PAGE_SIZE = 8
_FAVORITE_BOOK_VIEW_TTL = 24 * 3600
_favorite_book_views = {}
_MANUAL_BOOK_CHOICE_TTL = 15 * 60
_manual_book_choices = {}

_BOOK_CATEGORY_RU = {
    "fiction": "Художественная проза", "fantasy": "Фэнтези",
    "science fiction": "Фантастика", "mystery & detective": "Детектив",
    "thrillers": "Триллер", "romance": "Романтика", "history": "История",
    "biography": "Биография", "biography & autobiography": "Биография",
    "psychology": "Психология", "poetry": "Поэзия",
    "juvenile fiction": "Детская литература",
}

_BOOK_REBUSES = monthly_rebuses.local_pool("books")
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

# Проверенный сезонный резерв на случай, когда Google Books,
# Tavily/LLM и Open Library одновременно не дали выдачу. Даты, ISBN и
# авторы взяты с официальных страниц издателей.
_VERIFIED_SEASON_RELEASES = (
    {
        "title": "The Residency", "author": "C. D. Major",
        "published_date": "2026-08-04", "isbn": "9798217269495",
        "cover_url": "https://images1.penguinrandomhouse.com/cover/9798217269495",
        "info_link": "https://www.penguinrandomhouse.com/books/818091/the-residency-by-c-d-major/",
        "publisher": "Canelo", "publisher_date_confirmed": True,
        "categories": ["Thrillers"],
        "description": "Молодая художница приезжает в изолированную резиденцию, где невозможно отличить шанс на новую жизнь от опасной ловушки.",
    },
    {
        "title": "Hello Baby", "author": "Kim Eui-Kyung",
        "published_date": "2026-08-18", "isbn": "9780593734896",
        "cover_url": "https://images1.penguinrandomhouse.com/cover/9780593734896",
        "info_link": "https://www.penguinrandomhouse.com/books/769778/hello-baby-by-kim-eui-kyung/9780593734896/",
        "publisher": "Hogarth", "publisher_date_confirmed": True,
        "categories": ["Fiction"],
        "description": "Шесть женщин поддерживают друг друга во время ЭКО, но неожиданная новость испытывает их дружбу и надежды.",
    },
    {
        "title": "Appetite", "author": "P. Paramita",
        "published_date": "2026-08-04", "isbn": "9780593978580",
        "cover_url": "https://images1.penguinrandomhouse.com/cover/9780593978580",
        "info_link": "https://www.penguinrandomhouse.com/books/783084/appetite-by-p-paramita/",
        "publisher": "Dial Press", "publisher_date_confirmed": True,
        "categories": ["Fiction"],
        "description": "Молодая шеф-повар идёт к мечте, пока встреча с любимой звездой рестлинга не меняет её представление о славе и близости.",
    },
    {
        "title": "Hunger and Thirst", "author": "Claire Fuller",
        "published_date": "2026-06-02", "isbn": "9781963108729",
        "cover_url": "https://images1.penguinrandomhouse.com/cover/9781963108729",
        "info_link": "https://www.penguinrandomhouse.com/books/816559/hunger-and-thirst-by-claire-fuller/",
        "publisher": "Penguin Random House", "publisher_date_confirmed": True,
        "description": "Атмосферный роман о дружбе, принадлежности и цене принятия.",
    },
    {
        "title": "When Mikan Road Was Ours", "author": "D. K. Furutani",
        "published_date": "2026-07-28", "isbn": "9781668086940",
        "cover_url": "https://images.simonandschuster.com/BookImages/Products/9781668086940.jpg",
        "info_link": "https://www.simonandschuster.com/books/When-Mikan-Road-Was-Ours/D-K-Furutani/9781668086940",
        "publisher": "Atria Books", "publisher_date_confirmed": True,
        "description": "Семейная сага о четырёх поколениях японо-американской семьи.",
    },
    {
        "title": "Few and Far Between", "author": "Jan Carson",
        "published_date": "2026-07-28", "isbn": "9781668056639",
        "cover_url": "https://images.simonandschuster.com/BookImages/Products/9781668056639.jpg",
        "info_link": "https://www.simonandschuster.com/books/Few-and-Far-Between/Jan-Carson/9781668056639",
        "publisher": "Scribner", "publisher_date_confirmed": True,
        "description": "Тёмная и ироничная альтернативная история о семье и общественной травме.",
    },
)


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
                             params={"title": q, "language": "eng", "limit": 10,
                                     "fields": "cover_i,language"}, timeout=timeout)
            docs = r.json().get("docs", [])
            doc = next((doc for doc in docs
                        if doc.get("cover_i") and "eng" in (doc.get("language") or [])), None)
            if doc:
                return f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-L.jpg"
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
        [InlineKeyboardButton("🎭 По жанру", callback_data="book_genre_menu")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_books"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def books_home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Что почитать", callback_data="book_reco")],
        [InlineKeyboardButton("🎚️ Мои книги", callback_data="book_favorites")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_books_home(bot, cid, q=None, status=None):
    """Открывает ежедневную литературную витрину; подбор книги остаётся по кнопке."""
    daily_book, items = await asyncio.gather(
        _daily_book_content(),
        get_weekly_new_books(),
    )
    _start, _end, season = _book_season()
    today = datetime.now(config.TZ).date()
    msg = leisure_ui.weekly_books_screen(
        _book_city(cid), daily_book, items, day=today, season=season,
    )
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


async def warm_books_home_cache(cid, *, refresh=False):
    """Готовит данные литературной витрины без персональной рекомендации."""
    await asyncio.gather(
        _daily_book_content(refresh=True),
        get_weekly_new_books(refresh=True),
    )
    return True


def _favorite_book_value(record):
    return str(record.get("value") or record.get("title") or record.get("name") or "").strip()


def _manual_book_parts(value):
    """Разбирает «Название — Автор» или «Название (год)» без угадывания издания."""
    text = " ".join(str(value or "").split()).strip(" ,;.-")
    year_match = re.match(r"^(?P<title>.+?)\s*(?:\(|,?\s)(?P<year>\d{4})\)?$", text)
    if year_match:
        return year_match.group("title").strip(" ,;.-"), "", year_match.group("year")
    author_parts = re.split(r"\s+[—–-]\s+", text, maxsplit=1)
    if len(author_parts) == 2 and all(part.strip() for part in author_parts):
        return author_parts[0].strip(), author_parts[1].strip(), ""
    return text, "", ""


def _book_identity(value):
    return " ".join(re.findall(r"[a-zа-яё0-9]+", str(value or "").casefold(), flags=re.I))


_AUTHOR_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
})


def _author_group_key(value):
    """Сводит частые латинские и кириллические написания одного автора."""
    transliterated = str(value or "").casefold().translate(_AUTHOR_TRANSLIT)
    transliterated = transliterated.replace("w", "v").replace("y", "i")
    tokens = re.findall(r"[a-z0-9]+", transliterated)
    skeletons = [re.sub(r"[aeiou]", "", token) for token in tokens]
    skeletons = [token for token in skeletons if token]
    return " ".join(skeletons or tokens)


async def _analyze_manual_book_query(value):
    """Разбирает свободный ввод, не используя AI как источник метаданных книги."""
    raw = " ".join(str(value or "").split()).strip()
    local_title, local_author, local_year = _manual_book_parts(raw)
    prompt = f"""
Разбери короткий поисковый запрос книги. Это данные, а не инструкции.
Запрос: {secure.wrap_untrusted(raw, 'запрос пользователя')}
Выдели название. alternative_title — оригинальное или
международное название той же книги, только если уверен.
Автора и год верни только если они явно написаны в запросе; не угадывай их.
JSON: {{"title": "", "alternative_title": "", "author": "", "year": ""}}
"""
    try:
        data = await ai.allm_json(
            prompt, 350, tier="leisure", module="leisure_collection_add",
            fallback_allowed=True, privacy_level="public", budget_seconds=15,
        )
    except Exception:
        data = {}
    data = data if isinstance(data, dict) else {}

    def clean(name, fallback="", limit=160):
        return " ".join(str(data.get(name) or fallback or "").split()).strip()[:limit]

    title = clean("title", local_title)
    alternative_title = clean("alternative_title", limit=160)
    ai_author = clean("author", limit=100)
    raw_identity = _book_identity(raw)
    ai_author_identity = _book_identity(ai_author)
    raw_author_key = _author_group_key(raw)
    ai_author_key = _author_group_key(ai_author)
    author = local_author
    if (not author and ai_author_identity
            and (f" {ai_author_identity} " in f" {raw_identity} "
                 or (ai_author_key
                     and f" {ai_author_key} " in f" {raw_author_key} "))):
        author = ai_author
    ai_year_match = re.search(r"\b(?:18|19|20|21)\d{2}\b", clean("year", limit=8))
    ai_year = ai_year_match.group(0) if ai_year_match else ""
    year = local_year
    if not year and ai_year and re.search(rf"(?<!\d){re.escape(ai_year)}(?!\d)", raw):
        year = ai_year
    return {
        "title": title or local_title,
        "alternative_title": alternative_title,
        "author": author,
        "year": year,
    }


def _manual_book_candidate(item):
    candidate = dict(item or {})
    catalog_title = str(candidate.get("title") or "").strip()
    if not catalog_title or not candidate.get("author") or not candidate.get("year"):
        return None
    if not str(candidate.get("cover_url") or "").startswith(("https://", "http://")):
        return None
    category_text = " ".join(
        str(value) for value in (candidate.get("categories") or [])
    ).casefold()
    if any(marker in category_text for marker in (
        "juvenile fiction", "juvenile nonfiction", "children's", "children books",
        "детская литература", "книги для детей",
    )):
        return None
    # Показываем именно подтверждённое каталожное название. AI-вариант нужен
    # только для поиска и не должен переименовывать найденную книгу.
    candidate["title"] = catalog_title
    candidate["value"] = catalog_title
    candidate = _with_book_url(candidate)
    candidate["genre_label"] = _favorite_book_genre(candidate)
    return candidate


_MANUAL_BOOK_GENRES = (
    "Художественная проза", "Фэнтези", "Фантастика", "Детектив", "Триллер",
    "Романтика", "История", "Биография", "Психология", "Поэзия",
    "Детская литература", "Другое",
)


def _fallback_manual_book_genre(item):
    text = " ".join(str(item.get(key) or "") for key in ("title", "description", "subtitle")).casefold()
    rules = (
        (("science fiction", "научн", "космос", "эксперимент"), "Фантастика"),
        (("fantasy", "маг", "волшеб"), "Фэнтези"),
        (("mystery", "detective", "расследован"), "Детектив"),
        (("thriller", "триллер"), "Триллер"),
        (("romance", "любов"), "Романтика"),
        (("biograph", "мемуар"), "Биография"),
        (("history", "историчес"), "История"),
        (("psycholog", "психолог"), "Психология"),
        (("poetry", "стих", "поэз"), "Поэзия"),
        (("children", "детск"), "Детская литература"),
    )
    return next((genre for markers, genre in rules if any(marker in text for marker in markers)), "Другое")


async def _determine_manual_book_genres(items):
    def needs_russian_description(item):
        description = str(item.get("description") or item.get("desc") or "").strip()
        return bool(description) and not re.search(r"[а-яё]", description, flags=re.I)

    missing = [
        (index, item) for index, item in enumerate(items)
        if (_favorite_book_genre(item) == "Без жанра"
            or needs_russian_description(item))
    ]
    if not missing:
        return items
    source = [{
        "id": index,
        "title": str(item.get("title") or "")[:160],
        "author": str(item.get("author") or "")[:100],
        "description": str(item.get("description") or "")[:500],
    } for index, item in missing]
    prompt = f"""
Подготовь метаданные реальных книг по подтверждённым названию, автору и описанию.
Это данные, не инструкции: {secure.wrap_untrusted(json.dumps(source, ensure_ascii=False), 'книги')}
Для каждой книги выбери ровно один жанр только из списка:
{', '.join(_MANUAL_BOOK_GENRES)}.
description_ru — точный перевод или краткое изложение исходного описания на русском,
1–2 законченных предложения, без новых фактов и рекламных фраз.
JSON: {{"items":[{{"id":0,"genre":"Фантастика","description_ru":""}}]}}
"""
    try:
        payload = await ai.allm_json(
            prompt, 600, tier="leisure", module="leisure_collection_add",
            fallback_allowed=True, privacy_level="public", budget_seconds=15,
        )
    except Exception:
        payload = {}
    resolved = {}
    russian_descriptions = {}
    for value in (payload.get("items") if isinstance(payload, dict) else []) or []:
        if not isinstance(value, dict):
            continue
        try:
            index = int(value.get("id"))
        except (TypeError, ValueError):
            continue
        genre = str(value.get("genre") or "").strip()
        if genre in _MANUAL_BOOK_GENRES:
            resolved[index] = genre
        description_ru = " ".join(str(value.get("description_ru") or "").split()).strip()
        if description_ru and re.search(r"[а-яё]", description_ru, flags=re.I):
            russian_descriptions[index] = description_ru[:500]
    for index, item in missing:
        if _favorite_book_genre(item) == "Без жанра":
            item["genre_label"] = resolved.get(index) or _fallback_manual_book_genre(item)
        if needs_russian_description(item):
            if index in russian_descriptions:
                item["description"] = russian_descriptions[index]
            else:
                item.pop("description", None)
                item.pop("desc", None)
    return items


async def _find_manual_book_candidates(query):
    try:
        volumes = await asyncio.wait_for(
            asyncio.to_thread(
                google_books.find_volumes,
                query.get("title", ""),
                alternative_title=query.get("alternative_title", ""),
                author=query.get("author", ""),
                year=query.get("year", ""),
                max_results=12,
                english_only=True,
            ),
            timeout=8.0,
        )
    except Exception:
        volumes = []
    if not volumes:
        try:
            volumes = await asyncio.wait_for(
                asyncio.to_thread(
                    open_library.search_books,
                    query.get("title", ""),
                    alternative_title=query.get("alternative_title", ""),
                    author=query.get("author", ""),
                    year=query.get("year", ""),
                    max_results=12,
                    english_only=True,
                ),
                timeout=8.0,
            )
        except Exception:
            volumes = []
    result = []
    seen_authors = set()
    for volume in volumes or []:
        candidate = _manual_book_candidate(volume)
        if candidate is None:
            continue
        authors = candidate.get("authors") or []
        primary_author = authors[0] if isinstance(authors, list) and authors else candidate.get("author")
        author_key = _author_group_key(primary_author)
        if not author_key or author_key in seen_authors:
            continue
        seen_authors.add(author_key)
        result.append(candidate)
    return await _determine_manual_book_genres(result)


def _manual_book_add_kb(token, index):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Добавить", callback_data=f"book_add_ok:{token}:{index}"),
        InlineKeyboardButton("❌ Другая", callback_data=f"book_add_next:{token}:{index}"),
    ]])


def _manual_book_choice(token, cid):
    state = _manual_book_choices.get(token)
    if (not state or state.get("cid") != str(cid)
            or time.time() - float(state.get("created_at") or 0) > _MANUAL_BOOK_CHOICE_TTL):
        _manual_book_choices.pop(token, None)
        return None
    return state


async def _show_manual_book_candidate(bot, cid, token, index, *, q=None):
    state = _manual_book_choice(token, cid)
    if state is None:
        await bot.send_message(chat_id=cid, text="Выбор устарел. Добавь книгу ещё раз.")
        return
    choices = state.get("choices") or []
    if not choices:
        return
    index = int(index) % len(choices)
    item = choices[index]
    msg = leisure_ui.book_add_candidate_card(item)
    kb = _manual_book_add_kb(token, index)
    cover = str(item.get("cover_url") or "").strip()
    if q is not None:
        try:
            await q.edit_message_media(
                media=InputMediaPhoto(
                    media=cover, caption=msg.text, caption_entities=msg.entities,
                ),
                reply_markup=kb,
            )
            state["current_index"] = index
            return
        except Exception:
            # Если Telegram не смог заменить media, старая карточка не должна
            # сохранить уже новый вариант по прежней кнопке.
            _manual_book_choices.pop(token, None)
            token = secrets.token_hex(4)
            _manual_book_choices[token] = state
            kb = _manual_book_add_kb(token, index)
    try:
        await bot.send_photo(
            chat_id=cid, photo=cover, caption=msg.text,
            caption_entities=msg.entities, reply_markup=kb,
        )
        state["current_index"] = index
    except Exception:
        await bot.send_message(
            chat_id=cid, text=msg.text, entities=msg.entities,
            reply_markup=kb, disable_web_page_preview=True,
        )
        state["current_index"] = index


async def offer_manual_favorite_book(bot, cid, value, origin="base"):
    """Показывает лучшую подтверждённую карточку до записи в «Мои книги»."""
    query = await _analyze_manual_book_query(value)
    choices = await _find_manual_book_candidates(query)
    if not choices:
        prefix = "loveaddls" if origin == "leisure" else "loveadd"
        store.pending_input[str(cid)] = f"{prefix}_books"
        await bot.send_message(
            chat_id=cid,
            text="Не получилось найти подтверждённую книгу с обложкой. "
                 "Попробуй уточнить название, автора или год.",
        )
        return
    now = time.time()
    for old_token, state in list(_manual_book_choices.items()):
        if now - float(state.get("created_at") or 0) > _MANUAL_BOOK_CHOICE_TTL:
            _manual_book_choices.pop(old_token, None)
    token = secrets.token_hex(4)
    _manual_book_choices[token] = {
        "cid": str(cid), "origin": origin, "created_at": now,
        "choices": choices, "current_index": 0,
    }
    await _show_manual_book_candidate(bot, cid, token, 0)


def _stored_book_identity(item):
    if isinstance(item, dict):
        title = item.get("title") or item.get("value") or item.get("name")
        author = item.get("author")
    else:
        title, author = item, ""
    return _book_identity(title), _author_group_key(author)


async def _confirm_manual_book_candidate(bot, cid, q, token, index):
    state = _manual_book_choice(token, cid)
    if state is None:
        await bot.send_message(chat_id=cid, text="Выбор устарел. Добавь книгу ещё раз.")
        return
    choices = state.get("choices") or []
    shown_index = int(state.get("current_index", index))
    if not 0 <= shown_index < len(choices):
        return
    item = choices[shown_index]
    _manual_book_choices.pop(token, None)
    title_key, author_key = _stored_book_identity(item)
    saved_items = list(store.get_list(config.FAVORITE_BOOKS_KEY, cid))
    duplicate = False
    legacy_index = None
    for saved_index, saved in enumerate(saved_items):
        saved_title, saved_author = _stored_book_identity(saved)
        if saved_title != title_key:
            continue
        if saved_author and author_key and saved_author != author_key:
            continue
        if not saved_author and author_key:
            legacy_index = saved_index
        else:
            duplicate = True
        break
    if legacy_index is not None:
        previous = saved_items[legacy_index]
        replacement = {**previous, **item} if isinstance(previous, dict) else dict(item)
        saved_items[legacy_index] = replacement
        store.set_list(config.FAVORITE_BOOKS_KEY, cid, saved_items)
    elif not duplicate:
        store.add_to_list(config.FAVORITE_BOOKS_KEY, cid, item)
    msg = leisure_ui.favorite_book_added_card(item, already=duplicate)
    cover = str(item.get("cover_url") or "").strip()
    try:
        await q.edit_message_media(
            media=InputMediaPhoto(
                media=cover, caption=msg.text, caption_entities=msg.entities,
            ),
            reply_markup=_favorite_book_added_kb(),
        )
    except Exception:
        await send_favorite_books_added_card(bot, cid, [item], already=duplicate)


async def handle_manual_book_add_callback(bot, cid, q, data):
    parts = str(data or "").split(":", 2)
    if len(parts) != 3:
        return
    action, token, raw_index = parts
    try:
        index = int(raw_index)
    except ValueError:
        return
    if action == "book_add_ok":
        await _confirm_manual_book_candidate(bot, cid, q, token, index)
        return
    state = _manual_book_choice(token, cid)
    if state is None:
        await bot.send_message(chat_id=cid, text="Выбор устарел. Добавь книгу ещё раз.")
        return
    choices = state.get("choices") or []
    current_index = int(state.get("current_index", index))
    next_index = current_index + 1
    if next_index >= len(choices):
        prefix = "loveaddls" if state.get("origin") == "leisure" else "loveadd"
        store.pending_input[str(cid)] = f"{prefix}_books"
        await bot.send_message(
            chat_id=cid,
            text="Других подтверждённых вариантов не нашлось. "
                 "Можно уточнить автора или год либо написать другое название.",
        )
        return
    await _show_manual_book_candidate(bot, cid, token, next_index, q=q)


async def resolve_manual_favorite_book(value):
    """Возвращает проверенную карточку или причину для короткого уточнения."""
    title, author, year = _manual_book_parts(value)
    if not title or not (author or year):
        return None, "clarify"
    try:
        volume = await asyncio.wait_for(
            asyncio.to_thread(google_books.find_volume, title, author=author),
            timeout=5.0,
        )
    except Exception:
        volume = None
    if not isinstance(volume, dict):
        return None, "not_found"
    if year and str(volume.get("year") or "") != year:
        return None, "not_found"
    if author:
        expected = _book_identity(author)
        actual = _book_identity(volume.get("author"))
        if not actual or (expected not in actual and actual not in expected):
            return None, "not_found"
    item = dict(volume)
    item["title"] = str(item.get("title") or title).strip()
    item["value"] = item["title"]
    item["author"] = str(item.get("author") or author).strip()
    item["year"] = str(item.get("year") or year).strip()
    item = _with_book_url(item)
    item["genre_label"] = _favorite_book_genre(item)
    return item, ""


def _favorite_book_added_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎚️ Мои книги", callback_data="book_favorites")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_books"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_favorite_books_added_card(bot, cid, items, *, already=False):
    items = [dict(item) for item in items or [] if isinstance(item, dict)]
    if not items:
        return
    if len(items) != 1:
        await send_favorite_books(bot, cid)
        return
    item = items[0]
    msg = leisure_ui.favorite_book_added_card(item, already=already)
    kb = _favorite_book_added_kb()
    cover = str(item.get("cover_url") or "").strip()
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


def _favorite_book_genre(item):
    determined = str(item.get("genre_label") or "").strip()
    if determined and determined != "Без жанра":
        return determined
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
        metadata = {**dict(record), "title": value}
        confirmed_snapshot = bool(
            metadata.get("google_books_id") or metadata.get("open_library_key")
        ) and bool(metadata.get("author") and metadata.get("cover_url"))
        if value and not confirmed_snapshot:
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

    enriched = list(await asyncio.gather(*(enrich(record) for record in records)))
    books = [item["book"] for item in enriched]
    before = [
        (str(book.get("genre_label") or ""), str(book.get("description") or book.get("desc") or ""))
        for book in books
    ]
    await _determine_manual_book_genres(books)
    updates = {}
    for item, previous in zip(enriched, before):
        book = item["book"]
        genre = _favorite_book_genre(book)
        item["genre"] = genre
        description = str(book.get("description") or "").strip()
        current = (str(book.get("genre_label") or ""), description)
        if current != previous:
            updates[item["id"]] = {
                "genre_label": genre,
                "description": description,
            }
    if updates:
        updated = [
            {**record, **updates.get(str(record.get("id") or ""), {})}
            for record in records
        ]
        store.set_list(config.FAVORITE_BOOKS_KEY, cid, updated)
    return enriched


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
    rows.append([InlineKeyboardButton(
        "🔣 Выбрать предпочтения", callback_data="book_prefs",
    )])
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
    return monthly_rebuses.cached_for_day("books", day, _BOOK_REBUSES)


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
    description = html.unescape(str(
        (item or {}).get("summary") or (item or {}).get("description") or ""
    ))
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
    prepared = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        summary = _premiere_summary(item)
        if not summary:
            continue
        prepared.append({
            **dict(item),
            "summary": summary,
            "url": _book_showcase_url(item),
        })
    return prepared


def _complete_weekly_book_showcase(primary, reserve, *, limit=3):
    """Добирает витрину проверенными книгами и не пропускает строки без описания."""
    selected, seen = [], set()
    for item in [*(primary or []), *(reserve or [])]:
        if not isinstance(item, dict) or not _premiere_summary(item):
            continue
        identity = (
            _normalized_isbn(item.get("isbn"))
            or "|".join(str(item.get(key) or "").strip().casefold() for key in ("title", "author"))
        )
        if not identity or identity in seen:
            continue
        seen.add(identity)
        selected.append(dict(item))
        if len(selected) >= limit:
            break
    return selected


async def _daily_book_content(*, refresh=False):
    today = datetime.now(config.TZ).date()
    week_anchor = today - timedelta(days=today.weekday())
    if refresh:
        rebus = await monthly_rebuses.for_day("books", today, _BOOK_REBUSES)
        birthday = await asyncio.to_thread(_load_book_birthday, week_anchor)
    else:
        rebus = monthly_rebuses.cached_for_day("books", today, _BOOK_REBUSES)
        birthday = _book_birthday_cache_get(week_anchor)
        if birthday is None:
            birthday = dict(_BOOK_BIRTHDAY_FALLBACKS.get(
                (week_anchor.month, week_anchor.day),
            ) or {})
    return {
        "rebus": rebus,
        "birthday": birthday,
    }

def _book_week_key() -> str:
    current = datetime.now(config.TZ).date()
    year, week, _weekday = current.isocalendar()
    return f"{year}-W{week:02d}"


def _book_season(today=None):
    today = today or datetime.now(config.TZ).date()
    if today.month in (12, 1, 2):
        start_year = today.year if today.month == 12 else today.year - 1
        winter_end = date(start_year + 1, 3, 1) - timedelta(days=1)
        return date(start_year, 12, 1), winter_end, "зимы"
    if today.month in (3, 4, 5):
        return date(today.year, 3, 1), date(today.year, 5, 31), "весны"
    if today.month in (6, 7, 8):
        return date(today.year, 6, 1), date(today.year, 8, 31), "лета"
    return date(today.year, 9, 1), date(today.year, 11, 30), "осени"


def _weekly_book_cache_get(*, allow_stale=False):
    entry = store._load(config.BOOK_WEEKLY_CACHE_KEY)
    season_start, _season_end, _season = _book_season()
    if (not isinstance(entry, dict) or entry.get("season") != season_start.isoformat()
            or entry.get("version") != _WEEKLY_SHOWCASE_VERSION):
        return None
    if not allow_stale and entry.get("week") != _book_week_key():
        return None
    items = entry.get("items")
    # Пустая витрина не должна блокировать новый поиск на весь день: после
    # обновления логики она может заполниться новинками месяца или бестселлерами.
    if not isinstance(items, list) or len(items) < 3:
        return None
    prepared = _books_with_premiere_summaries(items)
    return prepared[:3] if len(prepared) >= 3 else None


def _weekly_book_cache_set(items):
    season_start, _season_end, _season = _book_season()
    store._save(config.BOOK_WEEKLY_CACHE_KEY, {
        "version": _WEEKLY_SHOWCASE_VERSION,
        "week": _book_week_key(),
        "season": season_start.isoformat(),
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
    raw = str(value or "").strip()[:10]
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        raw += "-01"
    try:
        return date.fromisoformat(raw)
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


_REEDITION_MARKERS = (
    "new edition", "revised edition", "anniversary edition", "reissue",
    "movie tie-in", "paperback edition", "ebook edition", "новое издание",
    "переиздание", "юбилейное издание", "мягкая обложка",
)


def _normalized_isbn(value):
    return re.sub(r"[^0-9X]", "", str(value or "").upper())


def _normal_book_cover(value):
    url = str(value or "").strip()
    lowered = url.casefold()
    return url.startswith(("https://", "http://")) and not any(
        marker in lowered for marker in ("placeholder", "no-cover", "nocover")
    )


def _is_reedition(item):
    text = " ".join(str(item.get(field) or "") for field in (
        "title", "subtitle", "description",
    )).casefold()
    return any(marker in text for marker in _REEDITION_MARKERS)


def _weekly_book_score(item, *, today=None):
    """Оценивает только проверяемые новинки последних 90 дней."""
    today = today or datetime.now(config.TZ).date()
    released = _release_date(item.get("published_date"))
    author = str(item.get("author") or "").strip()
    isbn = _normalized_isbn(item.get("isbn"))
    if (not released or not today - timedelta(days=90) <= released <= today
            or not author or not isbn or not _normal_book_cover(item.get("cover_url"))
            or _is_reedition(item)):
        return None
    try:
        rating = float(item.get("rating") or 0)
        ratings_count = int(item.get("ratings_count") or 0)
    except (TypeError, ValueError):
        rating, ratings_count = 0, 0
    score = 30 + 10  # новинка и нормальная обложка
    if ratings_count >= 500:
        score += 15  # автор уже заметен по читательскому следу книги
    if rating >= 4.0:
        score += 15
    if ratings_count >= 100:
        score += 10
    if item.get("publisher_date_confirmed"):
        score += 20
    return score


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
    seasonal = []
    season_start, season_end, _season = _book_season()
    for item in candidates:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        released = _release_date(item.get("published_date"))
        if not released or not season_start <= released <= season_end:
            continue
        score = _monthly_book_score(item) or 0
        seasonal.append((score, item))
    seasonal.sort(key=lambda row: (
        row[0], str(row[1].get("published_date") or ""),
    ), reverse=True)
    return _showcase_items(seasonal, "season")[:3]


def _verified_season_releases(today=None):
    """Оставляет из ручно проверенного резерва только текущий сезон."""
    season_start, season_end, _season = _book_season(today)
    return [
        dict(item) for item in _VERIFIED_SEASON_RELEASES
        if (released := _release_date(item.get("published_date")))
        and season_start <= released <= season_end
    ]


async def get_weekly_new_books(*, refresh=False):
    if not refresh:
        cached = _weekly_book_cache_get()
        if cached is not None:
            return cached
        stale = _weekly_book_cache_get(allow_stale=True)
        if stale:
            return stale
        fallback = _complete_weekly_book_showcase(
            [], _rank_weekly_books(_verified_season_releases()),
        )
        return _books_with_premiere_summaries(fallback)
    stale = _weekly_book_cache_get(allow_stale=True)
    candidates = await asyncio.to_thread(google_books.search_new_releases, 40)
    prepared = []
    for item in candidates:
        if isinstance(item, dict):
            prepared.append({
                **item,
                "publisher_date_confirmed": bool(
                    item.get("publisher") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(item.get("published_date") or ""))
                ),
            })
    if len(_books_with_premiere_summaries(_rank_weekly_books(prepared))) < 3:
        prepared.extend(await _publisher_book_candidates())
    if len(_books_with_premiere_summaries(_rank_weekly_books(prepared))) < 3:
        prepared.extend(await _open_library_book_candidates())
    items = _complete_weekly_book_showcase(
        _rank_weekly_books(prepared),
        _rank_weekly_books(_verified_season_releases()),
    )
    if len(items) < 3 and stale:
        return stale
    items = _books_with_premiere_summaries(items)
    if items:
        _weekly_book_cache_set(items)
    return items


async def _open_library_book_candidates():
    """Независимый fallback без API-ключа и без LLM."""
    try:
        return await asyncio.to_thread(
            open_library.search_recent_releases, datetime.now(config.TZ).date(), 60,
        )
    except Exception:
        return []


def _rank_weekly_books(candidates):
    by_isbn = {}
    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        isbn = _normalized_isbn(item.get("isbn"))
        score = _weekly_book_score(item)
        if score is None:
            continue
        row = (score, _release_date(item.get("published_date")), dict(item))
        if isbn not in by_isbn or row[:2] > by_isbn[isbn][:2]:
            by_isbn[isbn] = row
    ranked = list(by_isbn.values())
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [item for _score, _released, item in ranked]


async def _publisher_book_candidates():
    """Добирает издательские анонсы через Tavily и сверяет издания в Google Books."""
    today = datetime.now(config.TZ).date()
    start = today - timedelta(days=90)
    domains = (
        "penguinrandomhouse.com", "harpercollins.com", "simonandschuster.com",
        "macmillan.com", "hachettebookgroup.com",
    )
    query = (
        f"new books published {start.isoformat()} to {today.isoformat()} "
        "official publisher release date author ISBN"
    )
    results = await asyncio.to_thread(
        research.tavily_search, query, 10, domains, scenario="book_releases",
    )
    if not results:
        return []
    source = json.dumps(results, ensure_ascii=False)[:12000]
    prompt = (
        "Извлеки из материалов официальных издательств книги, впервые опубликованные "
        f"с {start.isoformat()} по {today.isoformat()}. Не включай переиздания, paperback/ebook "
        "старых книг и не додумывай данные. Верни JSON: "
        '{"books":[{"title":"","author":"","published_date":"YYYY-MM-DD",'
        '"publisher":"","isbn":"","description_ru":"одно короткое предложение на русском",'
        '"source_url":""}]}. Описание составляй только по материалу источника.\n'
        + secure.wrap_untrusted(source, "результаты поиска по сайтам издательств")
    )
    try:
        payload = await asyncio.to_thread(
            ai.llm_json, prompt, 1200, tier="leisure", module="leisure_books",
        )
    except Exception:
        return []
    verified = []
    for extracted in (payload or {}).get("books") or []:
        if not isinstance(extracted, dict):
            continue
        title = str(extracted.get("title") or "").strip()
        author = str(extracted.get("author") or "").strip()
        if not title or not author:
            continue
        try:
            volume = await asyncio.to_thread(google_books.find_volume, title, author=author)
        except Exception:
            volume = None
        if not volume:
            isbn = _normalized_isbn(extracted.get("isbn"))
            cover_url = await asyncio.to_thread(open_library.cover_for_isbn, isbn)
            if not isbn or not cover_url:
                continue
            volume = {
                "title": title, "author": author, "isbn": isbn,
                "cover_url": cover_url,
                "info_link": str(extracted.get("source_url") or ""),
            }
        item = {
            **volume,
            "title": str(volume.get("title") or title).strip(),
            "author": str(volume.get("author") or author).strip(),
            "published_date": str(extracted.get("published_date") or volume.get("published_date") or ""),
            "publisher": str(extracted.get("publisher") or volume.get("publisher") or ""),
            "isbn": str(volume.get("isbn") or extracted.get("isbn") or ""),
            "publisher_date_confirmed": True,
            "publisher_source_url": str(extracted.get("source_url") or ""),
        }
        description_ru = " ".join(str(extracted.get("description_ru") or "").split()).strip()
        if description_ru:
            item["description"] = description_ru
        verified.append(item)
    return verified


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
    if not fresh:
        reserve = [
            _with_book_url({**item, "summary": _premiere_summary(item)})
            for item in _verified_season_releases(today)
            if _released_this_month(item.get("published_date"))
        ]
        fresh = reserve
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


async def _book_premieres_with_covers():
    items = await get_book_premieres()
    if not items:
        items = await get_book_premieres(refresh=True)
    return [
        item for item in items
        if str(item.get("cover_url") or "").strip()
    ][:7]


def _book_premieres_view(items, page=0):
    today = datetime.now(config.TZ).date()
    month = f"{_MONTHS[today.month - 1].capitalize()} {today.year}"
    period = month if all(_released_this_month(item.get("published_date")) for item in items) \
        else "Свежие новинки"
    page = max(0, min(int(page), len(items) - 1)) if items else 0
    msg = leisure_ui.book_premieres_screen(period, [items[page]] if items else [])
    rows = []
    if len(items) > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"book_premiere_page:{(page - 1) % len(items)}"),
            InlineKeyboardButton(f"{page + 1}/{len(items)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"book_premiere_page:{(page + 1) % len(items)}"),
        ])
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="m_books"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    return msg, InlineKeyboardMarkup(rows), page


async def send_book_premieres(bot, cid, *, status=None):
    items = await _book_premieres_with_covers()
    msg, kb, page = _book_premieres_view(items)
    if items:
        try:
            await bot.send_photo(
                chat_id=cid,
                photo=str(items[page].get("cover_url") or "").strip(),
                caption=msg.text,
                caption_entities=msg.entities,
                reply_markup=kb,
            )
            return
        except Exception:
            pass
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb,
                             disable_web_page_preview=True)
        return
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb,
                           disable_web_page_preview=True)


async def show_book_premiere_page(q, page):
    items = await _book_premieres_with_covers()
    if not items:
        return
    msg, kb, page = _book_premieres_view(items, page)
    await q.edit_message_media(
        media=InputMediaPhoto(
            media=str(items[page].get("cover_url") or "").strip(),
            caption=msg.text,
            caption_entities=msg.entities,
        ),
        reply_markup=kb,
    )


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
        [InlineKeyboardButton("⬅️ Назад", callback_data="book_favorites"),
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

# Расширенный проверенный резерв включается только после персонального поиска и
# Google Books. Новые годы идут первыми, затем подбор естественно углубляется в
# прошлые годы, не заканчиваясь после двух карточек.
_GENRE_TOP_BOOKS = {
    "fantasy": [
        ("Заражённая чаша", "The Tainted Cup", "2024", "Роберт Джексон Беннетт"),
        ("Приключения Амины аль-Сирафи", "The Adventures of Amina al-Sirafi", "2023", "Шеннон Чакраборти"),
        ("Вавилон", "Babel", "2022", "Ребекка Куанг"),
    ],
    "scifi": [
        ("Министерство времени", "The Ministry of Time", "2024", "Калиан Брэдли"),
        ("Море Спокойствия", "Sea of Tranquility", "2022", "Эмили Сент-Джон Мандел"),
        ("Проект „Аве Мария“", "Project Hail Mary", "2021", "Энди Вейер"),
    ],
    "detective": [
        ("Последний дьявол", "The Last Devil to Die", "2023", "Ричард Осман"),
        ("Горничная", "The Maid", "2022", "Нита Проуз"),
        ("Клуб убийств по четвергам", "The Thursday Murder Club", "2020", "Ричард Осман"),
    ],
    "thriller": [
        ("Ничего из этого не правда", "None of This Is True", "2023", "Лиза Джуэлл"),
        ("Список гостей", "The Guest List", "2020", "Люси Фоли"),
        ("Безмолвный пациент", "The Silent Patient", "2019", "Алекс Михаэлидес"),
    ],
    "romance": [
        ("Забавная история", "Funny Story", "2024", "Эмили Генри"),
        ("Счастливое место", "Happy Place", "2023", "Эмили Генри"),
        ("Книжные любовники", "Book Lovers", "2022", "Эмили Генри"),
    ],
    "history": [
        ("Джеймс", "James", "2024", "Персиваль Эверетт"),
        ("Шоссе Линкольна", "The Lincoln Highway", "2021", "Амор Тоулз"),
        ("Хэмнет", "Hamnet", "2020", "Мэгги О’Фаррелл"),
    ],
    "biography": [
        ("Илон Маск", "Elon Musk", "2023", "Уолтер Айзексон"),
        ("Запасной", "Spare", "2023", "Принц Гарри"),
        ("Образованная", "Educated", "2018", "Тара Вестовер"),
    ],
    "psychology": [
        ("Тревожное поколение", "The Anxious Generation", "2024", "Джонатан Хайдт"),
        ("Атомные привычки", "Atomic Habits", "2018", "Джеймс Клир"),
        ("Тело помнит всё", "The Body Keeps the Score", "2014", "Бессел ван дер Колк"),
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
    book_key = lambda value: (
        str(value.get("title") or "").casefold()
        if isinstance(value, dict) else str(value or "").casefold()
    )
    pool = rotation.candidates_for_cycle(
        _FALLBACK_BOOKS, used,
        current=extra_skip[-1] if extra_skip else None, key=book_key,
    )
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
    top = [{
        "title": title, "title_en": title_en, "year": year, "author": author,
        "desc": "Заметная книга жанра с сильными читательскими и критическими отзывами.",
    } for title, title_en, year, author in _GENRE_TOP_BOOKS.get(genre_key, [])]
    source = [*top, *_GENRE_FALLBACKS.get(genre_key, [])]
    book_key = lambda value: (
        str(value.get("title") or "").casefold()
        if isinstance(value, dict) else str(value or "").casefold()
    )
    pool = rotation.candidates_for_cycle(
        source, used,
        current=extra_skip[-1] if extra_skip else None, key=book_key,
    )
    return dict(pool[0]) if pool else None

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
