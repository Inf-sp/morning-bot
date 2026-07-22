"""Подтверждённые локальные киносеансы.

TMDB знает о национальном прокате, но не о расписании конкретного города. Этот
адаптер сначала получает список фильмов из городской афиши Biosagenda, а затем
при необходимости дополняет запись данными TMDB в ``leisure_movies``.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

import requests

import config
import store

_LOG = logging.getLogger(__name__)
_TTL_SECONDS = 60 * 60
_TITLE_RE = re.compile(r"<h[2-4][^>]*>\s*(?:<a[^>]*>)?\s*([^<]{2,100})", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_NO_TITLE = {"films", "bioscoopagenda", "filmagenda", "vandaag", "morgen", "alle films"}
_CITY_SLUGS = {"алкмар": "alkmaar"}


@dataclass(frozen=True)
class LocalCinemaMovie:
    title: str
    genres: tuple[str, ...] = ()


def _slug(city: str) -> str:
    text = (city or "").strip().lower()
    if text in _CITY_SLUGS:
        return _CITY_SLUGS[text]
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _clean(value: str) -> str:
    value = html.unescape(_TAG_RE.sub(" ", value or ""))
    return _SPACE_RE.sub(" ", value).strip(" ·|-\t\n")


def _parse_titles(page: str) -> list[LocalCinemaMovie]:
    """Извлекает названия из городской страницы без догадок.

    Страница афиши может менять разметку; заголовки остаются самым устойчивым
    представлением карточек фильмов. Если их нет, возвращаем пустой список, а
    не национальную выдачу TMDB.
    """
    out: list[LocalCinemaMovie] = []
    seen: set[str] = set()
    for raw in _TITLE_RE.findall(page or ""):
        title = _clean(raw)
        key = title.casefold()
        if len(title) < 2 or key in _NO_TITLE or key in seen:
            continue
        if not re.search(r"[A-Za-zÀ-ÿ0-9]", title):
            continue
        seen.add(key)
        out.append(LocalCinemaMovie(title=title))
    return out


def _cache_get(cid: str, city: str) -> list[dict] | None:
    data = store._load(config.LOCAL_CINEMA_CACHE_KEY) or {}
    entry = data.get(str(cid)) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or entry.get("city") != city:
        return None
    if datetime.now(config.TZ).timestamp() - float(entry.get("ts") or 0) > _TTL_SECONDS:
        return None
    movies = entry.get("movies")
    return movies if isinstance(movies, list) else None


def _cache_set(cid: str, city: str, movies: list[LocalCinemaMovie]) -> None:
    records = [{"title": movie.title, "genres": list(movie.genres)} for movie in movies]

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[str(cid)] = {"city": city, "ts": datetime.now(config.TZ).timestamp(), "movies": records}
        return data, None

    store.mutate_kv(config.LOCAL_CINEMA_CACHE_KEY, mutate)


def invalidate(cid: str) -> None:
    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data.pop(str(cid), None)
        return data, None

    store.mutate_kv(config.LOCAL_CINEMA_CACHE_KEY, mutate)


def get_city_movies(cid: str, city: str, *, refresh: bool = False) -> list[LocalCinemaMovie]:
    """Возвращает фильмы с подтверждённой городской страницы афиши.

    Пустой результат означает именно отсутствие подтверждения, а не отсутствие
    фильмов в стране. Это сохраняет обещание интерфейса «сейчас в кино».
    """
    city = (city or "").strip()
    if not city:
        return []
    if not refresh:
        cached = _cache_get(cid, city)
        if cached is not None:
            return [LocalCinemaMovie(str(x.get("title") or ""), tuple(x.get("genres") or ()))
                    for x in cached if isinstance(x, dict) and x.get("title")]
    slug = _slug(city)
    if not slug:
        return []
    url = f"https://www.biosagenda.nl/films-bioscoop-bioscopen_{quote(slug)}_3.html"
    try:
        response = requests.get(url, timeout=8, headers={"User-Agent": "morning-bot/1.0"})
        if response.status_code != 200:
            return []
        movies = _parse_titles(response.text)
    except Exception as error:
        _LOG.info("local cinema listing unavailable for %s: %r", city, error)
        return []
    _cache_set(cid, city, movies)
    return movies
