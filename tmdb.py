"""Обёртки TMDb API для рекомендаций кино.

Все вызовы синхронные (requests), с TTL-кэшем через util.ttl_get/ttl_set.
Язык — ru-RU. Ключ — config.TMDB_API_KEY. Каждый результат нормализуется к
единому dict, совместимому с существующей карточкой (name/name_en/year/rating/
genres/kind/poster/url/overview/id).

Endpoint'ы:
- search_id      — резолв названия → (id, kind)
- recommendations / similar — основной источник кандидатов
- detail         — детали (runtime/страна/студия для movie; сезоны/статус/… для tv)
- discover       — подбор по жанру/настроению/фильтрам
"""
from dataclasses import dataclass
from datetime import date, datetime

import config
import api_usage
import util

_BASE = "https://api.themoviedb.org/3"
_IMG = "https://image.tmdb.org/t/p/w500"
_LANG = "ru-RU"

# genre_id → русское имя (movie + tv). Совпадает с leisure._TMDB_GENRES.
GENRES = {
    28: "боевик", 12: "приключения", 16: "анимация", 35: "комедия", 80: "криминал",
    99: "документальный", 18: "драма", 10751: "семейный", 14: "фэнтези", 36: "история",
    27: "ужасы", 10402: "музыка", 9648: "детектив", 10749: "мелодрама", 878: "фантастика",
    10770: "телефильм", 53: "триллер", 10752: "военный", 37: "вестерн",
    10759: "боевик", 10762: "детское", 10763: "новости", 10764: "реалити",
    10765: "фантастика", 10766: "мыло", 10767: "ток-шоу", 10768: "военное",
}

# Имя жанра → genre_id для discover (movie-центрично; tv-эквиваленты подставляются в discover).
GENRE_NAME_TO_ID = {
    "боевик": 28, "приключения": 12, "анимация": 16, "комедия": 35, "криминал": 80,
    "документальный": 99, "драма": 18, "семейный": 10751, "фэнтези": 14, "история": 36,
    "ужасы": 27, "музыка": 10402, "детектив": 9648, "мелодрама": 10749, "романтика": 10749,
    "фантастика": 878, "триллер": 53, "военный": 10752, "вестерн": 37, "sci-fi": 878,
}

_BAD = ("making of", "behind the scenes", "bonus", "featurette",
        "the making", "deleted scenes", "trailer", "teaser")


@dataclass(frozen=True)
class CinemaMovie:
    id: int | str
    title: str
    original_title: str | None
    overview: str | None
    poster_url: str | None
    release_date: date | None
    genres: list[str]
    rating: float | None
    popularity: float | None
    country_code: str
    is_theatrical: bool


def _get(path, params, timeout=12, language=None):
    """GET к TMDb, возвращает json или None (без исключений наружу)."""
    if not config.TMDB_API_KEY:
        return None
    import requests
    p = {"api_key": config.TMDB_API_KEY, "language": language or _LANG}
    p.update(params or {})
    try:
        r = requests.get(f"{_BASE}{path}", params=p, timeout=timeout)
        api_usage.record_request("tmdb", ok=200 <= r.status_code < 300, status_code=r.status_code,
                                 error="" if 200 <= r.status_code < 300 else f"HTTP {r.status_code}",
                                 headers=r.headers)
        return r.json()
    except Exception as e:
        api_usage.record_request("tmdb", ok=False, error=type(e).__name__)
        return None


def _poster(path):
    return f"{_IMG}{path}" if path else None


def _year(x):
    date = x.get("release_date") or x.get("first_air_date") or ""
    return date[:4] if date else ""


def _kind_of(x, default=None):
    mt = x.get("media_type")
    if mt in ("movie", "tv"):
        return mt
    return default


def _is_readable(s):
    """True, если строка содержит буквы кириллицы или латиницы (читаемо для рус. юзера).

    Названия на тайском/японском/корейском/китайском/арабском и т.п. считаем нечитаемыми —
    для них лучше показать латинское оригинальное название.
    """
    return any(("а" <= c.lower() <= "я") or ("a" <= c.lower() <= "z") for c in s)


def _display_name(localized, original):
    """Выбирает читаемое название: локализованное, если читаемо; иначе латинский оригинал."""
    localized = (localized or "").strip()
    original = (original or "").strip()
    if localized and _is_readable(localized):
        return localized
    if original and _is_readable(original):
        return original
    return localized or original


def _parse_date(raw):
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _float_or_none(value):
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    return val


def _genre_names(x):
    names = []
    for g in x.get("genres") or []:
        if isinstance(g, dict):
            name = (g.get("name") or "").strip()
            if name:
                names.append(name)
    if names:
        return names
    for gid in x.get("genre_ids") or []:
        name = GENRES.get(gid)
        if name and name not in names:
            names.append(name)
    return names


