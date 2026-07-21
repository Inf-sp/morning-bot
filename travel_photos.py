"""Touristic country covers from Pexels with official Unsplash fallback."""

from __future__ import annotations

import re
import time

import requests

import api_usage
import config
import provider_runtime


_BLOCKED = re.compile(
    r"\b(person|people|man|men|woman|women|girl|boy|portrait|selfie|food|meal|"
    r"map|flag|text|sign|logo|poster|menu|illustration|drawing)\b",
    re.IGNORECASE,
)


def _score(*, width, height, description, position, strict=True):
    try:
        width, height = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return None
    if strict:
        if width <= height or width < 1200 or height < 600 or _BLOCKED.search(description or ""):
            return None
        ratio = width / max(height, 1)
        return min(width * height, 30_000_000) - abs(ratio - 1.75) * 1_000_000 - position * 20_000
    else:
        if width <= 0 or height <= 0:
            return None
        return min(width * height, 30_000_000) - position * 20_000


def _pexels(query, strict=True):
    if not config.PEXELS_API_KEY:
        return None
    started = time.monotonic()
    try:
        params = {"query": query, "size": "large", "per_page": 15, "locale": "en-US"}
        if strict:
            params["orientation"] = "landscape"
        response = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": config.PEXELS_API_KEY},
            params=params,
            timeout=12,
        )
        ok = response.status_code == 200
        api_usage.record_request(
            "pexels", ok=ok, status_code=response.status_code,
            error="" if ok else f"HTTP {response.status_code}", headers=response.headers,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        provider_runtime.record_result(
            "pexels", ok, status_code=response.status_code,
            error="" if ok else f"HTTP {response.status_code}", headers=response.headers,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        if not ok:
            return None
        candidates = []
        for position, photo in enumerate(response.json().get("photos") or []):
            if not isinstance(photo, dict):
                continue
            score = _score(width=photo.get("width"), height=photo.get("height"),
                           description=photo.get("alt"), position=position, strict=strict)
            src = photo.get("src") or {}
            url = src.get("landscape") or src.get("large2x") or src.get("large") or src.get("original")
            if score is not None and url:
                candidates.append((score, photo, url))
        if not candidates:
            return None
        _, photo, url = max(candidates, key=lambda row: row[0])
        return {
            "provider": "pexels", "id": str(photo.get("id") or ""), "url": url,
            "page_url": str(photo.get("url") or ""), "photographer": str(photo.get("photographer") or ""),
            "photographer_url": str(photo.get("photographer_url") or ""), "alt": str(photo.get("alt") or ""),
            "width": int(photo.get("width") or 0), "height": int(photo.get("height") or 0), "query": query,
        }
    except Exception as exc:
        api_usage.record_request("pexels", ok=False, error=type(exc).__name__)
        provider_runtime.record_result("pexels", False, error=type(exc).__name__)
        return None


def _unsplash(query, strict=True):
    if not config.UNSPLASH_ACCESS_KEY:
        return None
    started = time.monotonic()
    try:
        params = {"query": query, "content_filter": "high", "per_page": 15}
        if strict:
            params["orientation"] = "landscape"
        response = requests.get(
            "https://api.unsplash.com/search/photos",
            headers={"Authorization": f"Client-ID {config.UNSPLASH_ACCESS_KEY}", "Accept-Version": "v1"},
            params=params,
            timeout=12,
        )
        ok = response.status_code == 200
        api_usage.record_request(
            "unsplash", ok=ok, status_code=response.status_code,
            error="" if ok else f"HTTP {response.status_code}", headers=response.headers,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        provider_runtime.record_result(
            "unsplash", ok, status_code=response.status_code,
            error="" if ok else f"HTTP {response.status_code}", headers=response.headers,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        if not ok:
            return None
        candidates = []
        for position, photo in enumerate(response.json().get("results") or []):
            if not isinstance(photo, dict):
                continue
            description = photo.get("alt_description") or photo.get("description") or ""
            score = _score(width=photo.get("width"), height=photo.get("height"),
                           description=description, position=position, strict=strict)
            url = (photo.get("urls") or {}).get("regular") or (photo.get("urls") or {}).get("full") or (photo.get("urls") or {}).get("small")
            if score is not None and url:
                candidates.append((score, photo, url, description))
        if not candidates:
            return None
        _, photo, url, description = max(candidates, key=lambda row: row[0])
        user = photo.get("user") or {}
        provider_runtime.activate_fallback("pexels", "unsplash", reason="request")
        return {
            "provider": "unsplash", "id": str(photo.get("id") or ""), "url": url,
            "page_url": str((photo.get("links") or {}).get("html") or ""), "photographer": str(user.get("name") or ""),
            "photographer_url": str((user.get("links") or {}).get("html") or ""), "alt": str(description),
            "width": int(photo.get("width") or 0), "height": int(photo.get("height") or 0), "query": query,
        }
    except Exception as exc:
        api_usage.record_request("unsplash", ok=False, error=type(exc).__name__)
        provider_runtime.record_result("unsplash", False, error=type(exc).__name__)
        return None


def country_cover(country):
    """Return exactly one cached-ready landscape photo descriptor or None."""
    name = " ".join(str(country or "").split()).strip()
    if not name:
        return None
    query = f"{name} scenic travel landscape"
    return _pexels(query, strict=True) or _unsplash(query, strict=True)


def _normalize_pixabay_query(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    noise_words = {
        "detective", "mystery", "scene", "room", "background", "cinematic",
        "dramatic", "story", "clue", "crime", "illustration", "image",
        "picture", "ai", "generated",
    }
    words = re.findall(r"[a-zA-Z0-9'-]+", text)
    words = [w for w in words if w not in noise_words]
    return " ".join(words[:4])


def _score_pixabay_hit(hit: dict, query: str) -> float:
    tags = {
        tag.strip().lower()
        for tag in str(hit.get("tags", "")).split(",")
        if tag.strip()
    }
    query_words = set(query.lower().split())
    score = 0.0
    for word in query_words:
        if word in tags:
            score += 5
        if any(word in tag for tag in tags):
            score += 2
    score += min(int(hit.get("likes", 0)), 100) * 0.02
    crowded_tags = {
        "people", "group", "crowd", "city", "landscape", "interior",
        "room", "background", "fantasy", "collage", "concept", "scene",
    }
    score -= len(tags & crowded_tags) * 4
    return score


def _pixabay(query):
    """Fetch illustration/vector image from Pixabay using image_type=illustration."""
    if not config.PIXABAY_API_KEY:
        return None
    normalized = _normalize_pixabay_query(query)
    if not normalized:
        return None
    try:
        response = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": config.PIXABAY_API_KEY,
                "q": normalized,
                "lang": "en",
                "image_type": "illustration",
                "editors_choice": "true",
                "safesearch": "true",
                "order": "popular",
                "per_page": 30,
                "min_width": 600,
                "min_height": 600,
            },
            timeout=10,
        )
        if response.status_code != 200:
            return None
        hits = response.json().get("hits") or []
        if not hits:
            params = response.request.params if hasattr(response.request, 'params') else {
                "key": config.PIXABAY_API_KEY,
                "q": normalized,
                "lang": "en",
                "image_type": "illustration",
                "editors_choice": "false",
                "safesearch": "true",
                "order": "popular",
                "per_page": 30,
                "min_width": 600,
                "min_height": 600,
            }
            response = requests.get(
                "https://pixabay.com/api/",
                params=params,
                timeout=10,
            )
            if response.status_code != 200:
                return None
            hits = response.json().get("hits") or []
        if not hits:
            return None
        hits = [hit for hit in hits if hit.get("imageWidth", 0) >= 600 and hit.get("imageHeight", 0) >= 600]
        if not hits:
            return None
        hits.sort(
            key=lambda hit: (
                _score_pixabay_hit(hit, normalized),
                hit.get("imageWidth", 0) * hit.get("imageHeight", 0),
            ),
            reverse=True,
        )
        best = hits[0]
        url = best.get("largeImageURL") or best.get("webformatURL")
        if not url:
            return None
        return {
            "provider": "pixabay",
            "url": url,
            "alt": best.get("tags", ""),
            "width": best.get("imageWidth", 0),
            "height": best.get("imageHeight", 0),
            "query": normalized,
        }
    except Exception:
        return None


def find_illustration(query):
    """Find a beautiful illustration/drawing for the detective game result.

    Uses Pixabay (image_type=illustration) as primary source — returns actual
    drawings and vector art. Falls back to Pexels/Unsplash only if Pixabay
    has no key or returns nothing.
    """
    name = " ".join(str(query or "").split()).strip()
    if not name:
        return None
    return _pixabay(name) or _pexels(name, strict=False) or _unsplash(name, strict=False)


def find_photo(query):
    """Alias: find_illustration for the detective game."""
    return find_illustration(query)
