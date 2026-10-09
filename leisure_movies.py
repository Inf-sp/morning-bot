from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
import asyncio
import logging
import secrets
import time
from datetime import datetime

import config
import store
import tmdb
import movie_engine
import recommendation_stoplist
import inclusive_recommendations
import tracking
import monthly_rebuses
from ui import leisure as leisure_ui
from leisure_collection import (
    item_text,
    movie_title_for_lookup,
)

_log = logging.getLogger(__name__)


_MOVIE_PREMIERES_CACHE_VERSION = 5
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
_CINEMA_REBUSES = monthly_rebuses.local_pool("movies")

def _display_title(it, tm):
    """Название, которое реально показано пользователю (TMDb если есть, иначе от LLM)."""
    name = (tm.get("name") if tm else "") or it.get("title", "")
    year = (tm.get("year") if tm else "") or ""
    return f"{name} ({year})" if year else name

def _movie_card(it, tm):
    return leisure_ui.movie_card(it, tm)


def _movie_home_only_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")]])


def _favorite_movie_added_kb(open_callback=None):
    rows = [[InlineKeyboardButton("Открыть карточку", callback_data=open_callback)]] if open_callback else []
    # Отдельной кнопки «Моё кино» нет: «Назад» и так ведёт в список.
    rows.append(nav_row("movie_favorites"))
    return InlineKeyboardMarkup(rows)


async def _added_movie_open_callback(cid, title):
    """Колбэк карточки только что добавленного фильма внутри «Моего кино» (жанр + позиция)."""
    try:
        records = await _favorite_movie_records(cid)
    except Exception:
        _log.debug("_added_movie_open_callback: ignored error", exc_info=True)
        return None
    token, view = _new_favorite_movie_view(cid, records)
    wanted = movie_title_for_lookup(title).casefold()
    for genre_index, (_genre, items) in enumerate(view["genres"]):
        for page, item in enumerate(items):
            if movie_title_for_lookup(item.get("value") or item["title"]).casefold() == wanted:
                return f"mfg:{token}:{genre_index}:{page}"
    return None


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
        open_callback = await _added_movie_open_callback(cid, titles[0])
    else:
        msg = leisure_ui.favorite_movies_added_card(titles)
        open_callback = None
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=_favorite_movie_added_kb(open_callback))


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
    msg = leisure_ui.favorite_movies_home(len(records))
    rows = [[InlineKeyboardButton("✅ Добавить фильм", callback_data="as_loveadd_movies")]]
    rows.extend([
        [InlineKeyboardButton(f"{genre} · {len(items)}", callback_data=f"mfg:{token}:{index}:0")]
        for index, (genre, items) in enumerate(view["genres"])
    ])
    rows.append([InlineKeyboardButton(
        "📝 Выбрать предпочтения", callback_data="movie_prefs",
    )])
    rows.append(nav_row("lz_lib"))
    kb = InlineKeyboardMarkup(rows)
    await rich_delivery.show(bot, cid, msg, reply_markup=kb, query=q)


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
    # «❌ Удалить» — первой строкой, над листанием.
    rows.insert(0, [InlineKeyboardButton(
        "❌ Удалить", callback_data=f"mfd:{token}:{item['id'][:8]}:{genre_index}:{page}",
    )])
    # Внутри категории добавления нет: «Удалить», листание, «Показать списком», навигация.
    rows.append([InlineKeyboardButton("Показать списком", callback_data=f"mfl:{token}:{genre_index}:0")])
    rows.append(nav_row("movie_favorites"))
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
            _log.debug("send_favorite_movie_genre: ignored error", exc_info=True)
    if poster:
        try:
            await bot.send_photo(
                chat_id=cid, photo=poster, caption=msg.text,
                caption_entities=msg.entities, reply_markup=kb,
            )
            return
        except Exception:
            _log.debug("send_favorite_movie_genre: ignored error", exc_info=True)
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb,
    )


_FAVORITE_LIST_PAGE = 12


