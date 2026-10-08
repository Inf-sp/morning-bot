"""Официальный API NS (Nederlandse Spoorwegen): станции и текущие сбои.

Ключ — ``NS_API_KEY`` (заголовок ``Ocp-Apim-Subscription-Key``). Сбой сети или
неожиданный ответ даёт пустой результат: рассылка просто ничего не присылает.
"""
import logging
from datetime import date

import requests

import config
import util

_log = logging.getLogger(__name__)

_BASE = "https://gateway.apiportal.ns.nl"
_TIMEOUT_SECONDS = 10
_STATION_TTL = 7 * 24 * 3600
# Сбои и аварии; плановые работы (MAINTENANCE) не присылаем.
ALERT_TYPES = ("DISRUPTION", "CALAMITY")


def _get(path, params=None):
    if not config.NS_API_KEY:
        return None
    try:
        response = requests.get(
            f"{_BASE}{path}", params=params or {},
            headers={"Ocp-Apim-Subscription-Key": config.NS_API_KEY}, timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except Exception as error:
        _log.warning("NS API unavailable path=%s: %r", path, error)
        return None


def station_codes(city):
    """Коды всех станций города: «Alkmaar» → ["AMR", "AMRN"] (Alkmaar и Alkmaar Noord)."""
    city = str(city or "").strip()
    if not city:
        return []
    cached = util.ttl_get("ns_stations", city.casefold(), _STATION_TTL)
    if cached is not None:
        return list(cached)
    data = _get("/nsapp-stations/v3", {"q": city, "countryCodes": "NL", "limit": 20})
    stations = (data or {}).get("payload") or [] if isinstance(data, dict) else []
    wanted = city.casefold()

    def belongs(station):
        names = (station.get("names") or {}).values() if isinstance(station.get("names"), dict) else ()
        return any(str(name).casefold() == wanted or str(name).casefold().startswith(f"{wanted} ")
                   for name in names)

    codes = list(dict.fromkeys(
        str((station.get("id") or {}).get("code") or "")
        for station in stations if isinstance(station, dict) and belongs(station)
    ))
    codes = [code for code in codes if code]
    if data is not None:
        util.ttl_set("ns_stations", city.casefold(), codes)
    return codes


def _label(value):
    return str((value or {}).get("label") or "").strip() if isinstance(value, dict) else ""


def parse_disruption(raw):
    """Нормализованный сбой: {id, type, title, cause, situation, until, extra} или None."""
    if not isinstance(raw, dict) or raw.get("type") not in ALERT_TYPES or raw.get("isActive") is False:
        return None
    timespan = next((span for span in raw.get("timespans") or [] if isinstance(span, dict)), {})
    expected = raw.get("expectedDuration") or {}
    return {
        "id": str(raw.get("id") or ""),
        "type": raw["type"],
        "title": str(raw.get("title") or "").strip(),
        "cause": _label(timespan.get("cause")),
        "situation": _label(timespan.get("situation")) or str(raw.get("description") or "").strip(),
        "until": str(expected.get("endTime") or timespan.get("end") or ""),
        "extra": _label(raw.get("summaryAdditionalTravelTime")),
    }


def active_disruptions(code):
    """Текущие сбои и аварии, затрагивающие станцию; None — NS не ответил."""
    if not code:
        return []
    data = _get(f"/disruptions/v3/station/{code}")
    if data is None:
        return None
    rows = data if isinstance(data, list) else (data.get("payload") or []) if isinstance(data, dict) else []
    return [item for item in map(parse_disruption, rows) if item and item["id"]]


_WORKS_TTL = 3600


def _day(value):
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def parse_works(raw, today):
    """Плановые работы (MAINTENANCE), идущие сегодня: {id, title, start, end} или None."""
    if not isinstance(raw, dict) or raw.get("type") != "MAINTENANCE":
        return None
    spans = [span for span in raw.get("timespans") or [] if isinstance(span, dict)]
    starts = [day for day in (_day(span.get("start")) for span in spans) if day]
    ends = [day for day in (_day(span.get("end")) for span in spans) if day]
    if not starts or not ends:
        return None
    start, end = min(starts), max(ends)
    if not start <= today <= end:
        return None
    return {"id": str(raw.get("id") or ""), "title": str(raw.get("title") or "").strip(),
            "start": start, "end": end}


def planned_works(code, today):
    """Работы на станции, идущие сегодня; список NS кэшируется на час. Сбой NS → []."""
    if not code:
        return []
    cache_key = f"{code}|{today.isoformat()}"
    rows = util.ttl_get("ns_works", cache_key, _WORKS_TTL)
    if rows is None:
        data = _get(f"/disruptions/v3/station/{code}")
        if data is None:
            return []
        rows = data if isinstance(data, list) else (data.get("payload") or []) if isinstance(data, dict) else []
        util.ttl_set("ns_works", cache_key, rows)
    return [item for item in (parse_works(row, today) for row in rows) if item and item["id"]]
