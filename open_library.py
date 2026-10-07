"""Публичные книжные метаданные Open Library без API-ключа."""

from __future__ import annotations

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


def search_books(
    title, alternative_title="", author="", year="", max_results=12,
    english_only=False,
):
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
                        "publisher,ratings_average,ratings_count,subject,language"
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
        languages = [str(value).casefold() for value in (doc.get("language") or [])]
        if english_only and not any(value in ("eng", "en") for value in languages):
            continue
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
            "language": "en" if any(value in ("eng", "en") for value in languages) else "",
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


