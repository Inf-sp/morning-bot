"""Официальный API NS (Nederlandse Spoorwegen): станции и текущие сбои.

Ключ — ``NS_API_KEY`` (заголовок ``Ocp-Apim-Subscription-Key``). Сбой сети или
неожиданный ответ даёт пустой результат: рассылка просто ничего не присылает.
"""
import logging

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


def station_code(city):
    """Код станции NS по названию города («Alkmaar» → «AMR»); не найдено → ""."""
    city = str(city or "").strip()
    if not city:
        return ""
    cached = util.ttl_get("ns_station", city.casefold(), _STATION_TTL)
    if cached is not None:
        return cached
    data = _get("/nsapp-stations/v3", {"q": city, "countryCodes": "NL", "limit": 10})
    stations = (data or {}).get("payload") or [] if isinstance(data, dict) else []
    wanted = city.casefold()

    def names(station):
        values = (station.get("names") or {}).values() if isinstance(station.get("names"), dict) else ()
        return {str(value).casefold() for value in values}

    exact = next((s for s in stations if isinstance(s, dict) and wanted in names(s)), None)
    station = exact or next((s for s in stations if isinstance(s, dict)), None)
    code = str(((station or {}).get("id") or {}).get("code") or "") if station else ""
    if data is not None:
        util.ttl_set("ns_station", city.casefold(), code)
    return code


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
