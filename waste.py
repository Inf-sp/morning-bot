"""Вывоз мусора на завтра для «Плана на завтра»: календарь HVC (inzamelkalender.hvcgroep.nl, без ключа) по индексу
и номеру дома из Настроек. HVC вывозит мусор в Alkmaar и соседних городах Северной Голландии.

Адрес — только в настройках пользователя (`waste_address`), наружу уходит лишь в запрос к HVC. Ответ HVC — следующая
дата вывоза каждого вида мусора; кэш на 12 часов в памяти. Сбой или адрес вне HVC — строки нет.
"""
import logging
import re
import time

import requests

import api_usage
import util

_log = logging.getLogger(__name__)

_BASE = "https://inzamelkalender.hvcgroep.nl"
_TIMEOUT = 15
_TTL = 12 * 3600
_HEADERS = {"User-Agent": "morning-bot/1.0 (personal Telegram bot)"}
ADDRESS_KEY = "waste_address"  # настройки: {"postcode": "1811AB", "number": "12", "bagid": "…"}
_POSTCODE_RE = re.compile(r"^\s*(\d{4})\s*([A-Za-z]{2})[\s,]+(\d+)\s*([A-Za-z]?)\s*(?:[-/]?\s*(\w+))?\s*$")
# Вид мусора HVC → по-русски; неизвестный — оригинал.
_KINDS = (
    (("gft", "groente", "tuin"), "органика (GFT)"),
    (("plastic", "pmd", "pbd", "blik", "drankkarton"), "пластик и банки"),
    (("papier", "karton"), "бумага"),
    (("glas",), "стекло"),
    (("textiel",), "текстиль"),
    (("kerst",), "ёлки"),
    (("grof",), "крупный мусор"),
    (("rest",), "остальной мусор"),
)


def parse_address(text) -> dict | None:
    """«1811 AB 12», «1811AB, 12a» → {"postcode": "1811AB", "number": "12", "suffix": "a"}; не похоже — None."""
    found = _POSTCODE_RE.match(str(text or ""))
    if not found:
        return None
    return {"postcode": f"{found[1]}{found[2].upper()}", "number": found[3], "suffix": (found[4] or "").lower()}


def label(address) -> str:
    """«1811 AB 12a» — как пишут индекс в Нидерландах."""
    code = str((address or {}).get("postcode") or "")
    return f"{code[:4]} {code[4:]} {address.get('number', '')}{address.get('suffix', '')}".strip() if code else ""


def _get(path):
    started = time.monotonic()
    try:
        response = requests.get(f"{_BASE}{path}", headers=_HEADERS, timeout=_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        api_usage.record_request("hvc", ok=False, error=type(error).__name__, monitor_result=False)
        _log.info("waste: hvc unavailable error=%s", type(error).__name__)
        return None
    api_usage.record_request("hvc", ok=True, latency_ms=int((time.monotonic() - started) * 1000),
                             monitor_result=False)
    return data


def find_bagid(address) -> str:
    """Адрес → bagid HVC; адрес не обслуживает HVC или сервис не ответил — ""."""
    suffix = address.get("suffix") or ""
    rows = _get(f"/adressen/{address['postcode']}:{address['number']}{suffix}")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return ""
    return str(rows[0].get("bagid") or "")


def kind_ru(title) -> str:
    text = str(title or "").casefold()
    return next((ru for markers, ru in _KINDS if any(marker in text for marker in markers)), str(title or "").strip())


def schedule(bagid) -> dict | None:
    """{вид по-русски: ближайшая дата «2026-11-27»}; сбой — None (кэш 12 часов)."""
    if not bagid:
        return None
    cached = util.ttl_get("waste", bagid, _TTL)
    if cached is not None:
        return cached
    rows = _get(f"/rest/adressen/{bagid}/afvalstromen")
    if not isinstance(rows, list):
        return None
    found = {}
    for row in rows:
        day = str((row or {}).get("ophaaldatum") or "")[:10] if isinstance(row, dict) else ""
        if day:
            kind = kind_ru(row.get("menu_title") or row.get("title"))
            found[kind] = min(day, found.get(kind, day))
    return util.ttl_set("waste", bagid, found)


def on_day(address, day) -> list:
    """Что вывозят в этот день по адресу из Настроек: ["бумага", "органика (GFT)"]; адреса нет или сбой — []."""
    bagid = str((address or {}).get("bagid") or "")
    found = schedule(bagid) or {}
    return sorted(kind for kind, when in found.items() if when == day.isoformat())
