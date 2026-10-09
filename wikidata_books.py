"""Названия книг через Wikidata: русское ↔ оригинальное и автор — без ключа и AI.

«Остров доктора Моро» → оригинал «The Island of Doctor Moreau», автор H. G. Wells;
и обратно — русское название для книги, найденной по-английски. Каталог (Google
Books) по-прежнему подтверждает издание и обложку; Wikidata только подсказывает,
что искать. Ответы кэшируются в памяти процесса.
"""
import logging
import re
import threading
import time

import requests

import util

_log = logging.getLogger(__name__)

_API = "https://www.wikidata.org/w/api.php"
_TIMEOUT_SECONDS = 6
_TTL_SECONDS = 30 * 86400
_HEADERS = {"User-Agent": "morning-bot/1.0 (personal Telegram bot)"}
_MIN_INTERVAL_SECONDS = 1.0  # Wikidata отвечает 429 на частые запросы
_rate_lock = threading.Lock()
_last_request = [0.0]
# Классы «литературное произведение»: роман, книга, рассказ, повесть, пьеса, сборник…
_WORK_CLASSES = {
    "Q7725634", "Q8261", "Q571", "Q47461344", "Q49084", "Q12106333", "Q25379", "Q1279564",
    "Q208628", "Q5185279", "Q112983", "Q1667921", "Q277759",
}
_WORK_MARKERS = ("роман", "повесть", "книга", "рассказ", "пьеса", "сборник", "поэма", "мемуар",
                 "novel", "book", "novella", "short story", "play by", "memoir", "poem")


class _Unavailable(Exception):
    """Wikidata не ответила — результат не кэшируем."""


def _get(params):
    with _rate_lock:  # ponytail: общий интервал на процесс; хватает для личного бота
        wait = _last_request[0] + _MIN_INTERVAL_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()
    try:
        response = requests.get(_API, params={**params, "format": "json"}, headers=_HEADERS,
                                timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except Exception as error:
        _log.warning("wikidata unavailable: %r", error)
        raise _Unavailable() from error


def _claim_ids(entity, prop):
    values = []
    for claim in (entity.get("claims") or {}).get(prop) or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            values.append(value["id"])
    return values


def _label(entity, language):
    return str(((entity.get("labels") or {}).get(language) or {}).get("value") or "").strip()


def _is_work(entity):
    if set(_claim_ids(entity, "P31")) & _WORK_CLASSES:
        return True
    description = " ".join(
        str(value.get("value") or "") for value in (entity.get("descriptions") or {}).values()
    ).casefold()
    return any(marker in description for marker in _WORK_MARKERS)


def _year(entity):
    years = []
    for claim in (entity.get("claims") or {}).get("P577") or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
        match = re.match(r"[+-]?(\d{4})", str(value.get("time") or ""))
        if match:
            years.append(match.group(1))
    return min(years) if years else ""


def lookup(title, language="ru") -> dict | None:
    """{title_ru, title_en, title_original, author_en, author_ru, year} или None."""
    title = " ".join(str(title or "").split()).strip()
    if not title:
        return None
    cache_key = f"{language}:{title.casefold()}"
    cached = util.ttl_get("wikidata_book", cache_key, _TTL_SECONDS)
    if cached is not None:
        return cached or None
    try:
        found = _lookup(title, language)
    except _Unavailable:
        return None  # сбой сети не запоминаем как «не найдено»
    util.ttl_set("wikidata_book", cache_key, found or {})
    return found


def _lookup(title, language):
    search = _get({"action": "wbsearchentities", "search": title, "language": language,
                   "uselang": language, "type": "item", "limit": 6})
    ids = [row.get("id") for row in (search or {}).get("search") or [] if row.get("id")]
    if not ids:
        return None
    data = _get({"action": "wbgetentities", "ids": "|".join(ids),
                 "props": "labels|claims|descriptions", "languages": "en|ru"})
    entities = (data or {}).get("entities") or {}
    works = [entities[item] for item in ids if item in entities and _is_work(entities[item])]
    if not works:
        return None
    # По английскому названию нужна запись произведения с русским названием, а не издание.
    work = next((item for item in works if _label(item, "ru")), works[0]) if language != "ru" else works[0]
    original = ""
    for claim in (work.get("claims") or {}).get("P1476") or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
        if value.get("text"):
            original = str(value["text"]).strip()
            break
    author_en = author_ru = ""
    authors = _claim_ids(work, "P50")
    if authors:
        author_data = _get({"action": "wbgetentities", "ids": authors[0], "props": "labels",
                            "languages": "en|ru"})
        author = ((author_data or {}).get("entities") or {}).get(authors[0]) or {}
        author_en, author_ru = _label(author, "en"), _label(author, "ru")
    title_ru = _label(work, "ru")
    return {
        "title_ru": title_ru if re.search(r"[а-яё]", title_ru, re.I) else "",
        "title_en": _label(work, "en"),
        "title_original": original,
        "author_en": author_en,
        "author_ru": author_ru,
        "year": _year(work),
    }
