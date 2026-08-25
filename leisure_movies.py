from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from ui.constants import ui_label
import asyncio
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import requests

import config
import store
import settings
import tmdb
import movie_engine
import recommendation_stoplist
import inclusive_recommendations
import verify
import tracking
import local_cinema
import monthly_rebuses
from util import _MONTHS
from ui import leisure as leisure_ui
from leisure_collection import (
    canonical_movie_label,
    content_recommend,
    movie_title_for_lookup,
    normalize_movie_items,
)
from module_binding import bind_functions as _bind_functions
import movie_discovery as _movie_discovery

_log = logging.getLogger(__name__)
_DISCOVERY_DEPENDENCIES = (
    InputMediaPhoto, time, timedelta, quote_plus, requests, local_cinema, _MONTHS,
    movie_title_for_lookup,
)


_CINEMA_BIRTHDAY_LOCK = threading.Lock()
_CINEMA_BIRTHDAY_CACHE_VERSION = 3
_MOVIE_PREMIERES_CACHE_VERSION = 5
_FAVORITE_MOVIE_PAGE_SIZE = 8
_FAVORITE_MOVIE_VIEW_TTL = 24 * 3600
_favorite_movie_views = {}
_FAVORITE_MOVIE_GENRES = (
    "Комедия", "Ужасы", "Фантастика", "Триллер", "Романтика", "Драма",
)


def _favorite_movie_genre(metadata):
    genres = [
        part.strip().casefold()
        for part in str((metadata or {}).get("genres") or "").split(",")
        if part.strip()
    ]
    aliases = {
        "комедия": "Комедия", "comedy": "Комедия",
        "ужасы": "Ужасы", "horror": "Ужасы",
        "фантастика": "Фантастика", "фэнтези": "Фантастика",
        "science fiction": "Фантастика", "fantasy": "Фантастика", "анимация": "Фантастика",
        "триллер": "Триллер", "детектив": "Триллер", "криминал": "Триллер",
        "боевик": "Триллер", "thriller": "Триллер", "mystery": "Триллер",
        "crime": "Триллер", "action": "Триллер",
        "романтика": "Романтика", "мелодрама": "Романтика", "romance": "Романтика",
        "драма": "Драма", "история": "Драма", "документальный": "Драма",
        "drama": "Драма", "history": "Драма", "documentary": "Драма",
    }
    return next((aliases[genre] for genre in genres if genre in aliases), "Драма")
_CINEMA_REBUSES = (
    {
        "emoji": "🦈 🌊 👨‍🔬",
        "answer": "Челюсти",
        "fact": "Стивен Спилберг снимал многие сцены с точки зрения акулы, пока механическая модель не работала.",
    },
    {
        "emoji": "🕶️ 💊 🤖",
        "answer": "Матрица",
        "fact": "Сёстры Вачовски отправили актёров на многомесячную подготовку по боевым искусствам.",
    },
    {
        "emoji": "🛳️ 🧊 💔",
        "answer": "Титаник",
        "fact": "Джеймс Кэмерон лично погружался к обломкам лайнера для подводных съёмок.",
    },
    {
        "emoji": "🧙 💍 🌋",
        "answer": "Властелин колец",
        "fact": "Питер Джексон снимал три части истории одновременно в Новой Зеландии.",
    },
    {
        "emoji": "🦖 🏝️ 🚙",
        "answer": "Парк юрского периода",
        "fact": "Стивен Спилберг соединил полноразмерную аниматронику и компьютерную графику.",
    },
)
_BIRTHDAY_FALLBACKS = {
    (8, 4): {
        "name": "Грета Гервиг",
        "birth": "1983-08-04",
        "role": "режиссёр и актриса",
        "fact": "«Леди Бёрд» принесла ей две номинации на «Оскар».",
    },
}

def _display_title(it, tm):
    """Название, которое реально показано пользователю (TMDb если есть, иначе от LLM)."""
    name = (tm.get("name") if tm else "") or it.get("title", "")
    year = (tm.get("year") if tm else "") or ""
    return f"{name} ({year})" if year else name

def _movie_card(it, tm):
    return leisure_ui.movie_card(it, tm)


def _movie_home_only_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")]])


def _favorite_movie_added_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎚️ Моё кино", callback_data="movie_favorites")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_movie"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_favorite_movies_added_card(bot, cid, titles):
    """Подтверждает ручное добавление фильма отдельной полезной карточкой."""
    titles = [str(title or "").strip() for title in titles or [] if str(title or "").strip()]
    if not titles:
        return
    if len(titles) == 1:
        try:
            tm = await asyncio.wait_for(
                asyncio.to_thread(tmdb.lookup_title, movie_title_for_lookup(titles[0])), timeout=4.0,
            ) if config.TMDB_API_KEY else None
        except Exception:
            tm = None
        msg = leisure_ui.favorite_movie_added_card(titles[0], tm)
    else:
        msg = leisure_ui.favorite_movies_added_card(titles)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=_favorite_movie_added_kb())


