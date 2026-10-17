"""Свежие альбомы по жанру из открытой RSS-ленты Apple Music (без ключа); резерв — чарты Deezer (без ключа).

Источник реальных релизов для «Новинки» в музыке: даты и названия не
придумываются AI. Лента страны кэшируется на 6 часов в памяти процесса.
"""
import logging
import re
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


# Не актуальная новая музыка: DJ-миксы и диджеи, детские песни, переиздания, концертные записи, сборники.
# ponytail: список слов по названию; Apple помечает часть детских альбомов просто «Pop» — дополнить по жалобам.
_NOT_FRESH_RE = re.compile(
    r"\b(?:dj[\s-]*mix|mixed|mix|kinder\w*|lullab\w*|nursery|slaapliedjes|anniversary|remaster\w*|"
    r"super deluxe|live (?:at|in|from)|concert|greatest hits|best of|beste zangers)\b|^dj\b|\(live\)",
    re.I,
)
_CHILDREN_GENRE = "4"  # «Children's Music» / «Kindermuziek» в ленте Apple


def is_fresh_album(item) -> bool:
    """Современный новый альбом: без миксов, детской музыки, переизданий, концертов и сборников."""
    return not (_NOT_FRESH_RE.search(str(item.get("title") or ""))
                or _NOT_FRESH_RE.search(str(item.get("artist") or "")))


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


_DEEZER_GENRES = {"indie": 85, "pop": 132, "electronic": 106, "rnb": 165, "rock": 152, "hiphop": 116}
_DEEZER_ALBUMS = 12  # альбомов чарта жанра, у которых уточняется дата выхода


def _deezer_releases(genre_key, since) -> list[dict]:
    """Резерв, когда лента Apple недоступна: чарт альбомов жанра Deezer, дата выхода — из карточки
    альбома; только свежие (с since). Кэш на 6 часов."""
    genre_id = _DEEZER_GENRES.get(genre_key)
    if not genre_id:
        return []
    cached = util.ttl_get("deezer_albums", genre_key, _TTL_SECONDS)
    if cached is not None:
        return cached
    items = []
    try:
        rows = requests.get(f"https://api.deezer.com/chart/{genre_id}/albums", params={"limit": 25},
                            timeout=_TIMEOUT_SECONDS).json().get("data") or []
        for row in rows[:_DEEZER_ALBUMS]:
            album = requests.get(f"https://api.deezer.com/album/{int(row['id'])}", timeout=_TIMEOUT_SECONDS).json()
            try:
                released = date.fromisoformat(str(album.get("release_date") or ""))
            except ValueError:
                continue
            title = " ".join(str(row.get("title") or "").split())
            artist = " ".join(str((row.get("artist") or {}).get("name") or "").split())
            if title and artist and released >= since:
                items.append({"title": title, "artist": artist, "date": released.isoformat(),
                              "cover": str(row.get("cover_xl") or row.get("cover_big") or ""),
                              "url": str(row.get("link") or ""), "genre": genre_key})
    except Exception as error:
        _log.warning("deezer releases unavailable genre=%s: %r", genre_key, error)
        return []
    util.ttl_set("deezer_albums", genre_key, items)
    return items


