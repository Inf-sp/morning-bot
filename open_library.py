"""Публичные книжные метаданные Open Library без API-ключа."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import requests

import util


_SEARCH_URL = "https://openlibrary.org/search.json"
_CACHE_TTL = 7 * 86400
_HEADERS = {"User-Agent": "morning-bot/1.0 (book discovery)"}


def cover_for_isbn(isbn):
    """Возвращает URL только существующей обложки; default=false исключает заглушку."""
    normalized = "".join(char for char in str(isbn or "").upper() if char.isdigit() or char == "X")
    if len(normalized) not in (10, 13):
        return ""
    url = f"https://covers.openlibrary.org/b/isbn/{normalized}-L.jpg?default=false"
    try:
        response = requests.get(url, headers=_HEADERS, timeout=6, stream=True)
        content_type = str(response.headers.get("Content-Type") or "").casefold()
        return url if response.status_code == 200 and content_type.startswith("image/") else ""
    except requests.RequestException:
        return ""


def _publication_date(values, today):
    formats = ("%Y-%m-%d", "%Y-%m", "%B %d, %Y", "%b %d, %Y", "%B %Y", "%b %Y")
    parsed = []
    for value in values or []:
        raw = str(value or "").strip()
        for fmt in formats:
            try:
                candidate = datetime.strptime(raw, fmt).date()
                parsed.append(candidate)
                break
            except ValueError:
                continue
    recent = [value for value in parsed if today - timedelta(days=90) <= value <= today]
    return max(recent).isoformat() if recent else ""


def search_recent_releases(today=None, max_results=60):
    """Возвращает впервые опубликованные за 90 дней книги с ISBN и обложкой."""
    today = today or date.today()
    cache_key = f"recent:{today.isoformat()}:{int(max_results)}"
    cached = util.ttl_get("open_library_books", cache_key, _CACHE_TTL)
    if isinstance(cached, list):
        return [dict(item) for item in cached]
    docs = []
    for subject in ("fiction", "biography", "history"):
        try:
            response = requests.get(
                _SEARCH_URL,
                params={
                    "q": f"first_publish_year:[{today.year - 1} TO {today.year}] subject:{subject}",
                    "sort": "new", "limit": max(1, min(100, int(max_results))),
                    "fields": (
                        "key,title,author_name,first_publish_year,publish_date,isbn,cover_i,"
                        "publisher,ratings_average,ratings_count,subject"
                    ),
                },
                headers=_HEADERS,
                timeout=8,
            )
            if response.status_code == 200:
                docs.extend(response.json().get("docs") or [])
        except (requests.RequestException, TypeError, ValueError):
            continue
    items = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        released = _publication_date(doc.get("publish_date"), today)
        authors = [str(value).strip() for value in doc.get("author_name") or [] if str(value).strip()]
        isbns = [str(value).strip() for value in doc.get("isbn") or [] if str(value).strip()]
        cover_id = doc.get("cover_i")
        if (not released or not authors or not isbns or not cover_id
                or int(doc.get("first_publish_year") or 0) != int(released[:4])):
            continue
        isbn = next((value for value in isbns if len(value.replace("-", "")) == 13), isbns[0])
        key = str(doc.get("key") or "").strip()
        items.append({
            "title": str(doc.get("title") or "").strip(),
            "author": ", ".join(authors),
            "authors": authors,
            "published_date": released,
            "isbn": isbn,
            "cover_url": f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg",
            "publisher": ", ".join(str(value) for value in (doc.get("publisher") or [])[:2]),
            "rating": doc.get("ratings_average"),
            "ratings_count": int(doc.get("ratings_count") or 0),
            "categories": [str(value) for value in (doc.get("subject") or [])[:3]],
            "info_link": f"https://openlibrary.org{key}" if key.startswith("/") else "",
            "publisher_date_confirmed": True,
        })
    if items:
        util.ttl_set("open_library_books", cache_key, items)
    return items