def _favorite_movie_value(record):
    return str(record.get("value") or record.get("name") or record.get("title") or "").strip()


async def _favorite_movie_records(cid):
    records = store.ensure_list_ids(config.FAVORITE_MOVIES_KEY, cid)
    semaphore = asyncio.Semaphore(6)

    async def enrich(record):
        value = _favorite_movie_value(record)
        title = movie_title_for_lookup(value)
        metadata = None
        if config.TMDB_API_KEY and title:
            async with semaphore:
                try:
                    metadata = await asyncio.wait_for(
                        asyncio.to_thread(tmdb.lookup_title, title), timeout=5.0,
                    )
                except Exception:
                    metadata = None
        metadata = dict(metadata or {})
        display_title = str(metadata.get("name") or title or value).strip()
        genre = _favorite_movie_genre(metadata)
        return {
            "id": str(record.get("id") or ""),
            "value": value,
            "title": display_title,
            "genre": genre,
            "tm": metadata,
        }

    return list(await asyncio.gather(*(enrich(record) for record in records)))


def _new_favorite_movie_view(cid, records):
    now = time.time()
    for token, view in list(_favorite_movie_views.items()):
        if now - view.get("created_at", 0) > _FAVORITE_MOVIE_VIEW_TTL:
            _favorite_movie_views.pop(token, None)
    token = secrets.token_hex(3)
    genres = {}
    for record in records:
        genres.setdefault(record["genre"], []).append(record)
    for items in genres.values():
        items.sort(key=lambda item: item["title"].casefold())
    ordered_genres = [genre for genre in _FAVORITE_MOVIE_GENRES if genre in genres]
    view = {
        "cid": str(cid),
        "created_at": now,
        "genres": [(genre, genres[genre]) for genre in ordered_genres],
    }
    _favorite_movie_views[token] = view
    return token, view


def _favorite_movie_view(cid, token):
    view = _favorite_movie_views.get(token)
    if not view or view.get("cid") != str(cid):
        return None
    if time.time() - view.get("created_at", 0) > _FAVORITE_MOVIE_VIEW_TTL:
        _favorite_movie_views.pop(token, None)
        return None
    return view


