"""Cinema home, local listings and premiere discovery."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from leisure_movies import (
        InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto,
        _BIRTHDAY_FALLBACKS, _CINEMA_BIRTHDAY_CACHE_VERSION,
        _CINEMA_BIRTHDAY_LOCK, _CINEMA_REBUSES, _MONTHS,
        _MOVIE_PREMIERES_CACHE_VERSION, _log, _movie_prefs,
        asyncio, config, datetime, leisure_ui, local_cinema, quote_plus,
        movie_title_for_lookup, requests, store, time, timedelta, tmdb,
    )


def _movie_home_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Подобрать новое кино", callback_data="movie_reco")],
        [InlineKeyboardButton("🎟️ Премьеры фильмов", callback_data="movie_premieres")],
        [InlineKeyboardButton("📺 Премьеры сериалов", callback_data="series_premieres")],
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
        "overview": str(getattr(movie, "overview", "") or "").strip(),
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
    """Открывает витрину с недельным прокатом; подбор запускается отдельной кнопкой."""
    await send_movie_now_playing(bot, cid, q=q, status=status)


def _featured_now_playing(items, *, require_overview=False):
    """На витрину попадают только достаточно известные картины из проката."""
    featured = []
    for item in items or []:
        if require_overview and not str(item.get("overview") or "").strip():
            continue
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
    return monthly_rebuses.cached_for_day("movies", day, _CINEMA_REBUSES)


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
        "rebus": await monthly_rebuses.for_day("movies", now.date(), _CINEMA_REBUSES),
        "birthday": await asyncio.to_thread(_load_cinema_birthday, now.date()),
    }


async def send_movie_now_playing(bot, cid, q=None, status=None):
    city = _movie_city(cid)
    featured = _featured_now_playing(
        await get_local_now_playing(cid, limit=20), require_overview=True,
    )[:3]
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
    """Готовит недельный прокат и дневные рубрики без сообщений пользователю."""
    await get_local_now_playing(cid, limit=20)
    await _daily_cinema_content()
    return True


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
    trailer_urls = await asyncio.gather(*(
        asyncio.to_thread(tmdb.trailer_url, item.get("id"), "movie")
        for item in items
    ))
    for item, trailer_url in zip(items, trailer_urls):
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
        InlineKeyboardButton("⬅️ Назад", callback_data="m_movie"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    return msg, InlineKeyboardMarkup(rows), page


async def _movie_premieres_with_posters(cid):
    return [
        item for item in (await get_movie_premieres(cid))
        if str(item.get("poster") or "").strip()
    ][:5]


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
    return result[:5]


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
        InlineKeyboardButton("⬅️ Назад", callback_data="m_movie"),
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