def _cinema_movie(x, country_code):
    localized = x.get("title") or x.get("name") or ""
    original = (x.get("original_title") or x.get("original_name") or "").strip() or None
    if not _is_readable(localized) and not _is_readable(original or ""):
        return None
    rating = _float_or_none(x.get("vote_average"))
    if rating is not None and rating <= 0:
        rating = None
    popularity = _float_or_none(x.get("popularity"))
    return CinemaMovie(
        id=x.get("id"),
        title=_display_name(localized, original or ""),
        original_title=original,
        overview=(x.get("overview") or "").strip() or None,
        poster_url=_poster(x.get("poster_path")),
        release_date=_parse_date(x.get("release_date")),
        genres=_genre_names(x),
        rating=rating,
        popularity=popularity,
        country_code=(country_code or "").upper(),
        is_theatrical=True,
    )


def normalize(x, kind=None):
    """Приводит сырой TMDb-объект к единому dict карточки."""
    kind = _kind_of(x, kind) or ("tv" if x.get("name") else "movie")
    genre_ids = x.get("genre_ids") or [g.get("id") for g in (x.get("genres") or []) if isinstance(g, dict)]
    genres = ", ".join(GENRES.get(g, "") for g in genre_ids[:3] if GENRES.get(g))
    localized = x.get("title") or x.get("name") or ""
    original = x.get("original_title") or x.get("original_name") or ""
    return {
        "id": x.get("id"),
        "name": _display_name(localized, original),
        "name_en": original,
        "year": _year(x),
        "rating": x.get("vote_average") or 0,
        "genre_ids": [g for g in genre_ids if g],
        "genres": genres,
        "kind": kind,
        "poster": _poster(x.get("poster_path")),
        "url": f"https://www.themoviedb.org/{kind}/{x.get('id')}" if x.get("id") else "",
        "overview": x.get("overview", "") or "",
    }


def _clean(results, kind=None):
    out = []
    for x in results or []:
        nm = (x.get("title") or x.get("name") or "").lower()
        if not nm or any(b in nm for b in _BAD):
            continue
        out.append(normalize(x, kind))
    return out


# ---------- endpoints ----------
def search_id(title, kind=None):
    """Резолв названия → нормализованный dict (с id/kind) или None."""
    if not config.TMDB_API_KEY or not title:
        return None
    ck = f"{kind or 'multi'}|{title}".strip().lower()
    cached = util.ttl_get("tmdb_search_id", ck, 86400)
    if cached is not None:
        return cached or None
    if kind in ("movie", "tv"):
        data = _get(f"/search/{kind}", {"query": title, "include_adult": "false"})
    else:
        data = _get("/search/multi", {"query": title, "include_adult": "false"})
    items = _clean((data or {}).get("results", []), kind)
    result = items[0] if items else None
    util.ttl_set("tmdb_search_id", ck, result or False)
    return result


def recommendations(tmdb_id, kind):
    return _related(tmdb_id, kind, "recommendations")


def similar(tmdb_id, kind):
    return _related(tmdb_id, kind, "similar")


def _related(tmdb_id, kind, endpoint):
    if not config.TMDB_API_KEY or not tmdb_id or kind not in ("movie", "tv"):
        return []
    ck = f"{endpoint}|{kind}|{tmdb_id}"
    cached = util.ttl_get("tmdb_related", ck, 86400)
    if cached is not None:
        return cached
    data = _get(f"/{kind}/{tmdb_id}/{endpoint}", {"page": 1})
    items = _clean((data or {}).get("results", []), kind)
    util.ttl_set("tmdb_related", ck, items)
    return items


def detail(tmdb_id, kind):
    """Детали фильма/сериала: runtime/страна/студия или сезоны/статус/след.серия/длит."""
    if not config.TMDB_API_KEY or not tmdb_id or kind not in ("movie", "tv"):
        return None
    ck = f"detail|{kind}|{tmdb_id}"
    cached = util.ttl_get("tmdb_detail", ck, 86400)
    if cached is not None:
        return cached or None
    data = _get(f"/{kind}/{tmdb_id}", {"append_to_response": "credits"})
    if not data:
        util.ttl_set("tmdb_detail", ck, False)
        return None
    base = normalize(data, kind)
    credits = data.get("credits") or {}
    crew = credits.get("crew") or []
    cast = credits.get("cast") or []
    base["director"] = next((c.get("name") for c in crew if c.get("job") == "Director"), "")
    base["cast"] = [c.get("name") for c in cast[:5] if c.get("name")]
    countries = [c.get("iso_3166_1") for c in (data.get("production_countries") or [])]
    base["countries"] = [c for c in countries if c]
    if kind == "movie":
        base["runtime"] = data.get("runtime") or 0
        companies = [c.get("name") for c in (data.get("production_companies") or [])]
        base["studio"] = companies[0] if companies else ""
    else:
        base["seasons"] = data.get("number_of_seasons") or 0
        base["episodes"] = data.get("number_of_episodes") or 0
        base["status"] = data.get("status") or ""
        base["next_episode"] = data.get("next_episode_to_air") or None
        rt = data.get("episode_run_time") or []
        base["episode_runtime"] = rt[0] if rt else 0
    util.ttl_set("tmdb_detail", ck, base)
    return base