async def send_favorite_movies(bot, cid, q=None):
    records = await _favorite_movie_records(cid)
    token, view = _new_favorite_movie_view(cid, records)
    summaries = [
        {"genre": genre, "titles": [item["title"] for item in items]}
        for genre, items in view["genres"]
    ]
    msg = leisure_ui.favorite_movies_home(len(records), summaries)
    rows = [
        [InlineKeyboardButton(f"{genre} · {len(items)}", callback_data=f"mfg:{token}:{index}:0")]
        for index, (genre, items) in enumerate(view["genres"])
    ]
    rows.append([InlineKeyboardButton("🆕 Добавить фильм", callback_data="as_loveadd_movies")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_movie"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_favorite_movie_genre(bot, cid, token, genre_index, page=0, q=None):
    view = _favorite_movie_view(cid, token)
    if view is None or not 0 <= genre_index < len(view["genres"]):
        await send_favorite_movies(bot, cid, q=q)
        return
    genre, items = view["genres"][genre_index]
    page = max(0, min(int(page), len(items) - 1))
    item = items[page]
    _title, msg = _movie_card({"title": item["title"]}, item["tm"])
    rows = []
    if len(items) > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"mfg:{token}:{genre_index}:{(page - 1) % len(items)}"),
            InlineKeyboardButton(f"{page + 1}/{len(items)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"mfg:{token}:{genre_index}:{(page + 1) % len(items)}"),
        ])
    rows.append([InlineKeyboardButton(
        "❌ Удалить", callback_data=f"mfd:{token}:{item['id'][:8]}:{genre_index}:{page}",
    )])
    rows.append([InlineKeyboardButton("🆕 Добавить фильм", callback_data="as_loveadd_movies")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="movie_favorites"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    kb = InlineKeyboardMarkup(rows)
    poster = str(item["tm"].get("poster") or "").strip()
    if q is not None and poster:
        try:
            await q.edit_message_media(
                media=InputMediaPhoto(
                    media=poster, caption=msg.text, caption_entities=msg.entities,
                ),
                reply_markup=kb,
            )
            return
        except Exception:
            pass
    if poster:
        try:
            await bot.send_photo(
                chat_id=cid, photo=poster, caption=msg.text,
                caption_entities=msg.entities, reply_markup=kb,
            )
            return
        except Exception:
            pass
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb,
    )


def _favorite_movie_from_view(cid, token, short_id):
    view = _favorite_movie_view(cid, token)
    if view is None:
        return None
    return next((item for _genre, items in view["genres"] for item in items
                 if item["id"].startswith(short_id)), None)


async def send_favorite_movie_card(bot, cid, token, short_id, genre_index, page):
    item = _favorite_movie_from_view(cid, token, short_id)
    if item is None:
        await send_favorite_movies(bot, cid)
        return
    _title, msg = _movie_card({"title": item["title"]}, item["tm"])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"mfd:{token}:{short_id}:{genre_index}:{page}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"mfg:{token}:{genre_index}:{page}"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    poster = item["tm"].get("poster")
    if poster:
        try:
            await bot.send_photo(chat_id=cid, photo=poster, caption=msg.text,
                                 caption_entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_favorite_movie_delete_confirmation(bot, cid, token, short_id, genre_index, page, q=None):
    item = _favorite_movie_from_view(cid, token, short_id)
    if item is None:
        await send_favorite_movies(bot, cid, q=q)
        return
    text = f"Удалить «{item['title']}»?"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "❌ Удалить",
            callback_data=f"mfdok:{token}:{short_id}:{genre_index}:{page}",
        )],
        [InlineKeyboardButton("Отмена", callback_data=f"mfg:{token}:{genre_index}:{page}"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    if q is not None:
        try:
            if getattr(q.message, "photo", None):
                await q.message.edit_caption(caption=text, reply_markup=kb)
            else:
                await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def delete_favorite_movie(bot, cid, token, short_id, genre_index=None, page=0, q=None):
    item = _favorite_movie_from_view(cid, token, short_id)
    if item is not None:
        store.remove_from_list_by_ids(config.FAVORITE_MOVIES_KEY, cid, [item["id"]])
    view = _favorite_movie_view(cid, token)
    if view is not None and genre_index is not None and 0 <= genre_index < len(view["genres"]):
        _genre, items = view["genres"][genre_index]
        items[:] = [value for value in items if not value["id"].startswith(short_id)]
        if items:
            await send_favorite_movie_genre(
                bot, cid, token, genre_index, min(int(page), len(items) - 1), q=q,
            )
            return
    _favorite_movie_views.pop(token, None)
    await send_favorite_movies(bot, cid, q=q)


def _movie_kb(i, category=None):
    """Клавиатура карточки кино с быстрым подбором по жанру.

    category используется только для контекста подбора.
    """
    rows = [
        [InlineKeyboardButton("✨ Другое кино", callback_data=f"movie_no_{i}")],
        [InlineKeyboardButton("🎭 По жанру", callback_data="movie_genre_menu")],
    ]
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="m_movie"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    return InlineKeyboardMarkup(rows)


# Шесть популярных жанров для быстрого меню «По жанру».
_GENRE_MENU = [
    ("Комедия", 35), ("Ужасы", 27),
    ("Фантастика", 878), ("Триллер", 53),
    ("Романтика", 10749), ("Драма", 18),
]

def _movie_genre_menu_kb():
    rows = []
    buttons = [InlineKeyboardButton(label, callback_data=f"movie_g_{gid}")
               for label, gid in _GENRE_MENU]
    for button in buttons:
        rows.append([button])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_movie"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)

MIN_TMDB_RATING = 7.0

_MOVIE_FALLBACKS = [
    {"title": "Решение уйти", "title_en": "Decision to Leave", "hook": "изящный детектив с холодной романтикой и сильной режиссурой"},
    {"title": "Пылающий", "title_en": "Burning", "hook": "медленный корейский триллер с тревожной пустотой и недосказанностью"},
    {"title": "Разделение", "title_en": "Severance", "hook": "сериал про офисный абсурд, контроль и очень цепкую загадку"},
    {"title": "Медведь", "title_en": "The Bear", "hook": "нервный сериал про работу, семью и попытку собрать жизнь заново"},
    {"title": "Патерсон", "title_en": "Paterson", "hook": "тихое кино про ритм дней, наблюдательность и внутреннюю опору"},
]

def _movie_used(cid):
    """Множество названий, которые нельзя повторять: любимые и чёрный список."""
    wl = store.get_list(config.FAVORITE_MOVIES_KEY, cid)
    blocked = recommendation_stoplist.values(cid, "movie")
    used = set()
    for x in list(wl) + blocked:
        used.add((x if isinstance(x, str) else str(x)).lower())
    return used

def _fallback_movie_items(cid):
    used = _movie_used(cid)
    return [
        dict(x) for x in _MOVIE_FALLBACKS
        if x["title"].lower() not in used and x["title_en"].lower() not in used
    ]

def _normalize_movie_items(items):
    """LLM иногда возвращает строки или неполные объекты вместо ожидаемых dict."""
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, str):
            title = it.strip()
            if title:
                out.append({"title": title, "title_en": "", "hook": ""})
            continue
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or it.get("name") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "title_en": str(it.get("title_en") or it.get("original_title") or it.get("name_en") or "").strip(),
            "hook": str(it.get("hook") or it.get("why") or it.get("desc") or "").strip(),
        })
    return out

