"""Точные ссылки на музыкальные треки через YouTube Data API.

Модуль вызывается только для «Вайба дня». Удачные и пустые результаты
кэшируются, поэтому одинаковый трек не создаёт запрос для каждого чата.
"""
from __future__ import annotations

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


_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
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


def _cache_get(key: str):
    cached = util.ttl_get("youtube_tracks", key, _MEMORY_CACHE_TTL)
    if cached is not None:
        return _music_url_from_cached(str(cached))
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
    url = _music_url_from_cached(str(item.get("url") or ""))
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
        pass
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
        pass
    return timeout


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
        started = time.monotonic()
        try:
            response = requests.get(
                _SEARCH_URL,
                params={
                    "key": config.YOUTUBE_API_KEY,
                    "part": "snippet",
                    "q": f"{artist} {track} official audio",
                    "type": "video",
                    "videoCategoryId": "10",
                    "maxResults": 5,
                },
                timeout=_timeout(),
            )
        except requests.exceptions.Timeout as error:
            api_usage.record_request(
                "youtube", False, error="timeout",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return ""
        except requests.exceptions.RequestException as error:
            api_usage.record_request(
                "youtube", False, error="network_error",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return ""
        if response.status_code != 200:
            api_usage.record_request(
                "youtube", False, status_code=response.status_code,
                error=provider_runtime.google_error_details(response), headers=response.headers,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return ""
        try:
            items = response.json().get("items") or []
        except (AttributeError, TypeError, ValueError):
            api_usage.record_request(
                "youtube", False, error="invalid_json", headers=response.headers,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return ""
        api_usage.record_request(
            "youtube", True, headers=response.headers,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        url = _best_video_url(items, track, artist)
        _cache_set(key, url)
        return url