async def send_favorite_movie_list(bot, cid, token, genre_index, page=0, q=None):
    """«Показать списком» как в «Моём словаре»: фильмы кнопками, нажатие открывает карточку."""
    import rich_delivery

    view = _favorite_movie_view(cid, token)
    if view is None or not 0 <= genre_index < len(view["genres"]):
        await send_favorite_movies(bot, cid, q=q)
        return
    genre, items = view["genres"][genre_index]
    pages = max(1, (len(items) + _FAVORITE_LIST_PAGE - 1) // _FAVORITE_LIST_PAGE)
    page = max(0, min(int(page), pages - 1))
    start = page * _FAVORITE_LIST_PAGE
    buttons = [
        InlineKeyboardButton(item["title"][:24], callback_data=f"mfg:{token}:{genre_index}:{start + offset}")
        for offset, item in enumerate(items[start:start + _FAVORITE_LIST_PAGE])
    ]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"mfl:{token}:{genre_index}:{(page - 1) % pages}"),
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"mfl:{token}:{genre_index}:{(page + 1) % pages}"),
        ])
    rows.append(nav_row("movie_favorites"))
    msg = leisure_ui.favorite_movie_list(genre, len(items))
    await rich_delivery.show(bot, cid, msg, reply_markup=InlineKeyboardMarkup(rows), query=q)


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
        nav_row(f"mfg:{token}:{genre_index}:{page}"),
    ])
    poster = item["tm"].get("poster")
    if poster:
        try:
            await bot.send_photo(chat_id=cid, photo=poster, caption=msg.text,
                                 caption_entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            _log.debug("send_favorite_movie_card: ignored error", exc_info=True)
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
            _log.debug("send_favorite_movie_delete_confirmation: ignored error", exc_info=True)
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
        # «Другой фильм» меняет кнопки под карточкой на выбор жанра (movie_pick_*).
        [InlineKeyboardButton("✨ Другой фильм", callback_data=f"movie_pick_{i}")],
        # «Не нравится» — в чёрный список, сразу следующая рекомендация.
        [InlineKeyboardButton("Не нравится", callback_data=f"movie_no_{i}", api_kwargs={"style": "danger"})],
        # «Настроить» — «Моё кино» новым сообщением, карточка остаётся.
        [InlineKeyboardButton("🎚️ Настроить", callback_data="lz_cfg_movie")],
    ]
    rows.append(nav_row("m_leisure"))
    return InlineKeyboardMarkup(rows)


# Шесть популярных жанров для быстрого меню «Выбрать жанр».
_GENRE_MENU = [
    ("Комедия", 35), ("Ужасы", 27),
    ("Фантастика", 878), ("Триллер", 53),
    ("Романтика", 10749), ("Драма", 18),
]

def _movie_genre_menu_kb(back="m_leisure"):
    """Выбор после «Другой фильм»: жанры (стандартные, без цвета) и зелёная новинка."""
    rows = [
        # Новинка — первой, как в книгах, музыке и играх.
        [InlineKeyboardButton("🆕 Новинка", callback_data="nov_movie")],
        *[[InlineKeyboardButton(label, callback_data=f"movie_g_{gid}")]
          for label, gid in _GENRE_MENU],
    ]
    rows.append(nav_row(back))
    return InlineKeyboardMarkup(rows)

MIN_TMDB_RATING = 7.0
_DISCOVER_PAGES = 8
# Сколько секунд бюджета оставить резерву (discover + постер), если движок не успел.
_RESERVE_SECONDS = 6.0

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
    # Полная запись и чистое название: элементы бывают и {"id", "value"}.
    return {
        value.lower()
        for x in list(wl) + blocked
        for value in (item_text(x), movie_title_for_lookup(x))
        if value
    }

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
    if tm and tm.get("id"):
        tm = dict(tm)
        kind = tm.get("kind") or "movie"
        poster, trailer = await asyncio.gather(
            asyncio.to_thread(tmdb.english_poster, tm.get("id"), kind),
            asyncio.to_thread(tmdb.trailer_url, tm.get("id"), kind),
        )
        tm["poster"] = poster or tm.get("poster")
        tm["trailer_url"] = trailer or ""
    title, msg = _movie_card(it, tm)
    kb = _movie_kb(i, category=category)
    if tm and tm.get("poster"):
        try:
            await bot.send_photo(chat_id=cid, photo=tm["poster"], caption=msg.text, caption_entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            _log.debug("_send_movie_card: ignored error", exc_info=True)
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

async def _discover_movie_pick(cid, prefs):
    """Свежий качественный фильм из TMDb discover без AI.

    Первая страница discover — всего 20 популярных фильмов; у активного
    пользователя они быстро уходят в «показанные», поэтому листаем дальше.
    """
    requested_kind = prefs.get("type_pref") or "movie"
    excluded = movie_engine._excluded_norms(cid)
    hard_excluded = movie_engine._excluded_norms(cid, include_shown=False)
    requested_kinds = [requested_kind]
    if prefs.get("type_pref"):
        requested_kinds.append("tv" if requested_kind == "movie" else "movie")
    min_rating = max(MIN_TMDB_RATING, float(prefs.get("min_rating") or MIN_TMDB_RATING))
    repeats = []
    tm = None
    for candidate_kind in requested_kinds:
        for page in range(1, _DISCOVER_PAGES + 1):
            try:
                items = await asyncio.to_thread(
                    tmdb.discover, candidate_kind, None, min_rating, 2000, page=page)
            except Exception:
                items = []
            if not items:
                break
            items = [movie for movie in items
                     if int(movie.get("vote_count") or 0) >= movie_engine.MIN_VOTE_COUNT
                     and movie_engine._norm(movie.get("name")) not in hard_excluded]
            fresh = [movie for movie in items
                     if movie_engine._norm(movie.get("name")) not in excluded]
            if fresh:
                tm = movie_engine.rank(fresh, {
                    "genres": {}, "countries": {}, "kind_pref": None,
                }, prefs)[0]
                break
            repeats.extend(items)
        if tm:
            break
    if not tm and repeats:
        # Всё свежее уже показано: давно показанный лучше, чем ошибка.
        shown = [movie_engine._norm(value)
                 for value in store.get_list(config.MOVIE_SHOWN_KEY, cid)]
        order = {name: index for index, name in enumerate(shown)}
        tm = min(repeats, key=lambda movie: order.get(movie_engine._norm(movie.get("name")), -1))
    if not tm:
        return None, None
    return {"title": tm.get("name", ""),
            "hook": "Свежий фильм с хорошими оценками — можно начать с него."}, tm


async def _lookup_movie_tm(it):
    """Ищет TMDb-данные для LLM-варианта в пределах бюджета действия."""
    if not config.TMDB_API_KEY:
        return None
    remaining = tracking.remaining_action_seconds()
    timeout = min(5.0, remaining - 0.5) if remaining is not None else 5.0
    if timeout <= 0.2:
        return None
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(tmdb.lookup_title, it.get("title", ""), it.get("title_en", "")),
            timeout=timeout,
        )
    except Exception:
        return None


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
        it, tm = await _discover_movie_pick(cid, prefs)
    else:
        remaining = tracking.remaining_action_seconds()
        engine_timeout = max(1.0, remaining - _RESERVE_SECONDS) if remaining is not None else None
        it, tm = await _tmdb_engine_pick(cid, timeout=engine_timeout)
        if it is None:
            # Быстрый TMDB-резерв до медленного LLM-пути.
            it, tm = await _discover_movie_pick(cid, prefs)
    if it is None:
        it, tm = await _llm_movie_pick(cid, _movie_used(cid))
        if it and not tm:
            tm = await _lookup_movie_tm(it)
    # Без данных TMDb карточка выходит пустой («Название» + сырой hook) — не показываем.
    if not it or not tm:
        _log.warning(
            "movie reco: no TMDb-backed pick (tmdb_key=%s, llm_item=%s)",
            bool(config.TMDB_API_KEY), bool(it),
        )
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