def _pick_good_movie(items, used_titles, prefs=None):
    """Выбирает качественный вариант с приоритетом действующих настроек кино."""
    used = {str(u).lower() for u in used_titles}
    accepted = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("title", "").lower() in used:
            continue
        tm = tmdb.lookup_title(it.get("title", ""), it.get("title_en", "")) if config.TMDB_API_KEY else None
        disp = _display_title(it, tm).lower()
        if disp in used:
            continue
        if not config.TMDB_API_KEY:
            return it, tm
        rating = (tm or {}).get("rating") or 0
        vote_count = int((tm or {}).get("vote_count") or 0)
        if rating >= MIN_TMDB_RATING and vote_count >= movie_engine.MIN_VOTE_COUNT:
            accepted.append((movie_engine._score(tm or {}, {
                "genres": {}, "countries": {}, "kind_pref": None,
            }, prefs), it, tm))
    if not accepted:
        return None, None
    _score, item, tm = max(accepted, key=lambda row: row[0])
    return item, tm

async def _send_movie_card(bot, cid, it, i, tm="__lookup__", category=None, status=None):
    it = it if isinstance(it, dict) else {"title": str(it)}
    if tm == "__lookup__":
        try:
            remaining = tracking.remaining_action_seconds()
            timeout = min(5.0, remaining - 0.5) if remaining is not None else 5.0
            if timeout <= 0.2:
                raise asyncio.TimeoutError
            tm = await asyncio.wait_for(
                asyncio.to_thread(
                    tmdb.lookup_title, it.get("title", ""), it.get("title_en", "")),
                timeout=timeout,
            ) if config.TMDB_API_KEY else None
        except Exception:
            tm = None
    title, msg = _movie_card(it, tm)
    kb = _movie_kb(i, category=category)
    if tm and tm.get("poster"):
        try:
            await bot.send_photo(chat_id=cid, photo=tm["poster"], caption=msg.text, caption_entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb)
        return
    try:
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
    except Exception:
        await bot.send_message(chat_id=cid, text=msg.text, reply_markup=kb)


def _movie_cache_signature(cid):
    """Только параметры, которые действительно меняют дневной подбор."""
    return {
        "preferences": _movie_prefs(cid),
        "favorites": sorted(_movie_used(cid)),
    }


def _cached_movie(cid):
    entry = (store._load(config.MOVIE_RECO_CACHE_KEY) or {}).get(str(cid)) or {}
    today = datetime.now(config.TZ).date().isoformat()
    item = entry.get("item")
    if (entry.get("date") != today or entry.get("signature") != _movie_cache_signature(cid)
            or not isinstance(item, dict) or not str(item.get("title") or "").strip()):
        return None
    tm = entry.get("tm")
    return dict(item), dict(tm) if isinstance(tm, dict) else None


def _cache_movie(cid, it, tm):
    cached_tm = dict(tm or {})
    if isinstance(cached_tm.get("anchors"), set):
        cached_tm["anchors"] = sorted(cached_tm["anchors"])

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[str(cid)] = {
            "date": datetime.now(config.TZ).date().isoformat(),
            "signature": _movie_cache_signature(cid),
            "item": dict(it or {}),
            "tm": cached_tm,
        }
        return data, None

    store.mutate_kv(config.MOVIE_RECO_CACHE_KEY, mutate)


async def get_current_movie(cid):
    """Возвращает подготовленную рекомендацию дня, не отмечая её просмотренной."""
    cached = _cached_movie(cid)
    if cached:
        return cached

    seen = store.get_list(config.FAVORITE_MOVIES_KEY, cid)
    if not seen:
        prefs = _movie_prefs(cid)
        requested_kind = prefs.get("type_pref") or "movie"
        excluded = movie_engine._excluded_norms(cid)
        requested_kinds = [requested_kind]
        if prefs.get("type_pref"):
            requested_kinds.append("tv" if requested_kind == "movie" else "movie")
        candidates = []
        for candidate_kind in requested_kinds:
            try:
                candidates = await asyncio.to_thread(
                    tmdb.discover, candidate_kind, None,
                    max(MIN_TMDB_RATING, float(prefs.get("min_rating") or MIN_TMDB_RATING)), 2000)
            except Exception as error:
                _log.info("movie home TMDb unavailable: %s", type(error).__name__)
                candidates = []
            candidates = [movie for movie in candidates
                          if movie_engine._norm(movie.get("name")) not in excluded
                          and int(movie.get("vote_count") or 0) >= movie_engine.MIN_VOTE_COUNT]
            if candidates:
                break
        candidates = movie_engine.rank(candidates, {
            "genres": {}, "countries": {}, "kind_pref": None,
        }, prefs)
        tm = candidates[0] if candidates else None
        it = {"title": (tm or {}).get("name", ""),
              "hook": "Свежий фильм с хорошими оценками — можно начать с него."} if tm else None
        if it is None:
            fallbacks = _fallback_movie_items(cid)
            it = fallbacks[0] if fallbacks else None
            tm = None
    else:
        it, tm = await _tmdb_engine_pick(cid)
        if it is None:
            it, tm = await _llm_movie_pick(cid, _movie_used(cid))
    if not it:
        return None, None
    _cache_movie(cid, it, tm)
    return it, tm

