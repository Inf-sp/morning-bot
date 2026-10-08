"""Официальные праздники страны (Nager.Date, без ключа) для строки «Моего дня»."""
import logging
from datetime import date, timedelta

import requests

import util

_log = logging.getLogger(__name__)

_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{cc}"
_TTL_SECONDS = 7 * 24 * 3600
_TIMEOUT_SECONDS = 8
# Русские названия праздников Нидерландов; для остальных — английское название Nager.
_RU_NAMES = {
    "Nieuwjaarsdag": "Новый год",
    "Goede Vrijdag": "Страстная пятница",
    "Eerste Paasdag": "Пасха",
    "Tweede Paasdag": "Второй день Пасхи",
    "Koningsdag": "День короля",
    "Bevrijdingsdag": "День освобождения",
    "Hemelvaartsdag": "Вознесение",
    "Eerste Pinksterdag": "Троица",
    "Tweede Pinksterdag": "Второй день Троицы",
    "Eerste Kerstdag": "Рождество",
    "Tweede Kerstdag": "Второй день Рождества",
}


def _year_holidays(year, cc):
    key = f"{cc}|{year}"
    cached = util.ttl_get("nager_holidays", key, _TTL_SECONDS)
    if cached is not None:
        return cached
    try:
        response = requests.get(_URL.format(year=year, cc=cc), timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
        rows = response.json() or []
    except Exception as error:
        _log.warning("Nager.Date unavailable cc=%s year=%s: %r", cc, year, error)
        return []
    holidays = [
        {"date": str(row.get("date") or ""), "local": str(row.get("localName") or "").strip(),
         "name": str(row.get("name") or "").strip()}
        for row in rows if isinstance(row, dict) and row.get("date")
    ]
    util.ttl_set("nager_holidays", key, holidays)
    return holidays


def _label(holiday):
    local, english = holiday["local"], holiday["name"]
    russian = _RU_NAMES.get(local) or english
    return f"{local} — {russian}" if local and russian and russian != local else local or russian


def holiday_lines(cc, today: date):
    """«Сегодня: Koningsdag — День короля» и/или «Завтра: …»; нет праздника или сбой — []."""
    cc = str(cc or "").upper()
    if len(cc) != 2:
        return []
    tomorrow = today + timedelta(days=1)
    by_date = {}
    for year in sorted({today.year, tomorrow.year}):
        for holiday in _year_holidays(year, cc):
            by_date.setdefault(holiday["date"], holiday)
    lines = []
    for prefix, day in (("Сегодня", today), ("Завтра", tomorrow)):
        holiday = by_date.get(day.isoformat())
        if holiday:
            lines.append(f"{prefix}: {_label(holiday)}")
    return lines
