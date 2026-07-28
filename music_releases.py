"""Новые заметные альбомы недели из публичного чарта Apple Music."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import requests

import config
import store


_BASE_URL = "https://rss.marketingtools.apple.com/api/v2/{country}/music/most-played/25/albums.json"
_TIMEOUT = 6


def _week_key() -> str:
    now = datetime.now(config.TZ)
    year, week, _weekday = now.isocalendar()
    return f"{year}-W{week:02d}"


def _cached(country_code: str) -> list[dict] | None:
    entry = store._load(config.MUSIC_WEEKLY_CACHE_KEY)
    if (not isinstance(entry, dict) or entry.get("week") != _week_key()
            or entry.get("date") != datetime.now(config.TZ).date().isoformat()
            or entry.get("country") != country_code):
        return None
    items = entry.get("items")
    return [dict(item) for item in items] if isinstance(items, list) else []


def _save(country_code: str, items: list[dict]) -> list[dict]:
    payload = {
        "week": _week_key(), "country": country_code,
        "date": datetime.now(config.TZ).date().isoformat(),
        "ts": int(time.time()), "items": items,
    }
    store._save(config.MUSIC_WEEKLY_CACHE_KEY, payload)
    return items


def weekly_new_albums(country_code: str = "NL", limit: int = 4) -> list[dict]:
    """Возвращает новые альбомы из регионального чарта Apple Music.

    Из чарта остаются только альбомы с датой релиза на текущей неделе: так
    витрина не выдаёт старый хит за новинку. Это best-effort источник без ключа.
    """
    country = str(country_code or "NL").strip().upper()[:2] or "NL"
    cached = _cached(country)
    if cached is not None:
        return cached[:max(1, int(limit))]
    try:
        response = requests.get(_BASE_URL.format(country=country.lower()), timeout=_TIMEOUT)
        response.raise_for_status()
        raw_items = ((response.json().get("feed") or {}).get("results") or [])
    except (requests.RequestException, TypeError, ValueError):
        return _save(country, [])

    items, seen = [], set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        artist = str(raw.get("artistName") or "").strip()
        title = str(raw.get("name") or "").strip()
        key = f"{artist.casefold()}|{title.casefold()}"
        if not artist or not title or key in seen:
            continue
        seen.add(key)
        release_date = str(raw.get("releaseDate") or "")[:10]
        try:
            released = date.fromisoformat(release_date)
        except ValueError:
            continue
        today = datetime.now(config.TZ).date()
        week_start = today - timedelta(days=today.weekday())
        if not week_start <= released <= week_start + timedelta(days=6):
            continue
        items.append({
            "artist": artist,
            "title": title,
            "release_date": release_date,
        })
        if len(items) >= 8:
            break
    return _save(country, items)[:max(1, int(limit))]