async def send_recos(bot, cid, kind, status=None):
    if kind == "book":
        import leisure_books
        await leisure_books.send_books_reco(bot, cid, status=status)
        return
    prefs = _movie_prefs(cid)
    inclusive_pick = (
        await _inclusive_movie_pick(cid, prefs)
        if inclusive_recommendations.is_due(cid, "movie") else None
    )
    # Даже без любимых открытие раздела должно дать современную качественную
    # рекомендацию; вкус начнёт уточняться после первых отметок в любимом.
    # Явный запрос всегда получает новый вариант, а не утреннюю карточку из кэша.
    seen = store.get_list(config.FAVORITE_MOVIES_KEY, cid)
    if inclusive_pick:
        it, tm = inclusive_pick
    elif not seen:
        requested_kind = prefs.get("type_pref") or "movie"
        excluded = movie_engine._excluded_norms(cid)
        requested_kinds = [requested_kind]
        if prefs.get("type_pref"):
            requested_kinds.append("tv" if requested_kind == "movie" else "movie")
        candidates = []
        for candidate_kind in requested_kinds:
            candidates = await asyncio.to_thread(
                tmdb.discover, candidate_kind, None,
                max(MIN_TMDB_RATING, float(prefs.get("min_rating") or MIN_TMDB_RATING)), 2000)
            candidates = [movie for movie in candidates
                          if movie_engine._norm(movie.get("name")) not in excluded
                          and int(movie.get("vote_count") or 0) >= movie_engine.MIN_VOTE_COUNT]
            if candidates:
                break
        candidates = movie_engine.rank(candidates, {
            "genres": {}, "countries": {}, "kind_pref": None,
        }, prefs)
        tm = candidates[0] if candidates else None
        it = {"title": (tm or {}).get("name", ""),
              "hook": "Свежий фильм с хорошими оценками — можно начать с него."} if tm else None
    else:
        it, tm = await _tmdb_engine_pick(cid)
    if it is None:
        it, tm = await _llm_movie_pick(cid, _movie_used(cid))
    if not it:
        await bot.send_message(
            chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз.",
            reply_markup=_movie_home_only_kb()); return
    disp = _display_title(it, tm)
    movie_engine.mark_shown(cid, disp)
    store.last_recos[str(cid)] = {"kind": kind, "items": [disp]}
    store.last_source[str(cid)] = "Кино"
    store.last_answer[str(cid)] = f"{disp} - {it.get('hook','')}"
    _cache_movie(cid, it, tm)
    inclusive = inclusive_recommendations.is_inclusive(
        "movie", it.get("title"), (tm or {}).get("name"), (tm or {}).get("name_en"),
    )
    if tm is not None and inclusive:
        tm = {**tm, "lgbt": True}
    inclusive_recommendations.record(cid, "movie", inclusive)
    await _send_movie_card(bot, cid, it, 0, tm=tm, status=status)


# Discovery, daily cinema and premieres live in movie_discovery.py.

def _movie_prefs(cid):
    """Предпочтения кино из настроек → dict для движка (приоритеты, не запреты)."""
    return {
        "type_pref": settings.get(cid, "movie_type_pref", "") or None,
        "recency": settings.get(cid, "movie_recency", "") or None,
        "min_rating": _as_float(settings.get(cid, "movie_min_rating", None)),
    }


_INCLUSIVE_MOVIE_TITLES = (
    "Moonlight", "Portrait of a Lady on Fire", "Nimona",
    "Heartstopper", "It's a Sin", "Pose",
)


async def _inclusive_movie_pick(cid, prefs):
    """Проверенный ЛГБТ-проект, подходящий типу и минимальному рейтингу."""
    excluded = movie_engine._excluded_norms(cid)
    type_pref = prefs.get("type_pref")
    min_rating = float(prefs.get("min_rating") or 0)
    for title in _INCLUSIVE_MOVIE_TITLES:
        try:
            tm = await asyncio.to_thread(tmdb.lookup_title, title)
        except Exception:
            continue
        if not tm or movie_engine._norm(tm.get("name")) in excluded:
            continue
        if type_pref and tm.get("kind") != type_pref:
            continue
        if float(tm.get("rating") or 0) < min_rating:
            continue
        tm = {**tm, "lgbt": True}
        it = {
            "title": tm.get("name") or title,
            "title_en": tm.get("name_en") or title,
            "hook": "ЛГБТ-история с сильными отзывами и близким тебе настроением.",
        }
        return it, tm
    return None


def _as_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


