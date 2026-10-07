"""Cinema premieres discovery and the daily movie rebus."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from leisure_movies import (
        InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto,
        _CINEMA_REBUSES, _MONTHS, _MOVIE_PREMIERES_CACHE_VERSION,
        asyncio, config, datetime, leisure_ui, monthly_rebuses,
        movie_title_for_lookup, store, timedelta, tmdb,
    )


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


def _now_playing_week_key():
    today = datetime.now(config.TZ).date()
    year, week, _weekday = today.isocalendar()
    return f"{year}-W{week:02d}"


def _daily_rebus(day):
    """Один ребус и связанный факт на календарную дату, без случайных повторов в течение дня."""
    return monthly_rebuses.cached_for_day("movies", day, _CINEMA_REBUSES)


def daily_movie_rebus(day):
    """Публичный локальный ребус дня для компактных витрин без сетевого запроса."""
    return _daily_rebus(day)


async def warm_movie_premieres_cache(cid):
    """Обновляет недельную витрину премьер из расписания по понедельникам."""
    await get_movie_premieres(cid, refresh=True)
    return True


def _movie_premieres_cache_get(country_code, today, *, allow_stale=False):
    data = store._load(config.MOVIE_PREMIERES_CACHE_KEY) or {}
    entry = data.get(str(country_code or "").upper()) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or entry.get("version") != _MOVIE_PREMIERES_CACHE_VERSION:
        return None
    if not allow_stale and entry.get("week") != _now_playing_week_key():
        return None
    try:
        expires = datetime.fromisoformat(str(entry.get("expires") or "")).date()
    except ValueError:
        return None
    items = entry.get("items")
    if (expires < today and not allow_stale) or not isinstance(items, list):
        return None
    return [dict(item) for item in items if isinstance(item, dict)]


def _movie_premieres_cache_set(country_code, expires, items):
    country_code = str(country_code or "").upper()

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[country_code] = {
            "version": _MOVIE_PREMIERES_CACHE_VERSION,
            "week": _now_playing_week_key(),
            "expires": expires.isoformat(),
            "items": [dict(item) for item in items if isinstance(item, dict)],
        }
        return data, None

    store.mutate_kv(config.MOVIE_PREMIERES_CACHE_KEY, mutate)


def _movie_premiere_item(movie, today):
    release = getattr(movie, "release_date", None)
    if release is None or release.year != today.year:
        return None
    title = str(getattr(movie, "title", "") or "").strip()
    if not title:
        return None
    if release == today:
        date_label = "сегодня"
    else:
        date_label = f"{release.day} {_MONTHS[release.month - 1]}"
    return {
        "id": getattr(movie, "id", None),
        "title": title,
        "date": release.isoformat(),
        "date_label": date_label,
        "genres": ", ".join(getattr(movie, "genres", None) or [])[:70],
        "overview": str(getattr(movie, "overview", "") or "").strip(),
        "poster": str(getattr(movie, "poster_url", "") or "").strip(),
        "rating": float(getattr(movie, "rating", 0) or 0),
        "popularity": float(getattr(movie, "popularity", 0) or 0),
        "vote_count": int(getattr(movie, "vote_count", 0) or 0),
    }


async def get_movie_premieres(cid, *, refresh=False):
    """Новые релизы в стране; внешние данные обновляет только ночной прогрев.

    Днём экран читает недельный кэш. Если ночное обновление временно не
    состоялось, остаётся последняя готовая витрина вместо нового запроса к TMDb.
    """
    settings_data = store.get_settings(cid)
    country_code = str(settings_data.get("cc") or "NL").upper()
    today = datetime.now(config.TZ).date()
    cached = _movie_premieres_cache_get(country_code, today)
    if cached is not None:
        return cached
    if not refresh:
        stale = _movie_premieres_cache_get(country_code, today, allow_stale=True)
        if stale:
            return stale
        # Первый вход после смены формата сам собирает витрину;
        # дальше все открытия снова читают недельный кэш.
        refresh = True
    end = today + timedelta(days=13)
    now_playing, upcoming = await asyncio.gather(
        asyncio.to_thread(tmdb.get_now_playing, country_code, "ru-RU", 30),
        asyncio.to_thread(
            tmdb.get_upcoming_theatrical_releases,
            country_code, today, end, "ru-RU",
        ),
    )
    items, seen = [], set()
    for movie in [*(now_playing or []), *(upcoming or [])]:
        item = _movie_premiere_item(movie, today)
        if not item:
            continue
        try:
            release = datetime.fromisoformat(item["date"]).date()
        except ValueError:
            continue
        if release < today - timedelta(days=7) or release > end:
            continue
        key = str(item.get("id") or item["title"]).casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    items.sort(key=lambda item: (
        -float(item.get("popularity") or 0),
        -int(item.get("vote_count") or 0),
        item["date"],
    ))
    items = [item for item in items if item.get("overview")][:5]
    poster_urls, trailer_urls = await asyncio.gather(
        asyncio.gather(*(asyncio.to_thread(tmdb.english_poster, item.get("id"), "movie")
                         for item in items)),
        asyncio.gather(*(asyncio.to_thread(tmdb.trailer_url, item.get("id"), "movie")
                         for item in items)),
    )
    for item, poster_url, trailer_url in zip(items, poster_urls, trailer_urls):
        item["poster"] = str(poster_url or "").strip()
        item["trailer_url"] = str(trailer_url or "").strip()
    if items:
        _movie_premieres_cache_set(country_code, today + timedelta(days=7), items)
    return items


def _movie_premieres_view(cid, items, page=0):
    settings_data = store.get_settings(cid)
    country = _movie_country_label(settings_data.get("country"), settings_data.get("cc"))
    today = datetime.now(config.TZ).date()
    end = today + timedelta(days=13)
    date_range = f"{today.day} {_MONTHS[today.month - 1]} – {end.day} {_MONTHS[end.month - 1]}"
    page = max(0, min(int(page), len(items) - 1)) if items else 0
    msg = leisure_ui.movie_premieres_screen(
        country, date_range, [items[page]] if items else [],
    )
    rows = []
    if len(items) > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"movie_premiere_page:{(page - 1) % len(items)}"),
            InlineKeyboardButton(f"{page + 1}/{len(items)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"movie_premiere_page:{(page + 1) % len(items)}"),
        ])
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="lz_prem"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    return msg, InlineKeyboardMarkup(rows), page


async def _movie_premieres_with_posters(cid):
    items = [dict(item) for item in (await get_movie_premieres(cid))][:5]
    posters = await asyncio.gather(*(
        asyncio.to_thread(tmdb.english_poster, item.get("id"), "movie")
        for item in items
    ))
    for item, poster in zip(items, posters):
        item["poster"] = str(poster or "").strip()
    return [item for item in items if item.get("poster")]


async def send_movie_premieres(bot, cid, *, status=None):
    items = await _movie_premieres_with_posters(cid)
    msg, kb, page = _movie_premieres_view(cid, items)
    if items:
        try:
            await bot.send_photo(
                chat_id=cid,
                photo=str(items[page].get("poster") or "").strip(),
                caption=msg.text,
                caption_entities=msg.entities,
                reply_markup=kb,
            )
            return
        except Exception:
            pass
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb)
        return
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def show_movie_premiere_page(cid, q, page):
    items = await _movie_premieres_with_posters(cid)
    if not items:
        return
    msg, kb, page = _movie_premieres_view(cid, items, page)
    await q.edit_message_media(
        media=InputMediaPhoto(
            media=str(items[page].get("poster") or "").strip(),
            caption=msg.text,
            caption_entities=msg.entities,
        ),
        reply_markup=kb,
    )


async def get_series_premieres(cid):
    """Новые сериалы и новые сезоны избранного с рейтингом выше 7."""
    today = datetime.now(config.TZ).date()
    new_series_start = today - timedelta(days=30)
    end = today + timedelta(days=60)
    favorites = store.get_list(config.FAVORITE_MOVIES_KEY, cid)

    async def favorite_seasons(value):
        if "(фильм" in str(value or "").casefold():
            return []
        title = movie_title_for_lookup(value)
        found = await asyncio.to_thread(tmdb.search_id, title)
        if not found or found.get("kind") != "tv" or float(found.get("rating") or 0) <= 7:
            return []
        seasons = await asyncio.to_thread(
            tmdb.upcoming_tv_seasons, found.get("id"), today, end,
        )
        seasons = [dict(item) for item in seasons]
        for item in seasons:
            item["favorite"] = True
        return seasons

    favorite_groups, new_series = await asyncio.gather(
        asyncio.gather(*(favorite_seasons(value) for value in favorites)),
        asyncio.to_thread(tmdb.upcoming_tv_releases, new_series_start, end),
    )
    candidates = [item for group in favorite_groups for item in group]
    candidates.extend(new_series or [])
    result, seen = [], set()
    favorite_ids = {
        str(item.get("id")) for item in candidates
        if item.get("favorite") and item.get("id")
    }
    for item in candidates:
        rating = float(item.get("rating") or 0)
        key = (str(item.get("id") or item.get("name") or ""), int(item.get("season_number") or 0))
        if not item.get("favorite") and str(item.get("id")) in favorite_ids:
            continue
        if rating <= 7 or not item.get("poster") or not item.get("overview") or key in seen:
            continue
        seen.add(key)
        result.append(item)
    result.sort(key=lambda item: (
        not bool(item.get("favorite")),
        str(item.get("release_date") or ""),
        -float(item.get("rating") or 0),
    ))
    result = result[:5]
    posters = await asyncio.gather(*(
        asyncio.to_thread(
            tmdb.english_poster, item.get("id"), "tv",
            item.get("season_number") if item.get("season_number") else None,
        ) for item in result
    ))
    for item, poster in zip(result, posters):
        item["poster"] = str(poster or "").strip()
    return [item for item in result if item.get("poster")]


def _series_premieres_view(items, page=0):
    page = max(0, min(int(page), len(items) - 1)) if items else 0
    msg = leisure_ui.series_premiere_screen(items[page] if items else None)
    rows = []
    if len(items) > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"series_premiere_page:{(page - 1) % len(items)}"),
            InlineKeyboardButton(f"{page + 1}/{len(items)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"series_premiere_page:{(page + 1) % len(items)}"),
        ])
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="lz_prem"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    return msg, InlineKeyboardMarkup(rows), page


async def send_series_premieres(bot, cid, *, status=None):
    items = await get_series_premieres(cid)
    msg, kb, page = _series_premieres_view(items)
    if items:
        try:
            await bot.send_photo(
                chat_id=cid, photo=items[page]["poster"], caption=msg.text,
                caption_entities=msg.entities, reply_markup=kb,
            )
            return
        except Exception:
            pass
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb)
        return
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def show_series_premiere_page(cid, q, page):
    items = await get_series_premieres(cid)
    if not items:
        return
    msg, kb, page = _series_premieres_view(items, page)
    await q.edit_message_media(
        media=InputMediaPhoto(
            media=items[page]["poster"], caption=msg.text,
            caption_entities=msg.entities,
        ),
        reply_markup=kb,
    )


async def _combined_premieres(cid):
    movies, series = await asyncio.gather(
        _movie_premieres_with_posters(cid), get_series_premieres(cid),
    )
    combined = [dict(item, premiere_kind="movie") for item in movies]
    combined.extend(dict(item, premiere_kind="series") for item in series if item.get("poster"))
    combined.sort(key=lambda item: str(
        item.get("release_date") or item.get("air_date") or item.get("date") or "9999-99-99"
    ))
    return combined[:10]


def _combined_premieres_view(items, page=0):
    page = max(0, min(int(page), len(items) - 1)) if items else 0
    msg = leisure_ui.combined_premiere_screen(items[page] if items else None)
    rows = []
    if len(items) > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"combined_premiere_page:{(page - 1) % len(items)}"),
            InlineKeyboardButton(f"{page + 1}/{len(items)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"combined_premiere_page:{(page + 1) % len(items)}"),
        ])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="lz_prem"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    return msg, InlineKeyboardMarkup(rows), page


async def send_combined_premieres(bot, cid, *, status=None):
    items = await _combined_premieres(cid)
    msg, kb, page = _combined_premieres_view(items)
    if items:
        try:
            await bot.send_photo(
                chat_id=cid, photo=items[page]["poster"], caption=msg.text,
                caption_entities=msg.entities, reply_markup=kb,
            )
            return
        except Exception:
            pass
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb)
    else:
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def show_combined_premiere_page(cid, q, page):
    items = await _combined_premieres(cid)
    if not items:
        return
    msg, kb, page = _combined_premieres_view(items, page)
    try:
        await q.edit_message_media(
            media=InputMediaPhoto(media=items[page]["poster"], caption=msg.text,
                                  caption_entities=msg.entities),
            reply_markup=kb,
        )
        return
    except Exception:
        pass
    try:
        await q.edit_message_caption(
            caption=msg.text, caption_entities=msg.entities, reply_markup=kb,
        )
    except Exception:
        await q.edit_message_text(text=msg.text, entities=msg.entities, reply_markup=kb)
