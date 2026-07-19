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


def _score(*, width, height, description, position):
    try:
        width, height = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return None
    if width <= height or width < 1200 or height < 600 or _BLOCKED.search(description or ""):
        return None
    ratio = width / max(height, 1)
    return min(width * height, 30_000_000) - abs(ratio - 1.75) * 1_000_000 - position * 20_000


def _pexels(query):
    if not config.PEXELS_API_KEY:
        return None
    started = time.monotonic()
    try:
        response = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": config.PEXELS_API_KEY},
            params={"query": query, "orientation": "landscape", "size": "large", "per_page": 15, "locale": "en-US"},
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
                           description=photo.get("alt"), position=position)
            src = photo.get("src") or {}
            url = src.get("landscape") or src.get("large2x") or src.get("large")
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


def _unsplash(query):
    if not config.UNSPLASH_ACCESS_KEY:
        return None
    started = time.monotonic()
    try:
        response = requests.get(
            "https://api.unsplash.com/search/photos",
            headers={"Authorization": f"Client-ID {config.UNSPLASH_ACCESS_KEY}", "Accept-Version": "v1"},
            params={"query": query, "orientation": "landscape", "content_filter": "high", "per_page": 15},
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
                           description=description, position=position)
            url = (photo.get("urls") or {}).get("regular") or (photo.get("urls") or {}).get("full")
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
    return _pexels(query) or _unsplash(query)