async def _tmdb_engine_pick(cid, prefs=None):
    """Возвращает (it, tm) из TMDb-движка или (None, None), если данных мало.

    tm — нормализованный TMDb-dict кандидата (совместим с карточкой), дополненный
    деталями и полем because. it — лёгкий dict с title/hook для совместимости.
    """
    if prefs is None:
        prefs = _movie_prefs(cid)
    try:
        cands, taste = await asyncio.to_thread(movie_engine.recommend, cid, prefs)
    except Exception:
        return None, None
    if not cands:
        return None, None
    c = cands[0]
    return _candidate_to_card(cid, c)


def _candidate_to_card(cid, c, reason=None):
    """Обогащает кандидата деталями и строит (it, tm) для карточки.

    reason — явный источник рекомендации, если не «обычная» (Recommendations/Similar
    по любимому): {"kind": "genre", "label": "Комедия"}.
    Если reason не передан, источник — anchor-поля кандидата (because/via/anchors).

    ВАЖНО: tmdb.detail() отдаёт объект из общего TTL-кэша (по ссылке, не копию) —
    его нельзя мутировать напрямую, иначе персональное поле «because» одного
    пользователя утечёт в карточку другого пользователя/другого запроса для того же
    тайтла (баг: «Потому что понравился Элита» у сериала, никак не связанного с Элитой).
    Поэтому здесь всегда делаем dict(det) перед добавлением полей.
    """
    tm = dict(c)
    try:
        det = tmdb.detail(c.get("id"), c.get("kind"))
        if det:
            det = dict(det)  # копия — не мутируем общий кэш tmdb.detail
            tm = det
    except Exception:
        pass
    if reason is not None:
        tm["reason"] = reason
    else:
        tm["because"] = c.get("because")
        tm["via"] = c.get("via")
        tm["shared_genres"] = c.get("shared_genres") or []
        tm["anchors"] = c.get("anchors")
    it = {"title": tm.get("name", ""), "title_en": tm.get("name_en", ""),
          "hook": _reason_text(tm)}
    return it, tm


def _reason_text(tm):
    """Причина рекомендации — плоский текст (для it["hook"], фолбэков без карточки-TMDb)."""
    reason = tm.get("reason")
    if reason:
        return _reason_label(reason)
    because = tm.get("because")
    if because:
        if tm.get("via") == "similar":
            genres = ", ".join(tm.get("shared_genres") or [])
            return f"Подходит по жанрам: {genres}" if genres else ""
        return f"Потому что вам понравился «{because}»"
    return ""


def _reason_label(reason):
    kind = reason.get("kind")
    label = reason.get("label", "")
    if kind == "genre":
        return f"Подборка в жанре «{label}»"
    return ""


async def _llm_movie_pick(cid, used):
    """Старый LLM-путь как фолбэк движка."""
    items = []
    for _ in range(2):
        try:
            data = await asyncio.to_thread(content_recommend, "movie", str(cid))
            items = _normalize_movie_items(data.get("items", []) if isinstance(data, dict) else [])
        except Exception:
            items = []
        if items:
            break
    if not items:
        items = _fallback_movie_items(cid)
    if not items:
        return None, None
    remaining = tracking.remaining_action_seconds()
    timeout = min(5.0, remaining - 0.5) if remaining is not None else 5.0
    if timeout <= 0.2:
        return items[0], None
    try:
        picked = await asyncio.wait_for(
            asyncio.to_thread(_pick_good_movie, items, used, _movie_prefs(cid)), timeout=timeout)
    except Exception:
        return items[0], None
    if picked[0] is not None:
        return picked
    fallbacks = _fallback_movie_items(cid)
    if fallbacks != items:
        remaining = tracking.remaining_action_seconds()
        timeout = min(5.0, remaining - 0.5) if remaining is not None else 5.0
        if timeout <= 0.2:
            return fallbacks[0], None
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_pick_good_movie, fallbacks, used, _movie_prefs(cid)), timeout=timeout)
        except Exception:
            return fallbacks[0], None
    return None, None

async def movie_dislike(bot, cid, i):
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        recommendation_stoplist.add(cid, "movie", title, "hidden")
    await _advance_movie(bot, cid)

async def _advance_movie(bot, cid):
    """Загрузить следующую рекомендацию кино и показать карточку.

    Если текущая сессия рекомендаций привязана к жанру (last_recos["category"],
    проставлено в _show_discovered), следующая карточка ОБЯЗАНА остаться в той же категории —
    «Другое кино»/«В любимые»/«Уже видел» внутри «Комедии» не должны сбрасывать
    подбор на общий алгоритм. Без category — обычный путь Recommendations/Similar по любимым.
    """
    rec = store.last_recos.get(str(cid), {"kind": "movie", "items": []})
    category = rec.get("category")
    if category:
        it, tm = await _advance_in_category(cid, category)
        if not it:
            label = category["reason"]["label"]
            text = f"В этом жанре «{label}» пока не нашёл нового. Попробуй другой."
            kb = _movie_genre_menu_kb()
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
            return
    else:
        it, tm = await _tmdb_engine_pick(cid)
        if it is None:
            used = _movie_used(cid) | {str(x).lower() for x in rec["items"]}
            it, tm = await _llm_movie_pick(cid, used)
    if not it:
        await bot.send_message(
            chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз.",
            reply_markup=_movie_home_only_kb()); return
    disp = _display_title(it, tm)
    movie_engine.mark_shown(cid, disp)
    rec["items"].append(disp)
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    await _send_movie_card(bot, cid, it, ni, tm=tm, category=category)


