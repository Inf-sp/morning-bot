"""Точные ссылки через YouTube Data API: треки (YouTube Music) и официальные трейлеры (YouTube).

Удачные и пустые результаты кэшируются, поэтому одинаковый запрос не повторяется для каждого чата.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from difflib import SequenceMatcher

import requests

import api_usage
import config
import provider_runtime
import store
import util

_log = logging.getLogger(__name__)


_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
_CACHE_TTL = 30 * 24 * 60 * 60
_MEMORY_CACHE_TTL = 60 * 60
_CACHE_LIMIT = 120
_CACHE_LOCK = threading.Lock()
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")
_YOUTUBE_WATCH_RE = re.compile(r"^https://(?:www\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{6,})$")
_UNWANTED_MARKERS = ("cover", "karaoke", "reaction", "tutorial", "sped up")


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return " ".join(re.findall(r"[a-zа-яё0-9]+", text, flags=re.I))


def _cache_key(track: str, artist: str) -> str:
    return f"{_norm(artist)} | {_norm(track)}"


def _youtube_music_url(video_id: str) -> str:
    return f"https://music.youtube.com/watch?v={video_id}"


def _music_url_from_cached(value: str) -> str:
    """Старые ссылки youtube.com переводим в Music без нового API-запроса."""
    url = str(value or "")
    match = _YOUTUBE_WATCH_RE.fullmatch(url)
    return _youtube_music_url(match.group(1)) if match else url


def _cache_get(key: str, music: bool = True):
    convert = _music_url_from_cached if music else str  # трейлер остаётся ссылкой youtube.com
    cached = util.ttl_get("youtube_tracks", key, _MEMORY_CACHE_TTL)
    if cached is not None:
        return convert(str(cached))
    try:
        data = store._load(config.YOUTUBE_TRACK_CACHE_KEY)
    except Exception:
        return None
    item = data.get(key) if isinstance(data, dict) else None
    if not isinstance(item, dict):
        return None
    try:
        fresh = time.time() - float(item.get("ts") or 0) < _CACHE_TTL
    except (TypeError, ValueError):
        fresh = False
    if not fresh:
        return None
    url = convert(str(item.get("url") or ""))
    util.ttl_set("youtube_tracks", key, url)
    return url


def _cache_set(key: str, url: str) -> None:
    now = time.time()

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        fresh = {}
        for existing_key, item in data.items():
            if not isinstance(item, dict):
                continue
            try:
                ts = float(item.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if now - ts < _CACHE_TTL:
                fresh[str(existing_key)] = {"ts": ts, "url": str(item.get("url") or "")}
        fresh[key] = {"ts": now, "url": str(url or "")}
        if len(fresh) > _CACHE_LIMIT:
            oldest = sorted(fresh, key=lambda item: fresh[item]["ts"])[:len(fresh) - _CACHE_LIMIT]
            for stale_key in oldest:
                fresh.pop(stale_key, None)
        return fresh, None

    try:
        store.mutate_kv(config.YOUTUBE_TRACK_CACHE_KEY, mutate)
    except Exception:
        _log.debug("_cache_set: ignored error", exc_info=True)
    util.ttl_set("youtube_tracks", key, str(url or ""))


def _similarity(wanted: str, candidate: str) -> float:
    if not wanted or not candidate:
        return 0.0
    score = SequenceMatcher(None, wanted, candidate).ratio()
    if wanted in candidate or candidate in wanted:
        score = max(score, 0.95)
    return score


def _video_score(item: dict, track: str, artist: str) -> float:
    snippet = item.get("snippet") if isinstance(item, dict) else {}
    title = _norm((snippet or {}).get("title"))
    channel = _norm((snippet or {}).get("channelTitle"))
    wanted_track, wanted_artist = _norm(track), _norm(artist)
    if not title or not wanted_track:
        return 0.0
    score = _similarity(wanted_track, title) * 0.7
    score += max(_similarity(wanted_artist, title), _similarity(wanted_artist, channel)) * 0.3
    lowered = f" {title} "
    if " official " in lowered or " audio " in lowered or " topic " in f" {channel} ":
        score += 0.05
    if any(marker in lowered for marker in _UNWANTED_MARKERS):
        score -= 0.25
    return score


def _best_video_url(items, track: str, artist: str) -> str:
    candidates = []
    for item in items or []:
        video_id = str(((item or {}).get("id") or {}).get("videoId") or "").strip()
        if _VIDEO_ID_RE.fullmatch(video_id):
            candidates.append((_video_score(item, track, artist), video_id))
    if not candidates:
        return ""
    score, video_id = max(candidates)
    if score < 0.68:
        return ""
    return _youtube_music_url(video_id)


def _timeout() -> float:
    timeout = 6.0
    try:
        import tracking
        remaining = tracking.remaining_action_seconds()
        if remaining is not None:
            timeout = min(timeout, max(0.2, float(remaining)))
    except Exception:
        _log.debug("_timeout: ignored error", exc_info=True)
    return timeout


def _search(params) -> list | None:
    """Поиск YouTube Data API: результаты; None — сбой запроса (не кэшируется)."""
    return _api(_SEARCH_URL, params)


def _api(url, params) -> list | None:
    """GET к YouTube Data API: items; None — сбой запроса (не кэшируется)."""
    started = time.monotonic()

    def record(ok, **kwargs):
        api_usage.record_request("youtube", ok, latency_ms=int((time.monotonic() - started) * 1000), **kwargs)

    try:
        response = requests.get(
            url, params={"key": config.YOUTUBE_API_KEY, "part": "snippet", **params}, timeout=_timeout(),
        )
    except requests.exceptions.Timeout:
        record(False, error="timeout")
        return None
    except requests.exceptions.RequestException:
        record(False, error="network_error")
        return None
    if response.status_code != 200:
        record(False, status_code=response.status_code,
               error=provider_runtime.google_error_details(response), headers=response.headers)
        return None
    try:
        items = response.json().get("items") or []
    except (AttributeError, TypeError, ValueError):
        record(False, error="invalid_json", headers=response.headers)
        return None
    record(True, headers=response.headers)
    return items


def find_track_url(track: str, artist: str) -> str:
    """Возвращает точную ссылку YouTube Music или пустую строку при сомнении."""
    if not config.YOUTUBE_API_KEY:
        return ""
    key = _cache_key(track, artist)
    if not key.strip(" |"):
        return ""
    cached = _cache_get(key)
    if cached is not None:
        return cached
    # The lock also prevents a burst of one API lookup per chat after a deploy.
    with _CACHE_LOCK:
        cached = _cache_get(key)
        if cached is not None:
            return cached
        items = _search({
            "q": f"{artist} {track} official audio",
            "type": "video",
            "videoCategoryId": "10",
            "maxResults": 5,
        })
        if items is None:
            return ""
        url = _best_video_url(items, track, artist)
        _cache_set(key, url)
        return url


_TRAILER_MARKERS = ("trailer", "teaser", "трейлер")
_TRAILER_UNWANTED = ("reaction", "review", "fan made", "fanmade", "concept", "explained", "breakdown",
                     "recap", "parody", "реакция", "обзор")
_TRAILER_MIN_SCORE = 0.7
_TRAILER_MIN_VIEWS = 100_000  # ролик не с канала студии — только если его посмотрели столько раз


def _trusted_trailer(video: dict | None, channels) -> bool:
    """Официальный и качественный: HD и канал студии, дистрибьютора или телеканала из TMDb — или столько
    просмотров, сколько у перезалива и фанатского монтажа не бывает."""
    if not video or (video.get("contentDetails") or {}).get("definition") != "hd":
        return False
    channel = f" {_norm((video.get('snippet') or {}).get('channelTitle'))} "
    for name in channels or ():
        name = _norm(name)
        if len(name) >= 3 and (f" {name} " in channel or channel.strip() and channel in f" {name} "):
            return True
    views = str((video.get("statistics") or {}).get("viewCount") or "0")
    return views.isdigit() and int(views) >= _TRAILER_MIN_VIEWS


def _trailer_score(item: dict, titles: list, year) -> float:
    """Ролик — трейлер нужного фильма: название целиком в заголовке ролика, слово «trailer», не реакция и не
    обзор, не старше года выхода (иначе это трейлер одноимённого старого фильма)."""
    snippet = (item or {}).get("snippet") or {}
    video = f" {_norm(snippet.get('title'))} "
    if not any(f" {marker}" in video for marker in _TRAILER_MARKERS):
        return 0.0
    if any(marker in video for marker in _TRAILER_UNWANTED):
        return 0.0
    published = str(snippet.get("publishedAt") or "")[:4]
    if str(year or "").isdigit() and published.isdigit() and int(published) < int(year) - 1:
        return 0.0
    score = max((0.95 if f" {_norm(title)} " in video else SequenceMatcher(None, _norm(title), video.strip()).ratio())
                for title in titles)
    return score + (0.05 if " official " in video or " officiele " in video else 0.0)


def find_trailer_url(titles, year="", season: int = 0, channels=()) -> str:
    """Официальный трейлер на YouTube (youtube.com/watch) по английскому и оригинальному названию и году;
    "" при сомнении. Подходящие по названию ролики проверяются одним запросом videos.list (1 единица квоты):
    HD и канал из channels (студии и телеканалы TMDb) или много просмотров. Кэш 30 дней, как у треков."""
    titles = [title for title in dict.fromkeys(" ".join(str(title or "").split()) for title in titles or ())
              if _norm(title)]
    if not config.YOUTUBE_API_KEY or not titles:
        return ""
    key = f"trailer | {_norm(titles[0])} | {year or ''} | {season or ''}"
    cached = _cache_get(key, music=False)
    if cached is not None:
        return cached
    with _CACHE_LOCK:
        cached = _cache_get(key, music=False)
        if cached is not None:
            return cached
        query = " ".join(part for part in (
            titles[0], f"season {season}" if season > 1 else "", str(year or ""), "official trailer") if part)
        items = _search({"q": query, "type": "video", "maxResults": 5})
        if items is None:
            return ""
        scored = sorted(((_trailer_score(item, titles, year), str(((item or {}).get("id") or {}).get("videoId") or ""))
                         for item in items), reverse=True)
        candidates = [video_id for score, video_id in scored
                      if score >= _TRAILER_MIN_SCORE and _VIDEO_ID_RE.fullmatch(video_id)]
        details = _api(_VIDEOS_URL, {"part": "snippet,contentDetails,statistics", "id": ",".join(candidates)}) \
            if candidates else []
        if details is None:
            return ""
        by_id = {str(video.get("id") or ""): video for video in details}
        video_id = next((video_id for video_id in candidates if _trusted_trailer(by_id.get(video_id), channels)), "")
        url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
        _cache_set(key, url)
        return url
