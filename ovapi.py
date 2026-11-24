"""OVapi (v0.ovapi.nl, открытые данные NDOV, без ключа) — автобусы у остановки в реальном времени.

Только лёгкие запросы: список остановок города — раз в месяц (кэш), отправления остановки — по запросу (~ раз в
10 минут вместе с проверкой NS). Полный GTFS-Realtime всей страны не скачиваем: десятки МБ в минуту не для VM.
"""
import logging
import time
from datetime import datetime

import requests

import api_usage
import config
import store

_log = logging.getLogger(__name__)

_BASE = "https://v0.ovapi.nl"
_TIMEOUT = 10
_HEADERS = {"User-Agent": "morning-bot/1.0 (personal Telegram bot)"}
_STOPS_TTL = 30 * 86400
_STOPS_VERSION = 1


def _get(path):
    """JSON OVapi; сбой — None (состояние вызывающий не трогает)."""
    started = time.monotonic()
    try:
        response = requests.get(f"{_BASE}{path}", headers=_HEADERS, timeout=_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        api_usage.record_request("ovapi", ok=False, error=type(error).__name__, monitor_result=False)
        _log.info("ovapi unavailable path=%s error=%s", path.split("/")[1:2], type(error).__name__)
        return None
    api_usage.record_request("ovapi", ok=True, latency_ms=int((time.monotonic() - started) * 1000),
                             monitor_result=False)
    return data


def town_stop_areas(town) -> dict:
    """Остановки города: {код: название} из /stopareacode (вся страна, раз в месяц, в кэш — только город)."""
    town_key = " ".join(str(town or "").split()).casefold()
    if not town_key:
        return {}
    saved = (store._load(config.OVAPI_STOPS_KEY) or {}).get(town_key) or {}
    if saved.get("v") == _STOPS_VERSION and time.time() - float(saved.get("ts") or 0) < _STOPS_TTL:
        return dict(saved.get("stops") or {})
    data = _get("/stopareacode")
    if not isinstance(data, dict):
        return dict(saved.get("stops") or {})  # сбой — прежний список
    stops = {code: str(row.get("TimingPointName") or "") for code, row in data.items()
             if isinstance(row, dict) and str(row.get("TimingPointTown") or "").casefold() == town_key}

    def mutate(value):
        value = value if isinstance(value, dict) else {}
        value[town_key] = {"v": _STOPS_VERSION, "ts": time.time(), "stops": stops}
        return value, None

    store.mutate_kv(config.OVAPI_STOPS_KEY, mutate)
    return stops


def station_codes(town, name=None) -> list:
    """Коды остановок у вокзала города (по умолчанию — со словом «Station» в названии, `config.BUS_STOP_NAME`)."""
    wanted = str(name or config.BUS_STOP_NAME).casefold()
    return sorted(code for code, title in town_stop_areas(town).items() if wanted in title.casefold())


def _moment(value):
    try:
        moment = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=config.TZ)


def departures(codes):
    """Ближайшие рейсы и сообщения перевозчиков на остановках: {"passes": [...], "messages": [...]}; сбой — None.
    Рейс: {id, line, destination, planned, expected, delay_min, status, stop}; сообщение: {id, text, stop}."""
    if not codes:
        return {"passes": [], "messages": []}
    data = _get(f"/stopareacode/{','.join(codes)}")
    if not isinstance(data, dict):
        return None
    passes, messages = {}, {}
    for area in data.values():
        for timing_point in (area or {}).values() if isinstance(area, dict) else ():
            if not isinstance(timing_point, dict):
                continue
            stop = str((timing_point.get("Stop") or {}).get("TimingPointName") or "")
            for key, row in (timing_point.get("Passes") or {}).items():
                if not isinstance(row, dict) or str(row.get("TransportType") or "BUS").upper() not in ("BUS", "TRAM"):
                    continue
                planned, expected = _moment(row.get("TargetDepartureTime")), _moment(row.get("ExpectedDepartureTime"))
                if not planned:
                    continue
                delay = round(((expected or planned) - planned).total_seconds() / 60)
                passes[key] = {"id": key, "line": str(row.get("LinePublicNumber") or ""),
                               "destination": str(row.get("DestinationName50") or ""), "planned": planned,
                               "expected": expected or planned, "delay_min": delay,
                               "status": str(row.get("TripStopStatus") or "").upper(), "stop": stop}
            for key, row in (timing_point.get("GeneralMessages") or {}).items():
                text = " ".join(str((row or {}).get("MessageContent") or "").split()) if isinstance(row, dict) else ""
                if text:
                    messages[key] = {"id": key, "text": text, "stop": stop}
    ordered = sorted(passes.values(), key=lambda item: item["expected"])
    return {"passes": ordered, "messages": list(messages.values())}


def next_buses(town, now=None, minutes=40, limit=5):
    """Ближайшие автобусы от вокзала (для сбоя NS): [{line, destination, expected, delay_min}]; нет данных — []."""
    data = departures(station_codes(town))
    if not data:
        return []
    now = now or datetime.now(config.TZ)
    out = []
    for item in data["passes"]:
        wait = (item["expected"] - now).total_seconds() / 60
        if item["status"] != "CANCEL" and 0 <= wait <= minutes and item["line"]:
            out.append(item)
    return out[:limit]
