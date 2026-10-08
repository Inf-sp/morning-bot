"""Свежие альбомы по жанру из открытой RSS-ленты Apple Music (без ключа).

Источник реальных релизов для «Новинки» в музыке: даты и названия не
придумываются AI. Лента страны кэшируется на 6 часов в памяти процесса.
"""
import logging
from datetime import date, timedelta

import requests

import util

_log = logging.getLogger(__name__)

_URL = "https://rss.marketingtools.apple.com/api/v2/{cc}/music/most-played/100/albums.json"
_TTL_SECONDS = 6 * 3600
_TIMEOUT_SECONDS = 12
FRESH_DAYS = 90
# Ключи жанров музыки бота → genreId Apple (не зависят от языка витрины).
GENRE_IDS = {
    "indie": {"20"}, "pop": {"14"}, "electronic": {"7", "17"},
    "rnb": {"15"}, "rock": {"21"}, "hiphop": {"18"},
}


def _feed(cc) -> list[dict]:
    cached = util.ttl_get("apple_music_albums", cc, _TTL_SECONDS)
    if cached is not None:
        return cached
    try:
        response = requests.get(_URL.format(cc=cc), timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
        rows = (response.json().get("feed") or {}).get("results") or []
    except Exception as error:
        _log.warning("apple music feed unavailable cc=%s: %r", cc, error)
        return []
    util.ttl_set("apple_music_albums", cc, rows)
    return rows


def new_releases(genre_key, cc="us", today=None) -> list[dict]:
    """[{title, artist, date, cover, url, genre}] альбомов жанра за последние 90 дней, новые первыми."""
    wanted = GENRE_IDS.get(genre_key)
    if not wanted:
        return []
    since = (today or date.today()) - timedelta(days=FRESH_DAYS)
    items = []
    for row in _feed(str(cc or "us").lower()):
        if not isinstance(row, dict) or not wanted & {str(g.get("genreId")) for g in row.get("genres") or []}:
            continue
        title = " ".join(str(row.get("name") or "").split())
        artist = " ".join(str(row.get("artistName") or "").split())
        try:
            released = date.fromisoformat(str(row.get("releaseDate") or ""))
        except ValueError:
            continue
        if title and artist and released >= since:
            items.append({
                "title": title, "artist": artist, "date": released.isoformat(),
                "cover": str(row.get("artworkUrl100") or "").replace("100x100bb", "600x600bb"),
                "url": str(row.get("url") or ""), "genre": genre_key,
            })
    return sorted(items, key=lambda item: item["date"], reverse=True)