def _deezer_json(path, params) -> dict:
    """GET к API Deezer (без ключа); ответ-ошибка (лимит) и сбой сети — исключение."""
    response = requests.get(f"https://api.deezer.com/{path}", params=params, timeout=_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    if "error" in data:  # Deezer отвечает 200 с {"error": …} при лимите запросов
        raise RuntimeError(f"deezer error: {data['error']}")
    return data


def _deezer_artist(artist) -> dict | None:
    """Артист Deezer с точно таким именем (регистр, «the», знаки и диакритика не важны) или None."""
    import countries
    wanted = countries._artist_key(artist)
    rows = _deezer_json("search/artist", {"q": artist, "limit": 5}).get("data") or []
    return next((row for row in rows if countries._artist_key(row.get("name")) == wanted), None)


def top_tracks(artist, limit=3) -> list[str]:
    """Самые популярные треки артиста на Deezer (без ключа): точное совпадение имени; нет или сбой — []."""
    try:
        found = _deezer_artist(artist)
        if not found:
            return []
        tracks = _deezer_json(f"artist/{int(found['id'])}/top", {"limit": limit}).get("data") or []
    except Exception as error:
        _log.warning("deezer top tracks unavailable artist=%s: %r", artist, error)
        return []
    return [" ".join(str(row.get("title") or "").split()) for row in tracks if row.get("title")][:limit]


_CHARTS_USER = "Deezer Charts"  # аккаунт Deezer с плейлистами «Top <страна>» — чарт песен страны


def country_chart(country_en, limit=50) -> list[tuple]:
    """Чарт песен страны — плейлист «Top <страна>» аккаунта Deezer Charts (без ключа): [(трек, артист)]; нет такого
    плейлиста — []; сбой — исключение. Лента Apple Music по стране сейчас не отвечает."""
    if not country_en:
        return []
    wanted = f"top {country_en}".casefold()
    rows = _deezer_json("search/playlist", {"q": f"Top {country_en}", "limit": 10}).get("data") or []
    playlist = next((row for row in rows if (row.get("user") or {}).get("name") == _CHARTS_USER
                     and str(row.get("title") or "").casefold() == wanted), None)
    if not playlist:
        return []
    tracks = _deezer_json(f"playlist/{int(playlist['id'])}/tracks", {"limit": limit}).get("data") or []
    return [(" ".join(str(row["title"]).split()), " ".join(str(row["artist"]["name"]).split()))
            for row in tracks if row.get("title") and (row.get("artist") or {}).get("name")]


def related_artists(artist, limit=20) -> list[dict]:
    """Похожие артисты Deezer (без ключа): [{"name", "fans"}]; нет точного совпадения имени — []; сбой — исключение."""
    found = _deezer_artist(artist)
    if not found:
        return []
    rows = _deezer_json(f"artist/{int(found['id'])}/related", {"limit": limit}).get("data") or []
    return [{"name": " ".join(str(row["name"]).split()), "fans": int(row.get("nb_fan") or 0)}
            for row in rows if row.get("name")]


# Типы релизов Deezer → подпись в «Новой музыке»; сборники (compile) — не новый релиз.
_RECORD_TYPES = {"album": "", "ep": "EP", "single": "сингл"}


def artist_releases(artist, since, until) -> list[dict]:
    """Релизы артиста на Deezer, вышедшие с since по until: альбомы, EP и синглы — без сборников, концертников и
    переизданий (`is_fresh_album`). Нет такого артиста — []; сбой или лимит Deezer — исключение."""
    found = _deezer_artist(artist)
    if not found:
        return []
    out = []
    for row in _deezer_json(f"artist/{int(found['id'])}/albums", {"limit": 25}).get("data") or []:
        title = " ".join(str(row.get("title") or "").split())
        released, kind = str(row.get("release_date") or ""), str(row.get("record_type") or "")
        if (title and kind in _RECORD_TYPES and since.isoformat() <= released <= until.isoformat()
                and is_fresh_album({"title": title, "artist": artist})):
            out.append({"title": title, "artist": " ".join(str(found.get("name") or artist).split()),
                        "date": released, "cover": str(row.get("cover_xl") or row.get("cover_big") or ""),
                        "url": str(row.get("link") or ""), "genre": "", "genre_label": _RECORD_TYPES[kind],
                        "favorite": True})
    return out


def new_releases(genre_key, cc="us", today=None) -> list[dict]:
    """[{title, artist, date, cover, url, genre}] альбомов жанра за последние 90 дней, новые первыми;
    только современные новые альбомы (`is_fresh_album`, без детской музыки по жанру Apple)."""
    wanted = GENRE_IDS.get(genre_key)
    if not wanted:
        return []
    since = (today or date.today()) - timedelta(days=FRESH_DAYS)
    feed = _feed(str(cc or "us").lower())
    if not feed:
        return sorted(filter(is_fresh_album, _deezer_releases(genre_key, since)), key=lambda item: item["date"], reverse=True)
    items = []
    for row in feed:
        genres = {str(g.get("genreId")) for g in row.get("genres") or []} if isinstance(row, dict) else set()
        if not wanted & genres or _CHILDREN_GENRE in genres:
            continue
        title = " ".join(str(row.get("name") or "").split())
        artist = " ".join(str(row.get("artistName") or "").split())
        try:
            released = date.fromisoformat(str(row.get("releaseDate") or ""))
        except ValueError:
            continue
        if title and artist and released >= since and is_fresh_album({"title": title, "artist": artist}):
            items.append({
                "title": title, "artist": artist, "date": released.isoformat(),
                "cover": str(row.get("artworkUrl100") or "").replace("100x100bb", "600x600bb"),
                "url": str(row.get("url") or ""), "genre": genre_key,
            })
    return sorted(items, key=lambda item: item["date"], reverse=True)
