"""Страна и описание артиста через Wikidata и Википедию (резерв MusicBrainz) — без ключа; кэш — в countries."""
import logging
import re
import threading
import time
from urllib.parse import quote

import requests

_log = logging.getLogger(__name__)

_API = "https://www.wikidata.org/w/api.php"
_TIMEOUT_SECONDS = 6
_HEADERS = {"User-Agent": "morning-bot/1.0 (personal Telegram bot)"}
_MIN_INTERVAL_SECONDS = 1.0  # Wikidata отвечает 429 на частые запросы
_rate_lock = threading.Lock()
_last_request = [0.0]


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


_HUMAN = "Q5"


_MUSIC_MARKERS = ("singer", "musician", "rapper", "band", "group", "composer", "producer", "dj",
                  "певец", "певица", "музыкант", "рэпер", "группа", "композитор")
_MUSIC_GROUPS = {"Q215380", "Q5741069", "Q641066", "Q2088357"}  # музыкальная группа, рок-группа, гёрлз-, бойз-бенд


def artist_country(name) -> str:
    """Резерв MusicBrainz: страна артиста или группы (гражданство P27 / страна происхождения P495)."""
    return _person_country(name, _MUSIC_MARKERS, groups=_MUSIC_GROUPS)


def _person_country(name, markers, groups=frozenset()) -> str:
    name = " ".join(str(name or "").split()).strip()
    if not name:
        return ""
    search = _get({"action": "wbsearchentities", "search": name, "language": "en", "type": "item", "limit": 5})
    ids = [item.get("id") for item in (search or {}).get("search") or [] if item.get("id")]
    if not ids:
        return ""
    data = _get({"action": "wbgetentities", "ids": "|".join(ids), "props": "claims|descriptions",
                 "languages": "en|ru"})
    entities = (data or {}).get("entities") or {}
    for entity_id in ids:  # порядок поиска — по релевантности
        entity = entities.get(entity_id) or {}
        description = " ".join(str(value.get("value") or "")
                               for value in (entity.get("descriptions") or {}).values()).casefold()
        kinds = set(_claim_ids(entity, "P31"))
        is_group = bool(kinds & set(groups))
        if not is_group and (_HUMAN not in kinds or not any(m in description for m in markers)):
            continue
        citizenship = _claim_ids(entity, "P495" if is_group else "P27")
        if not citizenship:
            return ""
        # Историческое государство (Соединённое Королевство 1801–1922) кода ISO не имеет — берём следующее.
        found = _get({"action": "wbgetentities", "ids": "|".join(citizenship[:5]), "props": "claims"})
        for country_id in citizenship[:5]:
            claims = (((found or {}).get("entities") or {}).get(country_id) or {}).get("claims") or {}
            for claim in claims.get("P297") or []:
                value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                if isinstance(value, str) and len(value) == 2:
                    return value.upper()
        return ""
    return ""


def article_titles(qid) -> dict:
    """{"ruwiki": название, "enwiki": название, "ru_desc": краткое описание} по Q-id; чего нет — нет ключа."""
    data = _get({"action": "wbgetentities", "ids": qid, "props": "sitelinks|descriptions",
                 "languages": "ru", "sitefilter": "ruwiki|enwiki"})
    entity = (data.get("entities") or {}).get(qid) or {}
    found = {site: link["title"] for site, link in (entity.get("sitelinks") or {}).items() if link.get("title")}
    if desc := ((entity.get("descriptions") or {}).get("ru") or {}).get("value"):
        found["ru_desc"] = desc
    return found


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[A-ZА-ЯЁ«\"0-9])")


def article_intro(lang, title, sentences=2) -> str:
    """Первые предложения статьи Википедии (REST summary — без скобок с датами и сносок); сбой — исключение."""
    response = requests.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}",
                            headers=_HEADERS, timeout=_TIMEOUT_SECONDS)
    response.raise_for_status()
    extract = " ".join(str(response.json().get("extract") or "").split())
    return " ".join(_SENTENCE_END.split(extract)[:sentences])