def discover(kind, genre_ids=None, min_rating=None, year_gte=None, region=None,
             keywords=None, sort_by="popularity.desc", page=1):
    """Подбор кандидатов по жанру/фильтрам/настроению."""
    if not config.TMDB_API_KEY or kind not in ("movie", "tv"):
        return []
    params = {"sort_by": sort_by, "page": page, "vote_count.gte": 50}
    if genre_ids:
        params["with_genres"] = ",".join(str(g) for g in genre_ids)
    if min_rating:
        params["vote_average.gte"] = min_rating
    if keywords:
        params["with_keywords"] = ",".join(str(k) for k in keywords)
    if region:
        params["with_origin_country"] = region
    if year_gte:
        key = "primary_release_date.gte" if kind == "movie" else "first_air_date.gte"
        params[key] = f"{year_gte}-01-01"
    ck = f"discover|{kind}|" + "|".join(f"{k}={v}" for k, v in sorted(params.items()))
    cached = util.ttl_get("tmdb_discover", ck, 21600)
    if cached is not None:
        return cached
    data = _get(f"/discover/{kind}", params)
    items = _clean((data or {}).get("results", []), kind)
    util.ttl_set("tmdb_discover", ck, items)
    return items


def _regional_movie_page(endpoint, country_code, language, page, *, success_ttl, empty_ttl, error_ttl):
    cc = (country_code or "").upper()
    lang = language or _LANG
    key = f"{endpoint}|{cc}|{lang}|{page}"
    cached = util.ttl_get("tmdb_cinema_success", key, success_ttl)
    if cached is not None:
        return cached
    cached_empty = util.ttl_get("tmdb_cinema_empty", key, empty_ttl)
    if cached_empty is not None:
        return []
    cached_error = util.ttl_get("tmdb_cinema_error", key, error_ttl)
    if cached_error is not None:
        return []

    data = _get(f"/movie/{endpoint}", {"region": cc, "page": page}, timeout=15, language=lang)
    if not isinstance(data, dict):
        util.ttl_set("tmdb_cinema_error", key, True)
        return []

    results = data.get("results", [])
    items = [m for x in results if x.get("id") and (m := _cinema_movie(x, cc)) is not None]
    if items:
        util.ttl_set("tmdb_cinema_success", key, items)
    else:
        util.ttl_set("tmdb_cinema_empty", key, True)
    return items


def _regional_movies(endpoint, country_code, language, *, max_pages, success_ttl, empty_ttl, error_ttl):
    seen = {}
    ordered = []
    for page in range(1, max_pages + 1):
        items = _regional_movie_page(
            endpoint,
            country_code,
            language,
            page,
            success_ttl=success_ttl,
            empty_ttl=empty_ttl,
            error_ttl=error_ttl,
        )
        if not items:
            break
        for movie in items:
            if movie.id in seen:
                continue
            seen[movie.id] = movie
            ordered.append(movie.id)
        if len(items) < 20:
            break
    return [seen[mid] for mid in ordered]


def _recent_release_bucket(movie, today):
    rel = movie.release_date
    if rel is None:
        return 1
    delta = (today - rel).days
    return 0 if 0 <= delta <= 7 else 1


def get_now_playing(country_code, language=_LANG):
    """Фильмы, которые сейчас идут в кинотеатрах выбранной страны."""
    if not config.TMDB_API_KEY:
        return []
    movies = _regional_movies(
        "now_playing",
        country_code,
        language,
        max_pages=3,
        success_ttl=6 * 3600,
        empty_ttl=30 * 60,
        error_ttl=15 * 60,
    )
    today = date.today()
    ranked = list(enumerate(movies))
    ranked.sort(key=lambda pair: (
        _recent_release_bucket(pair[1], today),
        -(pair[1].popularity or 0.0),
        pair[0],
    ))
    return [movie for _idx, movie in ranked]


def get_upcoming_theatrical_releases(country_code, start_date, end_date, language=_LANG):
    """Будущие региональные кинопремьеры в заданном окне дат."""
    if not config.TMDB_API_KEY:
        return []
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    movies = _regional_movies(
        "upcoming",
        country_code,
        language,
        max_pages=3,
        success_ttl=24 * 3600,
        empty_ttl=60 * 60,
        error_ttl=15 * 60,
    )
    filtered = [movie for movie in movies if movie.release_date and start_date <= movie.release_date <= end_date]
    ranked = list(enumerate(filtered))
    ranked.sort(key=lambda pair: (
        pair[1].release_date,
        -(pair[1].popularity or 0.0),
        pair[0],
    ))
    return [movie for _idx, movie in ranked]