async def _advance_in_category(cid, category):
    """Следующий кандидат внутри выбранного жанра с тем же обязательным фильтром."""
    genre_id = category["value"]
    return await asyncio.to_thread(
        _discover_pick, cid, [genre_id], _movie_prefs(cid),
        require_genre_ids=[genre_id], reason=category["reason"])

async def send_movie_genre_menu(bot, cid, q=None):
    text = "Выбери жанр — подберу фильм или сериал под твой вкус внутри него."
    await _show_menu_over_card(bot, cid, text, _movie_genre_menu_kb(), q)


async def _show_menu_over_card(bot, cid, text, kb, q):
    """Показывает текстовое меню поверх текущего сообщения.

    Если сообщение текстовое — редактирует его. Если это карточка с постером
    (media), edit_text невозможен: снимаем кнопки у старой карточки (чтобы по ней
    нельзя было случайно нажать) и отправляем меню новым сообщением.
    """
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


# ---------- экран «Предпочтения кино» ----------
_PREF_TYPE = [(ui_label("cinema", "Фильмы"), "movie"), ("Сериалы", "tv")]
_PREF_RECENCY = [("Новинки", "new"), ("Любые годы", "")]
_PREF_RATING = [("6.5", "6.5"), ("7.0", "7.0"), ("7.5", "7.5"), ("8.0", "8.0")]


def _movie_prefs_kb(cid):
    tpref = settings.get(cid, "movie_type_pref", "") or ""
    rpref = settings.get(cid, "movie_recency", "") or ""
    rating = str(settings.get(cid, "movie_min_rating", "") or "")
    rows = [[InlineKeyboardButton(("✅ " if tpref == value else "") + label,
                                  callback_data=f"mpref_type_{value}")]
            for label, value in _PREF_TYPE]
    rows.extend([[InlineKeyboardButton(("✅ " if rpref == value else "") + label,
                                      callback_data=f"mpref_recency_{value or 'any'}")]
                 for label, value in _PREF_RECENCY])
    rows.extend([[InlineKeyboardButton(("✅ " if rating == value else "") + f"⭐️ {label}",
                                      callback_data=f"mpref_rating_{value}")]
                 for label, value in _PREF_RATING])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_preferences"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


async def send_movie_prefs(bot, cid, q=None):
    text = ("🎬 Кино\n\n"
            "Это приоритеты, а не жёсткие фильтры — я учитываю их при подборе, "
            "но всё равно могу предложить что-то за их пределами.")
    kb = _movie_prefs_kb(cid)
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb); return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def toggle_movie_pref(bot, cid, data, q=None):
    """Обработка mpref_* переключателей."""
    if data.startswith("mpref_type_"):
        v = data[len("mpref_type_"):]
        if v in {"movie", "tv"}:
            current = settings.get(cid, "movie_type_pref", "") or ""
            settings.set_(cid, "movie_type_pref", "" if current == v else v)
    elif data.startswith("mpref_recency_"):
        v = data[len("mpref_recency_"):]
        if v in {"new", "any"}:
            settings.set_(cid, "movie_recency", "" if v == "any" else v)
    elif data.startswith("mpref_rating_"):
        v = data[len("mpref_rating_"):]
        if v in {value for _label, value in _PREF_RATING}:
            current = str(settings.get(cid, "movie_min_rating", "") or "")
            settings.set_(cid, "movie_min_rating", "" if current == v else v)
    await send_movie_prefs(bot, cid, q)


def _genre_label(genre_id):
    raw_label = dict((gid, lbl) for lbl, gid in _GENRE_MENU).get(genre_id) or tmdb.GENRES.get(genre_id, "")
    return re.sub(r"^\S+\s+", "", raw_label) if raw_label else raw_label  # без ведущего эмодзи кнопки


async def send_movie_by_genre(bot, cid, genre_id):
    """Рекомендация внутри жанра: TMDb discover + учёт вкуса пользователя.

    Жанр — обязательный фильтр (не подсказка): показанный тайтл ОБЯЗАН иметь этот
    genre_id в TMDb genre_ids, иначе его нельзя показывать (см. _discover_pick require_genre_ids).
    """
    genre_id = int(genre_id)
    label = _genre_label(genre_id)
    reason = {"kind": "genre", "label": label}
    category = {"kind": "genre", "value": genre_id, "reason": reason}
    try:
        it, tm = await asyncio.to_thread(
            _discover_pick, cid, [genre_id], _movie_prefs(cid),
            require_genre_ids=[genre_id], reason=reason)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_movie")
        return
    if not it:
        await bot.send_message(chat_id=cid, text="В этом жанре пока не нашёл нового. Попробуй другой.",
                               reply_markup=_movie_genre_menu_kb())
        return
    await _show_discovered(bot, cid, it, tm, category=category)


