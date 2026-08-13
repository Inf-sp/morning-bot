from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from ui.constants import ui_label
import asyncio
import logging
import re
import threading
import time
from datetime import datetime
from urllib.parse import quote_plus

import requests

import config

_log = logging.getLogger(__name__)
import store
import settings
import tmdb
import movie_engine
import recommendation_stoplist
import verify
import tracking
import local_cinema
from ui import leisure as leisure_ui
from leisure_collection import (
    canonical_movie_label,
    content_recommend,
    movie_title_for_lookup,
    normalize_movie_items,
)


_CINEMA_BIRTHDAY_LOCK = threading.Lock()
_CINEMA_BIRTHDAY_CACHE_VERSION = 3
_CINEMA_REBUSES = (
    {
        "emoji": "🦈 🌊 👨‍🔬",
        "answer": "Челюсти",
        "fact": "«Челюсти» считают первым современным летним блокбастером.",
    },
    {
        "emoji": "🕶️ 💊 🤖",
        "answer": "Матрица",
        "fact": "«Матрица» получила четыре премии «Оскар» в технических категориях.",
    },
    {
        "emoji": "🛳️ 🧊 💔",
        "answer": "Титаник",
        "fact": "«Титаник» получил 11 премий «Оскар» — рекорд, который он делит ещё с двумя фильмами.",
    },
    {
        "emoji": "🧙 💍 🌋",
        "answer": "Властелин колец",
        "fact": "«Возвращение короля» выиграло все 11 номинаций на «Оскар».",
    },
    {
        "emoji": "🦖 🏝️ 🚙",
        "answer": "Парк юрского периода",
        "fact": "«Парк юрского периода» стал вехой для компьютерных визуальных эффектов в кино.",
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


def _movie_kb(i, category=None):
    """Клавиатура карточки кино с быстрым подбором по жанру.

    category используется только для контекста подбора.
    """
    rows = [
        [InlineKeyboardButton("✨ Подобрать другое кино", callback_data=f"movie_no_{i}")],
        [InlineKeyboardButton("🎭 По жанру", callback_data="movie_genre_menu")],
        [InlineKeyboardButton("🎚️ Моё кино", callback_data="movie_favorites")],
    ]
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="m_movie"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    return InlineKeyboardMarkup(rows)


# Шесть популярных жанров для быстрого меню «По жанру».
_GENRE_MENU = [
    ("😂 Комедия", 35), ("👻 Ужасы", 27),
    ("🚀 Фантастика", 878), ("🔪 Триллер", 53),
    ("💕 Романтика", 10749), ("🎭 Драма", 18),
]

def _movie_genre_menu_kb():
    rows = []
    buttons = [InlineKeyboardButton(label, callback_data=f"movie_g_{gid}")
               for label, gid in _GENRE_MENU]
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])
    rows.append([InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
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
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb)
        return
    if tm and tm.get("poster"):
        try:
            await bot.send_photo(chat_id=cid, photo=tm["poster"], caption=msg.text, caption_entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
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
    # Даже без любимых открытие раздела должно дать современную качественную
    # рекомендацию; вкус начнёт уточняться после первых отметок в любимом.
    # Явный запрос всегда получает новый вариант, а не утреннюю карточку из кэша.
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
    await _send_movie_card(bot, cid, it, 0, tm=tm, status=status)


def _movie_home_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Подобрать кино", callback_data="movie_reco")],
        [InlineKeyboardButton("🎭 По жанру", callback_data="movie_genre_menu")],
        [InlineKeyboardButton("🎚️ Моё кино", callback_data="movie_favorites")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def _movie_country_label(name, cc=""):
    name = str(name or "").strip()
    if name:
        return name
    cc = (cc or "").upper()
    by_cc = {
        "NL": "Нидерланды",
        "BE": "Бельгия",
        "DE": "Германия",
        "FR": "Франция",
        "GB": "Великобритания",
        "ES": "Испания",
        "IT": "Италия",
        "AT": "Австрия",
        "CH": "Швейцария",
        "PL": "Польша",
        "SE": "Швеция",
        "DK": "Дания",
        "PT": "Португалия",
        "US": "США",
    }
    return by_cc.get(cc, config.DEFAULT_CITY.get("country", "Нидерланды"))


def _movie_service_language(cid=None):
    """Язык регионального каталога без русской машинной локализации."""
    cc = str(store.get_settings(cid).get("cc") or "NL").upper() if cid is not None else "NL"
    return {
        "FR": "fr-FR",
        "DE": "de-DE",
        "ES": "es-ES",
        "IT": "it-IT",
        "NL": "nl-NL",
    }.get(cc, "en-US")


def _movie_city(cid):
    return str(store.get_settings(cid).get("city") or config.DEFAULT_CITY.get("name") or "").strip()


def _local_movie_score(item, prefs):
    """Качество и вкус ранжируют только уже подтверждённые городские сеансы."""
    rating = float(item.get("rating") or 0)
    votes = int(item.get("vote_count") or 0)
    popularity = float(item.get("popularity") or 0)
    genre_ids = set(item.get("genre_ids") or [])
    preferred = set(prefs.get("genres") or [])
    score = rating * 12 + min(votes, 2000) ** 0.5 + min(popularity, 100) * 0.15
    if preferred.intersection(genre_ids):
        score += 18
    return score


def _now_playing_week_key():
    today = datetime.now(config.TZ).date()
    year, week, _weekday = today.isocalendar()
    return f"{year}-W{week:02d}"


def _now_playing_catalog_get(cid, city):
    data = store._load(config.MOVIE_NOW_PLAYING_CACHE_KEY) or {}
    entry = data.get(str(cid)) if isinstance(data, dict) else None
    if (not isinstance(entry, dict)
            or entry.get("city") != city
            or entry.get("week") != _now_playing_week_key()):
        return None
    items = entry.get("items")
    if not isinstance(items, list) or not items:
        return None
    return [dict(item) for item in items if isinstance(item, dict)]


def _now_playing_catalog_set(cid, city, items):
    records = [dict(item) for item in (items or []) if isinstance(item, dict)]
    if not records:
        return None

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[str(cid)] = {
            "city": city,
            "week": _now_playing_week_key(),
            "items": records,
        }
        return data, None

    store.mutate_kv(config.MOVIE_NOW_PLAYING_CACHE_KEY, mutate)
    return records


def _regional_now_playing_item(movie):
    """Нормализует подтверждённый театральный релиз TMDb для витрины."""
    release_date = getattr(movie, "release_date", None)
    return {
        "id": getattr(movie, "id", None),
        "title": str(getattr(movie, "title", "") or "").strip(),
        "name_en": str(getattr(movie, "original_title", "") or "").strip(),
        "year": getattr(release_date, "year", 0) or 0,
        "rating": getattr(movie, "rating", None),
        "vote_count": int(getattr(movie, "vote_count", 0) or 0),
        "popularity": float(getattr(movie, "popularity", 0) or 0),
        "genre_ids": [],
        "genres": list(getattr(movie, "genres", None) or []),
    }


async def get_local_now_playing(cid, *, limit=20, refresh=False):
    """Локальная афиша → TMDB metadata → полезная сортировка.

    Не используем национальный ``now_playing`` как запасной вариант: без местной
    афиши нельзя утверждать, что фильм идёт в городе пользователя.
    """
    city = _movie_city(cid)
    prefs = _movie_prefs(cid)
    items = None if refresh else _now_playing_catalog_get(cid, city)
    if items is None:
        listed = await asyncio.to_thread(local_cinema.get_city_movies, cid, city, refresh=refresh)
        if listed:
            items = []
            for local in listed[:30]:
                meta = await asyncio.to_thread(tmdb.search_id, local.title, "movie") if config.TMDB_API_KEY else None
                if meta:
                    year = int(meta.get("year") or 0)
                    # Старая картина не становится новинкой только из-за повторного показа.
                    if year and year < datetime.now(config.TZ).year - 1:
                        continue
                    item = dict(meta)
                    item["title"] = item.get("name") or local.title
                    item["genres"] = [tmdb.GENRES.get(g, "") for g in item.get("genre_ids") or [] if tmdb.GENRES.get(g)]
                else:
                    item = {"title": local.title, "genres": list(local.genres), "rating": None,
                            "vote_count": 0, "popularity": 0, "genre_ids": []}
                items.append(item)
        else:
            cc = str(store.get_settings(cid).get("cc") or "NL").upper()
            regional = await asyncio.to_thread(
                tmdb.get_now_playing, cc, _movie_service_language(cid), max_results=20,
            )
            items = [
                _regional_now_playing_item(movie)
                for movie in regional
                if str(getattr(movie, "title", "") or "").strip()
            ]
        _now_playing_catalog_set(cid, city, items)
    items.sort(key=lambda item: _local_movie_score(item, prefs), reverse=True)
    return items[:max(1, int(limit or 20))]


async def send_movie_home(bot, cid, q=None, status=None):
    """Открывает уже подготовленную персональную карточку кино."""
    it, tm = await get_current_movie(cid)
    if not it:
        text = "Не удалось подобрать кино. Попробуй ещё раз."
        if status is not None:
            await status.replace(text, reply_markup=_movie_home_only_kb())
        else:
            await bot.send_message(chat_id=cid, text=text, reply_markup=_movie_home_only_kb())
        return
    disp = _display_title(it, tm)
    movie_engine.mark_shown(cid, disp)
    store.last_recos[str(cid)] = {"kind": "movie", "items": [disp]}
    store.last_source[str(cid)] = "Кино"
    store.last_answer[str(cid)] = f"{disp} - {it.get('hook', '')}"
    await _send_movie_card(bot, cid, it, 0, tm=tm, status=status)


def _featured_now_playing(items):
    """На витрину попадают только достаточно известные картины из проката."""
    featured = []
    for item in items or []:
        try:
            rating = float(item.get("rating") or 0)
            votes = int(item.get("vote_count") or 0)
        except (AttributeError, TypeError, ValueError):
            continue
        if rating >= 6.5 and votes >= 100:
            featured.append(item)
    return sorted(
        featured,
        key=lambda item: (
            float(item.get("popularity") or 0),
            int(item.get("vote_count") or 0),
            float(item.get("rating") or 0),
        ),
        reverse=True,
    )


def _youtube_trailer_search_url(item):
    query = " ".join(str(value or "").strip() for value in (
        item.get("title"), item.get("name_en"), item.get("year"), "official trailer",
    ) if str(value or "").strip())
    return f"https://www.youtube.com/results?search_query={quote_plus(query)}" if query else ""


async def _with_trailer_urls(items):
    """Добавляет ссылку на проверенный трейлер, не смешивая сеть с UI-рендером."""
    enriched = []
    for source in items or []:
        item = dict(source or {})
        trailer = await asyncio.to_thread(tmdb.trailer_url, item.get("id"), "movie")
        item["trailer_url"] = trailer or _youtube_trailer_search_url(item)
        enriched.append(item)
    return enriched


def _daily_rebus(day):
    """Один ребус и связанный факт на календарную дату, без случайных повторов в течение дня."""
    index = (day.timetuple().tm_yday - 216) % len(_CINEMA_REBUSES)
    return dict(_CINEMA_REBUSES[index])


def daily_movie_rebus(day):
    """Публичный локальный ребус дня для компактных витрин без сетевого запроса."""
    return _daily_rebus(day)


def _cinema_birthday_cache_get(day):
    data = store._load(config.CINEMA_DAILY_CACHE_KEY)
    entry = data.get(day.isoformat()) if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return None
    if entry.get("version") != _CINEMA_BIRTHDAY_CACHE_VERSION:
        return None
    birthday = entry.get("birthday")
    return dict(birthday) if isinstance(birthday, dict) else {}


def _cinema_birthday_cache_set(day, birthday):
    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[day.isoformat()] = {
            "version": _CINEMA_BIRTHDAY_CACHE_VERSION,
            "ts": time.time(),
            "birthday": dict(birthday or {}),
        }
        return data, None

    store.mutate_kv(config.CINEMA_DAILY_CACHE_KEY, mutate)


def _cinema_birthday_role(value):
    role = str(value or "").casefold()
    if "режисс" in role or "director" in role:
        return "режиссёр"
    if "актрис" in role or "actress" in role:
        return "актриса"
    if "актёр" in role or "actor" in role:
        return "актёр"
    return "кинематографист"


def _load_cinema_birthday(day):
    """Находит известного именинника кино и кэширует один результат для всех пользователей.

    Wikidata вызывается только при первом открытии экрана в новую дату. Если источник
    временно недоступен, показываем только заранее подтверждённый fallback для этой даты,
    а не приписываем день рождения случайному человеку.
    """
    cached = _cinema_birthday_cache_get(day)
    if cached is not None:
        return cached
    with _CINEMA_BIRTHDAY_LOCK:
        cached = _cinema_birthday_cache_get(day)
        if cached is not None:
            return cached
        query = """
            SELECT ?person ?personLabel ?birth ?occupationLabel ?notableWorkLabel (wikibase:sitelinks(?person) AS ?sitelinks) WHERE {
              ?person wdt:P31 wd:Q5; wdt:P569 ?birth; wdt:P106 ?occupation.
              VALUES ?occupation { wd:Q2526255 wd:Q33999 wd:Q10800557 }
              OPTIONAL { ?person wdt:P800 ?notableWork. }
              FILTER(MONTH(?birth) = %d && DAY(?birth) = %d)
              SERVICE wikibase:label { bd:serviceParam wikibase:language \"ru,en\". }
            }
            ORDER BY DESC(?sitelinks)
            LIMIT 1
        """ % (day.month, day.day)
        birthday = None
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
                role = _cinema_birthday_role((item.get("occupationLabel") or {}).get("value"))
                if name:
                    work = str((item.get("notableWorkLabel") or {}).get("value") or "").strip()
                    birthday = {"name": name, "role": role}
                    birth = str((item.get("birth") or {}).get("value") or "").strip()
                    if birth:
                        birthday["birth"] = birth
                    if work:
                        birthday["fact"] = f"Одна из заметных работ — «{work}»."
        except Exception as error:
            _log.info("cinema birthday lookup unavailable: %s", type(error).__name__)
        birthday = birthday or _BIRTHDAY_FALLBACKS.get((day.month, day.day)) or {}
        _cinema_birthday_cache_set(day, birthday)
        return dict(birthday)


async def _daily_cinema_content():
    now = datetime.now(config.TZ)
    return {
        "rebus": _daily_rebus(now.date()),
        "birthday": await asyncio.to_thread(_load_cinema_birthday, now.date()),
    }


async def send_movie_now_playing(bot, cid, q=None, status=None):
    city = _movie_city(cid)
    featured = _featured_now_playing(await get_local_now_playing(cid, limit=20))[:3]
    now_playing = await _with_trailer_urls(featured)
    cinema_day = await _daily_cinema_content()
    msg = leisure_ui.movie_now_playing_screen(city, now_playing, cinema_day)
    kb = _movie_home_kb()
    if status is not None:
        await status.replace(
            msg.text, entities=msg.entities, reply_markup=kb,
            disable_web_page_preview=True,
        )
        return
    if q is not None:
        try:
            await q.message.edit_text(
                msg.text, entities=msg.entities, reply_markup=kb,
                disable_web_page_preview=True,
            )
            return
        except Exception:
            pass
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb,
        disable_web_page_preview=True,
    )


async def warm_movie_home_cache(cid):
    """Готовит карточку кино заранее, не отправляя экран в Telegram."""
    it, _tm = await get_current_movie(cid)
    return bool(it)


def _movie_prefs(cid):
    """Предпочтения кино из настроек → dict для движка (приоритеты, не запреты)."""
    return {
        "type_pref": settings.get(cid, "movie_type_pref", "") or None,
        "recency": settings.get(cid, "movie_recency", "") or None,
        "min_rating": _as_float(settings.get(cid, "movie_min_rating", None)),
    }


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
