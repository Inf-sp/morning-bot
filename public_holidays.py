"""Официальные праздники страны (Nager.Date, без ключа) для строки «Моего дня»."""
import logging
from datetime import date, timedelta

import requests

import config
import store
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


# Коротко, что это за день (Нидерланды) — для недельной рассылки «Праздники и мероприятия».
_ABOUT = {
    "Nieuwjaarsdag": "выходной, вечером накануне — фейерверки",
    "Goede Vrijdag": "для большинства рабочий день",
    "Eerste Paasdag": "семейный завтрак и поиск яиц",
    "Tweede Paasdag": "выходной, мебельные магазины и ярмарки",
    "Koningsdag": "всё в оранжевом, блошиные рынки на улицах и вечеринки",
    "Bevrijdingsdag": "фестивали свободы по всей стране",
    "Hemelvaartsdag": "выходной, многие берут длинные выходные",
    "Eerste Pinksterdag": "выходной, магазины часто закрыты",
    "Tweede Pinksterdag": "выходной, фестивали и ярмарки",
    "Eerste Kerstdag": "семейный ужин, магазины закрыты",
    "Tweede Kerstdag": "выходной, прогулки и мебельные распродажи",
}


def _nth_weekday(year, month, weekday, nth):
    """nth-й день недели месяца (weekday: пн=0)."""
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))


def _nl_traditions(year) -> list:
    """Традиционные дни Нидерландов, не выходные: дата по правилу, без сети."""
    return [
        (date(year, 2, 14), "Valentijnsdag", "День святого Валентина"),
        (date(year, 5, 4), "Dodenherdenking", "в 20:00 две минуты молчания в память о погибших"),
        (_nth_weekday(year, 5, 6, 2), "Moederdag", "День матери"),
        (_nth_weekday(year, 6, 6, 3), "Vaderdag", "День отца"),
        (_nth_weekday(year, 9, 1, 3), "Prinsjesdag", "король открывает парламентский год, карета в Гааге"),
        (date(year, 11, 11), "Sint-Maarten", "дети с фонариками ходят по домам, поют и получают сладости"),
        (date(year, 12, 5), "Sinterklaasavond", "вечер подарков и шуточных стихов"),
        (date(year, 12, 31), "Oudejaarsavond", "oliebollen и фейерверки в полночь"),
    ]


def week_holidays(cc, start: date, end: date) -> list:
    """Праздники в [start, end]: [{date, name, about}] — государственные (Nager.Date; название русское, если есть) и
    для Нидерландов традиционные дни (Sint-Maarten, Sinterklaasavond…). Сбой источника — только традиции."""
    cc = str(cc or "").upper()
    found = {}
    for holiday in holidays_between(cc, start, end):
        local = holiday["local"] or holiday["name"]
        found[(holiday["date"], local)] = {"date": holiday["date"], "name": _label(holiday),
                                           "about": _ABOUT.get(local, "") if cc == "NL" else ""}
    if cc == "NL":
        for year in range(start.year, end.year + 1):
            for day, name, about in _nl_traditions(year):
                if start <= day <= end:
                    found.setdefault((day.isoformat(), name), {"date": day.isoformat(), "name": name, "about": about})
    return [found[key] for key in sorted(found)]


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
        # Список праздников на год не меняется: прежний сохранённый ответ — надёжный резерв.
        return list((store._load(config.PUBLIC_HOLIDAYS_KEY) or {}).get(key) or [])
    holidays = [
        {"date": str(row.get("date") or ""), "local": str(row.get("localName") or "").strip(),
         "name": str(row.get("name") or "").strip()}
        for row in rows if isinstance(row, dict) and row.get("date")
    ]
    util.ttl_set("nager_holidays", key, holidays)
    if holidays:
        def mutate(data):
            data = data if isinstance(data, dict) else {}
            data[key] = holidays
            return data, None
        store.mutate_kv(config.PUBLIC_HOLIDAYS_KEY, mutate)
    return holidays


def _label(holiday):
    local, english = holiday["local"], holiday["name"]
    russian = _RU_NAMES.get(local) or english
    return f"{local} — {russian}" if local and russian and russian != local else local or russian


def holidays_between(cc, start: date, end: date) -> list:
    """Государственные праздники страны в [start, end] по дате; сбой источника — []."""
    cc = str(cc or "").upper()
    if len(cc) != 2 or end < start:
        return []
    found = {}
    for year in range(start.year, end.year + 1):
        for holiday in _year_holidays(year, cc):
            if start.isoformat() <= holiday["date"] <= end.isoformat():
                found.setdefault((holiday["date"], holiday["local"] or holiday["name"]), holiday)
    return [found[key] for key in sorted(found)]


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
