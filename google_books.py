"""Поиск и проверка публичных метаданных книг через Google Books API v1."""

from __future__ import annotations

import html
import re
import time
import unicodedata
from difflib import SequenceMatcher

import requests

import api_usage
import config
import provider_runtime
import util


_BASE_URL = "https://www.googleapis.com/books/v1/volumes"
_CACHE_TTL = 24 * 60 * 60
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).lower()
    return " ".join(re.findall(r"[a-zа-яё0-9]+", text, flags=re.I))


def _year(value: str) -> str:
    match = re.match(r"^(\d{4})", str(value or "").strip())
    return match.group(1) if match else ""


def _float_value(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _int_value(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _cover_url(image_links: dict) -> str:
    for key in ("extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail"):
        url = str((image_links or {}).get(key) or "").strip()
        if url:
            return re.sub(r"^http://", "https://", url)
    return ""


def _plain_description(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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
        "publisher": str(info.get("publisher") or "").strip(),
        "language": str(info.get("language") or "").strip().casefold(),
        "published_date": str(info.get("publishedDate") or "").strip(),
        "year": _year(info.get("publishedDate")),
        "description": _plain_description(info.get("description")),
        "categories": [str(value).strip() for value in (info.get("categories") or []) if str(value).strip()],
        "rating": _float_value(info.get("averageRating")),
        "ratings_count": _int_value(info.get("ratingsCount")),
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


def _search_items(query: str, max_results: int = 8, *, order_by: str = "relevance") -> list[dict]:
    if not config.GOOGLE_BOOKS_API_KEY or not str(query or "").strip():
        return []
    order_by = "newest" if order_by == "newest" else "relevance"
    try:
        result_limit = max(1, min(40, int(max_results)))
    except (TypeError, ValueError):
        result_limit = 8
    cache_key = f"{_norm(query)}|{order_by}|{result_limit}"
    cached = util.ttl_get("google_books", cache_key, _CACHE_TTL)
    if isinstance(cached, list):
        return cached
    if not api_usage.google_books_requests()["allowed"]:
        return []
    timeout = 10.0
    try:
        import tracking
        remaining = tracking.remaining_action_seconds()
        if remaining is not None:
            if remaining <= 0.2:
                return []
            timeout = min(timeout, remaining)
    except Exception:
        pass
    started = time.monotonic()
    response = None
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            response = requests.get(
                _BASE_URL,
                params={
                    "q": query,
                    "key": config.GOOGLE_BOOKS_API_KEY,
                    "maxResults": result_limit,
                    "orderBy": order_by,
                    "printType": "books",
                    "projection": "lite",
                },
                timeout=timeout,
            )
        except requests.exceptions.Timeout as exc:
            if attempt < max_attempts - 1:
                time.sleep(0.2 * (attempt + 1))
                continue
            provider_runtime.record_result(
                "google_books", False, error="timeout",
                exception_type=type(exc).__name__,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return []
        except requests.exceptions.RequestException as exc:
            if attempt < max_attempts - 1:
                time.sleep(0.2 * (attempt + 1))
                continue
            provider_runtime.record_result(
                "google_books", False, error="network_error",
                exception_type=type(exc).__name__,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return []
        finally:
            api_usage.google_books_requests(consume=True)
        if response.status_code not in _TRANSIENT_STATUSES or attempt == max_attempts - 1:
            break
        retry_after = response.headers.get("Retry-After") if response.headers else None
        try:
            delay = min(1.0, max(0.0, float(retry_after)))
        except (TypeError, ValueError):
            delay = 0.2 * (attempt + 1)
        time.sleep(delay)
    if response is None:
        return []
    if response.status_code != 200:
        provider_runtime.record_result(
            "google_books", ok=False, status_code=response.status_code,
            error=provider_runtime.google_error_details(response), headers=response.headers,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return []
    try:
        items = response.json().get("items") or []
    except (TypeError, ValueError):
        provider_runtime.record_result(
            "google_books", ok=False, error="invalid_json", headers=response.headers,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return []
    provider_runtime.record_result(
        "google_books", True, headers=response.headers,
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    util.ttl_set("google_books", cache_key, items)
    return items


def search_by_subject(subject: str, max_results: int = 40) -> list[dict]:
    """Возвращает книги из Google Books по предметной категории."""
    subject = str(subject or "").strip()
    if not subject:
        return []
    volumes = []
    for item in _search_items(f"subject:{subject}", max_results=max_results):
        try:
            volume = _volume(item)
        except (AttributeError, TypeError, ValueError):
            continue
        if volume.get("title"):
            volumes.append(volume)
    return volumes


def search_new_releases(max_results: int = 20) -> list[dict]:
    """Недавние художественные и документальные книги из Google Books.

    Google Books не даёт отдельный чарт продаж. Поэтому дальше отбираются
    только издания с оценками читателей: это надёжнее, чем называть случайную
    новую запись в каталоге «топовой».
    """
    volumes = []
    seen = set()
    per_query = max(1, min(40, int(max_results)))
    for query in ("subject:Fiction", "subject:Biography", "subject:History"):
        for item in _search_items(query, max_results=per_query, order_by="newest"):
            try:
                volume = _volume(item)
            except (AttributeError, TypeError, ValueError):
                continue
            title = _norm(volume.get("title"))
            if not title or title in seen:
                continue
            seen.add(title)
            volumes.append(volume)
    return volumes


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
    if _norm(author):
        ranked = [
            pair for pair in ranked
            if _author_match_score(pair[1], author) >= 0.55
        ]
    if not ranked or ranked[0][0] < 0.58:
        return None
    return ranked[0][1]


def _author_match_score(volume: dict, author: str) -> float:
    wanted = _norm(author)
    candidate = _norm(volume.get("author"))
    if not wanted or not candidate:
        return 0.0
    score = SequenceMatcher(None, wanted, candidate).ratio()
    if wanted in candidate or candidate in wanted:
        score = max(score, 0.9)
    return score


def _edition_key(volume: dict) -> tuple[str, tuple[str, ...], str]:
    authors = tuple(sorted(
        _norm(author)
        for author in (volume.get("authors") or [])
        if _norm(author)
    ))
    if not authors and volume.get("author"):
        authors = (_norm(volume.get("author")),)
    return _norm(volume.get("title")), authors, str(volume.get("year") or "")


def _is_children_volume(volume: dict) -> bool:
    categories = " ".join(str(value) for value in (volume.get("categories") or [])).casefold()
    return any(marker in categories for marker in (
        "juvenile fiction", "juvenile nonfiction", "children's", "children books",
        "детская литература", "книги для детей",
    ))


def find_volumes(
    title: str,
    alternative_title: str = "",
    author: str = "",
    year: str = "",
    max_results: int = 8,
    english_only: bool = False,
) -> list[dict]:
    """Возвращает ранжированные проверенные варианты книги из Google Books.

    Одинаковые карточки, найденные по локальному и оригинальному названию,
    объединяются. Одноимённые книги разных авторов остаются отдельными
    вариантами, чтобы пользователь мог переключиться на следующую.
    """
    titles = []
    seen_titles = set()
    for value in (title, alternative_title):
        value = str(value or "").strip()
        normalized = _norm(value)
        if not normalized or normalized in seen_titles:
            continue
        seen_titles.add(normalized)
        titles.append(value)
    if not titles or not config.GOOGLE_BOOKS_API_KEY:
        return []

    try:
        limit = max(1, min(40, int(max_results)))
    except (TypeError, ValueError):
        limit = 8
    wanted_year = _year(year)
    raw_volumes = []
    for query_title in titles:
        query = " ".join(
            value
            for value in (query_title, str(author or "").strip(), wanted_year)
            if value
        )
        for item in _search_items(query, max_results=limit):
            try:
                volume = _volume(item)
            except (AttributeError, TypeError, ValueError):
                continue
            if volume.get("title"):
                raw_volumes.append(volume)

    scored = []
    normalized_titles = {_norm(value) for value in titles}
    for volume in raw_volumes:
        if english_only and str(volume.get("language") or "").casefold() != "en":
            continue
        if _is_children_volume(volume):
            continue
        title_score = _match_score(volume, titles, "")
        if title_score < 0.58:
            continue
        candidate_title = _norm(volume.get("title"))
        author_score = _author_match_score(volume, author)
        published_year = _int_value(volume.get("year"))
        rank = (
            int(candidate_title in normalized_titles),
            int(bool(wanted_year) and volume.get("year") == wanted_year),
            title_score,
            author_score,
            int(bool(volume.get("cover_url"))),
            _int_value(volume.get("ratings_count")),
            float(volume.get("rating") or 0),
            published_year,
        )
        scored.append((rank, author_score, volume))

    wanted_author = _norm(author)
    if wanted_author:
        scored = [entry for entry in scored if entry[1] >= 0.55]
    scored.sort(key=lambda entry: entry[0], reverse=True)

    results = []
    seen_editions = set()
    for _rank, _author_score, volume in scored:
        edition = _edition_key(volume)
        if edition in seen_editions:
            continue
        seen_editions.add(edition)
        verified = dict(volume)
        verified["google_books_verified"] = True
        results.append(verified)
        if len(results) >= limit:
            break
    return results


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
    for field in (
        "google_books_id", "cover_url", "preview_link", "info_link", "isbn",
        "rating", "ratings_count", "categories", "description",
    ):
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