async def send_current_movie(bot, cid, status=None):
    """«Что посмотреть»: готовая карточка дня из ночного прогрева.

    Без кэша — обычный живой подбор, который сам кэширует карточку на день.
    «✨ Другой фильм» (movie_next) всегда идёт живым путём ``send_recos``.
    """
    cached = _cached_movie(cid)
    it, tm = cached if cached else (None, None)
    if not it or not tm:
        await send_recos(bot, cid, "movie", status=status)
        return
    disp = _display_title(it, tm)
    movie_engine.mark_shown(cid, disp)
    store.last_recos[str(cid)] = {"kind": "movie", "items": [disp]}
    store.last_source[str(cid)] = "Кино"
    store.last_answer[str(cid)] = f"{disp} - {it.get('hook', '')}"
    await _send_movie_card(bot, cid, it, 0, tm=tm, status=status)


# Daily rebus and premieres live in movie_discovery.py.

from movie_recommendation import (  # noqa: E402 — movie_recommendation uses names defined above
    _INCLUSIVE_MOVIE_TITLES,
    _PREF_TYPE,
    _PREF_RECENCY,
    _PREF_RATING,
    _movie_prefs,
    _inclusive_movie_pick,
    _as_float,
    _tmdb_engine_pick,
    _candidate_to_card,
    _reason_text,
    _reason_label,
    _llm_movie_pick,
    movie_dislike,
    _advance_movie,
    _advance_in_category,
    send_movie_genre_menu,
    _show_menu_over_card,
    _movie_prefs_kb,
    send_movie_prefs,
    toggle_movie_pref,
    _genre_label,
    send_movie_by_genre,
    _show_discovered,
    _passes_genre_gate,
    _discover_pick,
    movie_love,
)


from movie_discovery import (  # noqa: E402 — movie_discovery uses names defined above
    _movie_country_label,
    _now_playing_week_key,
    _daily_rebus,
    daily_movie_rebus,
    warm_movie_premieres_cache,
    _movie_premieres_cache_get,
    _movie_premieres_cache_set,
    _movie_premiere_item,
    get_movie_premieres,
    _movie_premieres_view,
    _movie_premieres_with_posters,
    send_movie_premieres,
    show_movie_premiere_page,
    get_series_premieres,
    _series_premieres_view,
    send_series_premieres,
    show_series_premiere_page,
    _combined_premieres,
    _combined_premieres_view,
    send_combined_premieres,
    show_combined_premiere_page,
)
import rich_delivery
from ui.navigation import nav_row
