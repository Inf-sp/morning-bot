"""IGDB enrichment for digital game premieres.

Dates and source links remain owned by the premiere pipeline. This module only
adds a verified cover and a YouTube trailer when IGDB has a confident title
match. Board games are intentionally left untouched.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
import threading
import time

import requests

import api_usage
import config
import util


_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_MULTIQUERY_URL = "https://api.igdb.com/v4/multiquery"
_GAMES_URL = "https://api.igdb.com/v4/games"
_IMAGE_URL = "https://images.igdb.com/igdb/image/upload/t_cover_big/{image_id}.jpg"
_YOUTUBE_URL = "https://www.youtube.com/watch?v={video_id}"
_DIGITAL_PLATFORMS = {"pc", "ps5", "xbox", "switch", "mobile", "other"}
_TRAILER_WORDS = ("trailer", "teaser", "announcement", "announce", "reveal")
_LOOKUP_CACHE_TTL = 30 * 86400

_PLATFORM_IDS = {
    "pc": {6},
    "ps5": {167},
    "xbox": {169},
    "switch": {130, 508},
    "mobile": {34, 39},
}
_PLATFORM_LABELS = {
    "pc": "💻 ПК", "ps5": "🎮 PS5", "xbox": "🟩 Xbox",
    "switch": "🔴 Switch", "mobile": "📱 Мобильные",
}
_GENRES_RU = {
    "adventure": "приключение", "role-playing (rpg)": "RPG",
    "shooter": "шутер", "strategy": "стратегия", "racing": "гонки",
    "sport": "спорт", "simulator": "симулятор", "puzzle": "головоломка",
    "fighting": "файтинг", "platform": "платформер", "indie": "инди",
}
_GENRE_KEYS = {
    "adventure": "adventure", "role-playing (rpg)": "rpg",
    "shooter": "action", "fighting": "action", "hack and slash/beat 'em up": "action",
    "strategy": "strategy", "real time strategy (rts)": "strategy",
    "turn-based strategy (tbs)": "strategy",
}

_TOKEN_LOCK = threading.Lock()
_TOKEN = ""
_TOKEN_EXPIRES_AT = 0.0


def configured() -> bool:
    return bool(config.IGDB_CLIENT_ID and config.IGDB_CLIENT_SECRET)


def _request_timeout(default=12.0) -> float:
    try:
        import tracking

        remaining = tracking.remaining_action_seconds()
        if remaining is not None:
            return max(0.2, min(float(default), float(remaining)))
    except Exception:
        pass
    return float(default)


def _record(response=None, error="") -> None:
    status_code = getattr(response, "status_code", None)
    ok = response is not None and 200 <= int(status_code or 0) < 300
    api_usage.record_request(
        "igdb",
        ok=ok,
        status_code=status_code,
        error="" if ok else (error or f"HTTP {status_code or '?'}"),
        headers=getattr(response, "headers", None),
        monitor_result=False,
    )


def _access_token() -> str:
    global _TOKEN, _TOKEN_EXPIRES_AT
    if not configured():
        return ""
    now = time.time()
    if _TOKEN and now < _TOKEN_EXPIRES_AT - 60:
        return _TOKEN
    with _TOKEN_LOCK:
        now = time.time()
        if _TOKEN and now < _TOKEN_EXPIRES_AT - 60:
            return _TOKEN
        try:
            response = requests.post(
                _TOKEN_URL,
                params={
                    "client_id": config.IGDB_CLIENT_ID,
                    "client_secret": config.IGDB_CLIENT_SECRET,
                    "grant_type": "client_credentials",
                },
                timeout=_request_timeout(),
            )
            _record(response)
            if not 200 <= response.status_code < 300:
                return ""
            payload = response.json()
            token = str(payload.get("access_token") or "").strip()
            if not token:
                return ""
            try:
                expires_in = max(120, int(payload.get("expires_in") or 3600))
            except (TypeError, ValueError):
                expires_in = 3600
            _TOKEN = token
            _TOKEN_EXPIRES_AT = now + expires_in
            return token
        except Exception as exc:
            _record(error=type(exc).__name__)
            return ""


def _escape_query(value: str) -> str:
    return " ".join(str(value or "").split())[:160].replace("\\", "\\\\").replace('"', '\\"')


def _multiquery(titles: list[str], token: str) -> list[dict]:
    queries = []
    for index, title in enumerate(titles[:10]):
        queries.append(
            f'query games "game_{index}" {{ '
            f'search "{_escape_query(title)}"; '
            "fields id,name,slug,summary,cover.image_id,videos.name,videos.video_id,"
            "platforms.id,genres.name,first_release_date; limit 5; };"
        )
    if not queries:
        return []
    try:
        response = requests.post(
            _MULTIQUERY_URL,
            headers={
                "Client-ID": config.IGDB_CLIENT_ID,
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            data="\n".join(queries).encode("utf-8"),
            timeout=_request_timeout(),
        )
        _record(response)
        if not 200 <= response.status_code < 300:
            return []
        payload = response.json()
        return payload if isinstance(payload, list) else []
    except Exception as exc:
        _record(error=type(exc).__name__)
        return []


def _normalized_title(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", str(value or "").casefold()).split())


def _best_match(title: str, candidates) -> dict | None:
    expected = _normalized_title(title)
    if not expected:
        return None
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        actual = _normalized_title(candidate.get("name"))
        if actual == expected:
            return candidate
    return None


def _trailer_video_id(videos) -> str:
    for video in videos or []:
        if not isinstance(video, dict):
            continue
        name = str(video.get("name") or "").casefold()
        video_id = str(video.get("video_id") or "").strip()
        if video_id and any(word in name for word in _TRAILER_WORDS):
            return video_id
    return ""


def search_game_candidates(title: str) -> list[dict]:
    """Возвращает до пяти карточек IGDB для подтверждения перед сохранением."""
    query = " ".join(str(title or "").split()).strip()
    token = _access_token()
    if not query or not token:
        return []
    groups = _multiquery([query], token)
    candidates = next((group.get("result") or [] for group in groups
                       if isinstance(group, dict) and group.get("name") == "game_0"), [])
    expected = _normalized_title(query)
    if not candidates and expected == "the sims":
        groups = _multiquery(["The Sims 4"], token)
        candidates = next((group.get("result") or [] for group in groups
                           if isinstance(group, dict) and group.get("name") == "game_0"), [])
    result = []
    for game in candidates:
        if not isinstance(game, dict):
            continue
        name = " ".join(str(game.get("name") or "").split()).strip()
        image_id = str((game.get("cover") or {}).get("image_id") or "").strip()
        if not name or not image_id:
            continue
        platform_ids = {int(item.get("id") or 0) for item in (game.get("platforms") or [])
                        if isinstance(item, dict)}
        platforms = [key for key, identifiers in _PLATFORM_IDS.items()
                     if platform_ids.intersection(identifiers)]
        if not platforms:
            platforms = ["other"]
        genres = []
        for value in (game.get("genres") or []):
            raw = str(value.get("name") or "").strip().casefold() if isinstance(value, dict) else ""
            key = _GENRE_KEYS.get(raw) or ("simulator" if raw == "simulator" else "")
            if key and key not in genres:
                genres.append(key)
        item = {
            "igdb_id": game.get("id"), "name": name, "platforms": platforms,
            "genres": genres, "poster": _IMAGE_URL.format(image_id=image_id),
        }
        try:
            item["year"] = datetime.fromtimestamp(
                int(game.get("first_release_date")), tz=timezone.utc,
            ).year
        except (TypeError, ValueError, OSError):
            pass
        slug = str(game.get("slug") or "").strip()
        if slug:
            item["url"] = f"https://www.igdb.com/games/{slug}"
        result.append(item)
    result.sort(key=lambda item: _normalized_title(item.get("name")) != expected)
    exact = next((item for item in result
                  if _normalized_title(item.get("name")) == expected), None)
    parts = [item for item in result
             if (_normalized_title(item.get("name")) == expected
                 or _normalized_title(item.get("name")).startswith(f"{expected} "))]
    if exact and len(parts) > 1:
        franchise = dict(exact)
        franchise.update({
            "igdb_id": f"franchise:{expected}",
            "name": f"{exact['name']} (все части)",
            "franchise": True,
            "series_titles": [item["name"] for item in parts],
            "platforms": list(dict.fromkeys(
                platform for item in parts for platform in (item.get("platforms") or [])
            )),
            "genres": list(dict.fromkeys(
                genre for item in parts for genre in (item.get("genres") or [])
            )),
        })
        years = [int(item.get("year") or 0) for item in parts if item.get("year")]
        if years:
            franchise["year"] = min(years)
        result.insert(0, franchise)
    return result[:5]


def get_upcoming_games(platforms, *, today=None, days=180) -> list[dict]:
    """Return dated upcoming releases directly from the verified IGDB catalogue."""
    selected = {str(value).strip().casefold() for value in (platforms or [])}
    platform_ids = sorted({ident for key in selected for ident in _PLATFORM_IDS.get(key, set())})
    token = _access_token()
    if not token or not platform_ids:
        return []
    today = today or date.today()
    start = int(datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    end = int(datetime.combine(today + timedelta(days=days), datetime.max.time(), tzinfo=timezone.utc).timestamp())
    query = (
        "fields name,slug,first_release_date,cover.image_id,platforms.id,"
        "genres.name,summary,videos.name,videos.video_id; "
        f"where first_release_date >= {start} & first_release_date <= {end} "
        f"& platforms = ({','.join(str(value) for value in platform_ids)}) "
        "& cover != null & version_parent = null; sort first_release_date asc; limit 50;"
    )
    try:
        response = requests.post(
            _GAMES_URL,
            headers={
                "Client-ID": config.IGDB_CLIENT_ID,
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            data=query.encode("utf-8"),
            timeout=_request_timeout(),
        )
        _record(response)
        if not 200 <= response.status_code < 300:
            return []
        payload = response.json()
    except Exception as exc:
        _record(error=type(exc).__name__)
        return []
    result, seen = [], set()
    for game in payload if isinstance(payload, list) else []:
        title = " ".join(str(game.get("name") or "").split())
        slug = str(game.get("slug") or "").strip()
        timestamp = game.get("first_release_date")
        if not title or not slug or title.casefold() in seen:
            continue
        try:
            release = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date()
        except (TypeError, ValueError, OSError):
            continue
        keys = [
            key for key in _PLATFORM_IDS
            if any(
                int(item.get("id") or 0) in _PLATFORM_IDS[key]
                for item in (game.get("platforms") or []) if isinstance(item, dict)
            )
        ]
        if not set(keys).intersection(selected):
            continue
        image_id = str((game.get("cover") or {}).get("image_id") or "").strip()
        if not image_id:
            continue
        genres = [str(item.get("name") or "").strip() for item in (game.get("genres") or []) if isinstance(item, dict)]
        genre = next((_GENRES_RU.get(value.casefold(), value) for value in genres if value), "")
        trailer_id = _trailer_video_id(game.get("videos"))
        item = {
            "title": title,
            "date": release.isoformat(),
            "platforms": keys,
            "platform_label": " · ".join(_PLATFORM_LABELS[key] for key in keys),
            "genre": genre,
            "summary": "",
            "url": f"https://www.igdb.com/games/{slug}",
        }
        if image_id:
            item["poster"] = _IMAGE_URL.format(image_id=image_id)
        if trailer_id:
            item["trailer_url"] = _YOUTUBE_URL.format(video_id=trailer_id)
        result.append(item)
        seen.add(title.casefold())
    return result[:8]


def enrich_game_premieres(items) -> list[dict]:
    """Return copies of premiere items enriched with ``poster``/``trailer_url``."""
    result = [dict(item) for item in items or [] if isinstance(item, dict)]
    if not result or not configured():
        return result
    digital_indexes = [
        index for index, item in enumerate(result)
        if (not item.get("platforms")
            or set(item.get("platforms") or []).intersection(_DIGITAL_PLATFORMS))
        and str(item.get("title") or "").strip()
    ][:10]
    if not digital_indexes:
        return result
    unresolved = []
    for item_index in digital_indexes:
        cache_key = f"v2:{_normalized_title(result[item_index].get('title'))}"
        cached = util.ttl_get("igdb_game", cache_key, _LOOKUP_CACHE_TTL)
        if cached is None:
            unresolved.append(item_index)
            continue
        result[item_index].update(dict(cached))
        cached_platforms = result[item_index].get("platforms") or []
        if cached_platforms:
            result[item_index]["platform_label"] = " · ".join(
                _PLATFORM_LABELS[key] for key in cached_platforms if key in _PLATFORM_LABELS
            )
    digital_indexes = unresolved
    if not digital_indexes:
        return result
    token = _access_token()
    if not token:
        return result
    titles = [str(result[index]["title"]).strip() for index in digital_indexes]
    groups = _multiquery(titles, token)
    grouped = {
        str(group.get("name") or ""): group.get("result") or []
        for group in groups if isinstance(group, dict)
    }
    for query_index, item_index in enumerate(digital_indexes):
        match = _best_match(titles[query_index], grouped.get(f"game_{query_index}"))
        cache_key = f"v2:{_normalized_title(titles[query_index])}"
        if not match:
            util.ttl_set("igdb_game", cache_key, {})
            continue
        enrichment = {}
        image_id = str((match.get("cover") or {}).get("image_id") or "").strip()
        if image_id:
            enrichment["poster"] = _IMAGE_URL.format(image_id=image_id)
        video_id = _trailer_video_id(match.get("videos"))
        if video_id:
            enrichment["trailer_url"] = _YOUTUBE_URL.format(video_id=video_id)
        platform_ids = {
            int(value.get("id") or 0)
            for value in (match.get("platforms") or []) if isinstance(value, dict)
        }
        platforms = [
            key for key, identifiers in _PLATFORM_IDS.items()
            if platform_ids.intersection(identifiers)
        ]
        if platforms:
            enrichment["platforms"] = platforms
            enrichment["platform_label"] = " · ".join(
                _PLATFORM_LABELS[key] for key in platforms if key in _PLATFORM_LABELS
            )
        genres = [
            _GENRE_KEYS.get(str(value.get("name") or "").strip().casefold())
            for value in (match.get("genres") or []) if isinstance(value, dict)
        ]
        genres = list(dict.fromkeys(value for value in genres if value))
        if genres:
            enrichment["genres"] = genres
        try:
            enrichment["year"] = datetime.fromtimestamp(
                int(match.get("first_release_date")), tz=timezone.utc,
            ).year
        except (TypeError, ValueError, OSError):
            pass
        result[item_index].update(enrichment)
        util.ttl_set("igdb_game", cache_key, enrichment)
    return result


def enrich_game_recommendation(item) -> dict:
    """Add IGDB cover/trailer to one digital recommendation when configured."""
    if not isinstance(item, dict):
        return {}
    prepared = dict(item)
    prepared.setdefault("title", prepared.get("name"))
    enriched = enrich_game_premieres([prepared])
    return enriched[0] if enriched else prepared