async def _show_discovered(bot, cid, it, tm, category=None):
    """category — контекст жанра, из которого пришла карточка: сохраняем его
    в last_recos, чтобы «Другое кино»/«В любимые»/«Уже видел» (через _advance_movie)
    брали СЛЕДУЮЩУЮ рекомендацию из той же категории, а не сбрасывались на общий подбор,
    и чтобы подбор оставался внутри выбранного жанра."""
    tm = dict(tm or {})
    tm["poster"] = await asyncio.to_thread(
        tmdb.english_poster, tm.get("id"), tm.get("kind") or "movie",
    )
    disp = _display_title(it, tm)
    movie_engine.mark_shown(cid, disp)
    rec = store.last_recos.get(str(cid), {"kind": "movie", "items": []})
    rec["items"].append(disp)
    rec["category"] = category
    store.last_recos[str(cid)] = rec
    store.last_source[str(cid)] = "Кино"
    await _send_movie_card(bot, cid, it, len(rec["items"]) - 1, tm=tm, category=category)


def _passes_genre_gate(c, require_genre_ids=None):
    """Обязательная пост-проверка жанра перед отправкой карточки."""
    genre_ids = set(c.get("genre_ids") or [])
    if require_genre_ids and not set(require_genre_ids).issubset(genre_ids):
        return False
    return True


def _discover_pick(cid, genre_ids, prefs, require_genre_ids=None, reason=None):
    """Берёт кандидатов из discover (movie+tv), фильтрует по вкусу/исключениям, ранжирует.

    Жанр — обязательный пост-фильтр (см. _passes_genre_gate): показанный тайтл
    обязан ему соответствовать. Перебираем ранжированный список, а не берём слепо
    топ-1, — если лидер не проходит гейт из-за неполных данных TMDb, пробуем следующего.
    reason — источник рекомендации по жанру, а не anchor-«понравился».
    """
    min_rating = max(
        movie_engine.RATING_STEPS[0],
        float((prefs or {}).get("min_rating") or movie_engine.RATING_STEPS[0]),
    )
    taste = movie_engine.taste_profile(cid, resolve_details=False)
    excluded = movie_engine._excluded_norms(cid)
    steps = [r for r in movie_engine.RATING_STEPS if r <= min_rating] or [movie_engine.RATING_STEPS[-1]]
    for mr in steps:
        pool = {}
        for kind in ("movie", "tv"):
            for c in tmdb.discover(
                kind, genre_ids=genre_ids, min_rating=mr, year_gte=2000,
            ):
                if not c.get("id") or movie_engine._norm(c.get("name")) in excluded:
                    continue
                if not _passes_genre_gate(c, require_genre_ids):
                    continue
                pool[f"{c['kind']}:{c['id']}"] = c
        if pool:
            ranked = movie_engine.rank(list(pool.values()), taste, prefs)
            return _candidate_to_card(cid, ranked[0], reason=reason)
    return None, None


async def movie_love(bot, cid, i, q=None):
    """Добавляет фильм в любимые без дублей и отражает состояние на карточке."""
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        try:
            normalized = await asyncio.wait_for(
                asyncio.to_thread(normalize_movie_items, [title]), timeout=4.0,
            )
        except asyncio.TimeoutError:
            normalized = [canonical_movie_label(title)]
        if normalized:
            title = normalized[0]
        existing = {
            movie_title_for_lookup(item).casefold()
            for item in store.get_list(config.FAVORITE_MOVIES_KEY, cid)
        }
        if movie_title_for_lookup(title).casefold() not in existing:
            store.add_to_list(config.FAVORITE_MOVIES_KEY, cid, title)
        if q is not None:
            await q.message.edit_reply_markup(reply_markup=_movie_kb(i))


_bind_functions(globals(), _movie_discovery, ["_movie_home_kb","_movie_country_label","_movie_service_language","_movie_city","_local_movie_score","_now_playing_week_key","_previous_now_playing_week_key","_now_playing_catalog_get","_now_playing_catalog_set","_regional_now_playing_item","get_local_now_playing","send_movie_home","_featured_now_playing","_youtube_trailer_search_url","_with_trailer_urls","_daily_rebus","daily_movie_rebus","_cinema_birthday_cache_get","_cinema_birthday_cache_set","_cinema_birthday_role","_load_cinema_birthday","_daily_cinema_content","send_movie_now_playing","warm_movie_home_cache","warm_movie_premieres_cache","_movie_premieres_cache_get","_movie_premieres_cache_set","_movie_premiere_item","get_movie_premieres","_movie_premieres_view","_movie_premieres_with_posters","send_movie_premieres","show_movie_premiere_page","get_series_premieres","_series_premieres_view","send_series_premieres","show_series_premiere_page"])
