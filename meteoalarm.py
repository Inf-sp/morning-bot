"""Официальные погодные предупреждения KNMI через MeteoAlarm (feeds.meteoalarm.org, CAP в JSON, без ключа).

Предупреждение относится к пользователю, только если его координаты внутри полигона зоны. Уровень и вид — из
параметров CAP (awareness_level «2; yellow; Moderate», awareness_type «1; Wind»), по-русски без AI.
"""
import logging
import time
from datetime import datetime

import requests

import api_usage
import config
import util

_log = logging.getLogger(__name__)

_URL = "https://feeds.meteoalarm.org/api/v1/warnings/feeds-{country}"
_COUNTRIES = {"NL": "netherlands"}
_TTL = 15 * 60  # предупреждения обновляются нечасто: 15 минут — в памяти
_TIMEOUT = 20
_HEADERS = {"User-Agent": "morning-bot/1.0 (personal Telegram bot)"}

LEVELS = {2: ("жёлтый", "🟡"), 3: ("оранжевый", "🟠"), 4: ("красный", "🔴")}
TYPES = {1: ("ветер", "💨"), 2: ("снег и гололёд", "❄️"), 3: ("гроза", "⛈️"), 4: ("туман", "🌫️"),
         5: ("жара", "🌡️"), 6: ("мороз", "❄️"), 7: ("шторм на побережье", "🌊"), 8: ("лесные пожары", "🔥"),
         10: ("сильный дождь", "🌧️"), 12: ("наводнение", "🌊"), 13: ("дождь и паводок", "🌧️")}


def _feed(cc) -> list | None:
    country = _COUNTRIES.get(str(cc or "").upper())
    if not country:
        return []
    cached = util.ttl_get("meteoalarm", country, _TTL)
    if cached is not None:
        return cached
    started = time.monotonic()
    try:
        response = requests.get(_URL.format(country=country), headers=_HEADERS, timeout=_TIMEOUT)
        response.raise_for_status()
        rows = response.json().get("warnings") or []
    except (requests.RequestException, ValueError) as error:
        api_usage.record_request("meteoalarm", ok=False, error=type(error).__name__, monitor_result=False)
        _log.info("meteoalarm unavailable: %s", type(error).__name__)
        return None
    api_usage.record_request("meteoalarm", ok=True, latency_ms=int((time.monotonic() - started) * 1000),
                             monitor_result=False)
    return util.ttl_set("meteoalarm", country, rows)


def _inside(lat, lon, polygon) -> bool:
    """Точка внутри полигона «lat,lon lat,lon …» (луч по долготе)."""
    try:
        points = [tuple(map(float, pair.split(","))) for pair in str(polygon or "").split()]
    except ValueError:
        return False
    inside = False
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:] + points[:1]):
        if (lon1 > lon) != (lon2 > lon) and lat < (lat2 - lat1) * (lon - lon1) / (lon2 - lon1 or 1e-12) + lat1:
            inside = not inside
    return inside


def _number(value) -> int:
    try:
        return int(str(value or "").split(";")[0].strip())
    except ValueError:
        return 0


def _moment(value):
    try:
        moment = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(config.TZ)


def warnings_for(lat, lon, cc="NL", now=None) -> list | None:
    """Действующие и будущие официальные предупреждения для точки: [{level, level_ru, type_ru, emoji, onset,
    expires}], самые серьёзные первыми; сервис не ответил — None."""
    rows = _feed(cc)
    if rows is None:
        return None
    now = now or datetime.now(config.TZ)
    found, seen = [], set()
    for row in rows:
        for info in (row.get("alert") or {}).get("info") or []:
            if not isinstance(info, dict) or not str(info.get("language") or "").lower().startswith("en"):
                continue
            params = {str(p.get("valueName")): p.get("value") for p in info.get("parameter") or [] if isinstance(p, dict)}
            level, kind = _number(params.get("awareness_level")), _number(params.get("awareness_type"))
            expires = _moment(info.get("expires"))
            if level < 2 or (expires and expires < now):
                continue
            if not any(_inside(lat, lon, polygon) for area in info.get("area") or []
                       for polygon in (area.get("polygon") or []) if isinstance(area, dict)):
                continue
            onset = _moment(info.get("onset") or info.get("effective"))
            key = (level, kind, onset)
            if key in seen:
                continue
            seen.add(key)
            level_ru, level_emoji = LEVELS.get(level, LEVELS[2])
            type_ru, type_emoji = TYPES.get(kind, ("непогода", "⚠️"))
            found.append({"level": level, "level_ru": level_ru, "level_emoji": level_emoji, "type_ru": type_ru,
                          "emoji": type_emoji, "onset": onset, "expires": expires})
    return sorted(found, key=lambda item: (-item["level"], item["onset"] or now))


def line(item, now=None) -> str:
    """«Код оранжевый KNMI — ветер, с 14:00 до 21:00» (завтра — «завтра с …»)."""
    now = now or datetime.now(config.TZ)

    def clock(moment):
        if not moment:
            return ""
        day = "" if moment.date() == now.date() else "завтра " if (moment.date() - now.date()).days == 1 \
            else f"{moment.day:02d}.{moment.month:02d} "
        return f"{day}{moment:%H:%M}"

    span = " до ".join(part for part in (clock(item["onset"]) if item["onset"] and item["onset"] > now else "",
                                         clock(item["expires"])) if part)
    if span and not (item["onset"] and item["onset"] > now):
        span = f"до {span}"
    elif span:
        span = f"с {span}"
    return f"Код {item['level_ru']} KNMI — {item['type_ru']}" + (f", {span}" if span else "")
