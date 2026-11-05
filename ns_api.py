"""Официальный API NS (Nederlandse Spoorwegen): станции и текущие сбои.

Ключ — ``NS_API_KEY`` (заголовок ``Ocp-Apim-Subscription-Key``). Сбой сети или
неожиданный ответ даёт пустой результат: рассылка просто ничего не присылает.
"""
import logging
import time
from datetime import date

import requests

import api_usage
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
    started = time.monotonic()
    try:
        response = requests.get(
            f"{_BASE}{path}", params=params or {},
            headers={"Ocp-Apim-Subscription-Key": config.NS_API_KEY}, timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as error:
        _log.warning("NS API unavailable path=%s: %r", path, error)
        api_usage.record_request(  # учёт — для статуса и давности запросов в админке
            "ns", False, error=type(error).__name__,
            status_code=getattr(getattr(error, "response", None), "status_code", None),
            latency_ms=int((time.monotonic() - started) * 1000))
        return None
    api_usage.record_request("ns", True, latency_ms=int((time.monotonic() - started) * 1000))
    return data


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


def _title(raw):
    """Участок без хвостовой точки: NS иногда пишет «Alkmaar - Hoorn.»."""
    return str(raw.get("title") or "").strip().rstrip(".").strip()


def _label(value):
    return str((value or {}).get("label") or "").strip() if isinstance(value, dict) else ""


# Что значит сбой для пассажира (publicationSections[].consequence.level), от худшего к лучшему.
_LEVELS = (("NO_OR_MUCH_LESS_TRAINS", "поездов нет или почти нет"), ("LESS_TRAINS", "меньше поездов"),
           ("DIFFERENT_TRAINS", "поезда идут иначе"))


def _travel_minutes(value) -> str:
    """«+30 мин» / «+15–30 мин» из minimumDurationInMinutes / maximumDurationInMinutes; нет чисел — ""."""
    if not isinstance(value, dict):
        return ""
    low, high = value.get("minimumDurationInMinutes"), value.get("maximumDurationInMinutes")
    low, high = (int(v) if isinstance(v, (int, float)) and v > 0 else None for v in (low, high))
    if low and high and low != high:
        return f"+{low}–{high} мин"
    return f"+{high or low} мин" if (high or low) else ""


def _station_name(station) -> str:
    if not isinstance(station, dict):
        return ""
    names = station.get("names") if isinstance(station.get("names"), dict) else {}
    return str(station.get("name") or names.get("long") or names.get("medium") or station.get("stationCode")
               or "").strip()


def practical(raw) -> dict:
    """Практическое для пассажира: участки (sections), что с поездами (level), дополнительное время (extra_min),
    чем заменили поезда (alternative) и совет NS (advice). Чего нет в ответе NS — пусто."""
    span = next((span for span in raw.get("timespans") or [] if isinstance(span, dict)), {})
    sections, levels, names = [], set(), []
    for publication in raw.get("publicationSections") or []:
        if not isinstance(publication, dict):
            continue
        stations = [_station_name(station) for station in ((publication.get("section") or {}).get("stations") or [])]
        stations = [name for name in stations if name]
        names += [name for name in stations if name not in names]
        if len(stations) >= 2:
            label = f"{stations[0]} – {stations[-1]}"
            if label not in sections:
                sections.append(label)
        levels.add(str((publication.get("consequence") or {}).get("level") or ""))
    alternative = span.get("alternativeTransport") or next(
        (item.get("alternativeTransport") for item in raw.get("alternativeTransportTimespans") or []
         if isinstance(item, dict) and item.get("alternativeTransport")), None)
    advices = [str(advice).strip() for advice in span.get("advices") or [] if str(advice or "").strip()]
    return {
        "sections": sections[:3],
        "stations": names,  # все станции затронутых секций — Alkmaar в середине Amsterdam – Den Helder тоже
        "level": next((text for key, text in _LEVELS if key in levels), ""),
        "extra_min": _travel_minutes(raw.get("summaryAdditionalTravelTime"))
        or _travel_minutes(span.get("additionalTravelTime")),
        "alternative": _label(alternative) if isinstance(alternative, dict) else "",
        "advice": advices[0] if advices else "",
    }


def parse_disruption(raw):
    """Нормализованный сбой: {id, type, title, cause, situation, start, until, extra} или None."""
    if not isinstance(raw, dict) or raw.get("type") not in ALERT_TYPES or raw.get("isActive") is False:
        return None
    timespan = next((span for span in raw.get("timespans") or [] if isinstance(span, dict)), {})
    expected = raw.get("expectedDuration") or {}
    return {
        "id": str(raw.get("id") or ""),
        "type": raw["type"],
        "title": _title(raw),
        "cause": _label(timespan.get("cause")),
        "situation": _label(timespan.get("situation")) or str(raw.get("description") or "").strip(),
        "start": str(timespan.get("start") or raw.get("start") or ""),
        "until": str(expected.get("endTime") or timespan.get("end") or ""),
        "extra": _label(raw.get("summaryAdditionalTravelTime")),
        **practical(raw),
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
    return {"id": str(raw.get("id") or ""), "title": _title(raw),
            "start": start, "end": end, **practical(raw)}


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
