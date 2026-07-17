"""Поиск и проверка публичных метаданных книг через Google Books API v1."""

from __future__ import annotations

import re
import time
import unicodedata
from difflib import SequenceMatcher

import requests

import api_usage
import config
import service_monitor
import util


_BASE_URL = "https://www.googleapis.com/books/v1/volumes"
_CACHE_TTL = 24 * 60 * 60


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).lower()
    return " ".join(re.findall(r"[a-zа-яё0-9]+", text, flags=re.I))


def _year(value: str) -> str:
    match = re.match(r"^(\d{4})", str(value or "").strip())
    return match.group(1) if match else ""


def _cover_url(image_links: dict) -> str:
    for key in ("extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail"):
        url = str((image_links or {}).get(key) or "").strip()
        if url:
            return re.sub(r"^http://", "https://", url)
    return ""


def _volume(item: dict) -> dict:
    info = (item or {}).get("volumeInfo") or {}
    authors = [str(author).strip() for author in (info.get("authors") or []) if str(author).strip()]
    identifiers = {
        str(value.get("type") or ""): str(value.get("identifier") or "")
        for value in (info.get("industryIdentifiers") or [])
        if isinstance(value, dict)
    }
    return {
        "google_books_id": str((item or {}).get("id") or ""),
        "title": str(info.get("title") or "").strip(),
        "subtitle": str(info.get("subtitle") or "").strip(),
        "authors": authors,
        "author": ", ".join(authors),
        "published_date": str(info.get("publishedDate") or "").strip(),
        "year": _year(info.get("publishedDate")),
        "description": str(info.get("description") or "").strip(),
        "categories": [str(value).strip() for value in (info.get("categories") or []) if str(value).strip()],
        "cover_url": _cover_url(info.get("imageLinks") or {}),
        "preview_link": str(info.get("previewLink") or "").strip(),
        "info_link": str(info.get("infoLink") or "").strip(),
        "isbn": identifiers.get("ISBN_13") or identifiers.get("ISBN_10") or "",
    }


def _match_score(volume: dict, titles: list[str], author: str) -> float:
    candidate_title = _norm(volume.get("title"))
    if not candidate_title:
        return 0.0
    score = 0.0
    for title in titles:
        wanted = _norm(title)
        if not wanted:
            continue
        ratio = SequenceMatcher(None, wanted, candidate_title).ratio()
        if wanted in candidate_title or candidate_title in wanted:
            ratio = max(ratio, 0.9)
        score = max(score, ratio)
    wanted_author = _norm(author)
    candidate_author = _norm(volume.get("author"))
    if wanted_author and candidate_author:
        author_ratio = SequenceMatcher(None, wanted_author, candidate_author).ratio()
        if wanted_author in candidate_author or candidate_author in wanted_author:
            author_ratio = max(author_ratio, 0.9)
        score += author_ratio * 0.15
    return score


def _search_items(query: str) -> list[dict]:
    if not config.GOOGLE_BOOKS_API_KEY or not str(query or "").strip():
        return []
    cache_key = _norm(query)
    cached = util.ttl_get("google_books", cache_key, _CACHE_TTL)
    if isinstance(cached, list):
        api_usage.record_cache_hit("google_books")
        return cached
    started = time.time()
    try:
        response = requests.get(
            _BASE_URL,
            params={
                "q": query,
                "key": config.GOOGLE_BOOKS_API_KEY,
                "maxResults": 8,
                "orderBy": "relevance",
                "printType": "books",
                "projection": "lite",
            },
            timeout=10,
        )
    except requests.exceptions.Timeout:
        api_usage.record_request("google_books", ok=False, error="timeout")
        return []
    except requests.exceptions.RequestException:
        api_usage.record_request("google_books", ok=False, error="network_error")
        return []
    latency_ms = int((time.time() - started) * 1000)
    if response.status_code != 200:
        api_usage.record_request(
            "google_books", ok=False, status_code=response.status_code,
            error=service_monitor.google_error_details(response), latency_ms=latency_ms,
            headers=response.headers,
        )
        return []
    try:
        items = response.json().get("items") or []
    except (TypeError, ValueError):
        api_usage.record_request(
            "google_books", ok=False, error="invalid_json", latency_ms=latency_ms,
            headers=response.headers,
        )
        return []
    api_usage.record_request(
        "google_books", ok=True, latency_ms=latency_ms, headers=response.headers,
    )
    util.ttl_set("google_books", cache_key, items)
    return items


def find_volume(title: str, alternative_title: str = "", author: str = "") -> dict | None:
    """Возвращает наиболее похожее издание, не случайный первый результат."""
    titles = [value for value in (alternative_title, title) if str(value or "").strip()]
    if not titles or not config.GOOGLE_BOOKS_API_KEY:
        return None
    query = " ".join(value for value in (titles[0], author) if str(value or "").strip())
    volumes = []
    for item in _search_items(query):
        try:
            volumes.append(_volume(item))
        except (AttributeError, TypeError, ValueError):
            continue
    ranked = sorted(
        ((_match_score(volume, titles, author), volume) for volume in volumes),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 0.58:
        return None
    return ranked[0][1]


def enrich_book(item: dict) -> dict:
    """Дополняет карточку проверяемыми метаданными, не затирая редакторский текст."""
    result = dict(item or {})
    try:
        volume = find_volume(
            result.get("title", ""), result.get("title_en", ""), result.get("author", ""),
        )
    except Exception:
        return result
    if not volume:
        return result
    for field in ("google_books_id", "cover_url", "preview_link", "info_link", "isbn"):
        if volume.get(field):
            result[field] = volume[field]
    if not result.get("author") and volume.get("author"):
        result["author"] = volume["author"]
    if not result.get("year") and volume.get("year"):
        result["year"] = volume["year"]
    if not result.get("title_en") and volume.get("title"):
        result["title_en"] = volume["title"]
    result["google_books_verified"] = True
    return result
