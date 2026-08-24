"""Публичные книжные метаданные Open Library без API-ключа."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
import re
import unicodedata

import requests

import util


_SEARCH_URL = "https://openlibrary.org/search.json"
_CACHE_TTL = 7 * 86400
_HEADERS = {"User-Agent": "morning-bot/1.0 (book discovery)"}


def _norm(value):
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return " ".join(re.findall(r"[a-zа-яё0-9]+", text, flags=re.I))


def search_books(title, alternative_title="", author="", year="", max_results=12):
    """Ищет проверяемые варианты книги без API-ключа."""
    titles = []
    for value in (title, alternative_title):
        value = " ".join(str(value or "").split()).strip()
        if value and _norm(value) not in {_norm(item) for item in titles}:
            titles.append(value)
    if not titles:
        return []
    try:
        limit = max(1, min(40, int(max_results)))
    except (TypeError, ValueError):
        limit = 12
    wanted_author = _norm(author)
    wanted_year = str(year or "").strip()
    docs = []
    for query_title in titles:
        cache_key = f"search:{_norm(query_title)}:{wanted_author}:{wanted_year}:{limit}"
        cached = util.ttl_get("open_library_books", cache_key, _CACHE_TTL)
        if isinstance(cached, list):
            docs.extend(cached)
            continue
        query = f'title:"{query_title}"'
        if author:
            query += f' author:"{author}"'
        try:
            response = requests.get(
                _SEARCH_URL,
                params={
                    "q": query, "limit": limit,
                    "fields": (
                        "key,title,author_name,first_publish_year,publish_year,isbn,cover_i,"
                        "publisher,ratings_average,ratings_count,subject"
                    ),
                },
                headers=_HEADERS,
                timeout=8,
            )
            found = response.json().get("docs") or [] if response.status_code == 200 else []
        except (requests.RequestException, TypeError, ValueError):
            found = []
        if found:
            util.ttl_set("open_library_books", cache_key, found)
        docs.extend(found)

    ranked = []
    normalized_titles = [_norm(value) for value in titles]
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        candidate_title = str(doc.get("title") or "").strip()
        authors = [str(value).strip() for value in doc.get("author_name") or [] if str(value).strip()]
        cover_id = doc.get("cover_i")
        published_year = str(doc.get("first_publish_year") or "").strip()
        if not candidate_title or not authors or not published_year or not cover_id:
            continue
        title_key = _norm(candidate_title)
        title_score = max(SequenceMatcher(None, wanted, title_key).ratio() for wanted in normalized_titles)
        if any(wanted in title_key or title_key in wanted for wanted in normalized_titles):
            title_score = max(title_score, 0.9)
        candidate_author = _norm(" ".join(authors))
        author_score = SequenceMatcher(None, wanted_author, candidate_author).ratio() if wanted_author else 1.0
        if wanted_author and author_score < 0.55:
            continue
        if title_score < 0.58:
            continue
        item = {
            "open_library_key": str(doc.get("key") or ""),
            "title": candidate_title,
            "author": ", ".join(authors),
            "authors": authors,
            "year": published_year,
            "isbn": next((str(value) for value in doc.get("isbn") or [] if str(value)), ""),
            "cover_url": f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg",
            "publisher": ", ".join(str(value) for value in (doc.get("publisher") or [])[:2]),
            "rating": doc.get("ratings_average"),
            "ratings_count": int(doc.get("ratings_count") or 0),
            "categories": [str(value) for value in (doc.get("subject") or [])[:3]],
            "info_link": f"https://openlibrary.org{doc.get('key')}" if str(doc.get("key") or "").startswith("/") else "",
            "open_library_verified": True,
        }
        rank = (
            int(title_key in normalized_titles),
            int(bool(wanted_year) and published_year == wanted_year),
            title_score,
            int(item["ratings_count"]),
        )
        ranked.append((rank, item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _rank, item in ranked[:limit]]


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
