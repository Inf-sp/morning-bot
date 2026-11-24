"""CBS (opendata.cbs.nl, OData, без ключа) — город в цифрах для «Моего дня»: население, жильё, машины, компании.

Таблица «Regionale kerncijfers Nederland» (70072ned): код муниципалитета — по названию города (RegioS), показатели —
по названию (DataProperties: ключи вроде TotaleBevolking_1 меняются между версиями таблицы), год — последний, где
показатель заполнен. Кэш на месяц; сводка дня читает только кэш.
"""
import logging
import re
import time
from urllib.parse import quote

import requests

import api_usage
import config
import store

_log = logging.getLogger(__name__)

_BASE = "https://opendata.cbs.nl/ODataApi/odata/70072ned"
_TIMEOUT = 25
_TTL = 30 * 86400
_VERSION = 1
_HEADERS = {"User-Agent": "morning-bot/1.0 (personal Telegram bot)"}

# (название показателя в CBS, шаблон факта): {v} — число, {year} — год, {city} — город; доля — от населения.
_FACTS = (
    ("Totale bevolking", "В {city} живёт {v} человек — данные CBS за {year} год."),
    ("Bevolkingsdichtheid", "Плотность населения {city} — {v} человек на км²."),
    ("Levend geboren kinderen", "За {year} год в {city} родилось {v} детей."),
    ("Woningvoorraad", "В {city} {v} домов и квартир."),
    ("Gemiddelde WOZ-waarde van woningen", "Средняя оценочная стоимость жилья в {city} — {v} тыс. €."),
    ("Personenauto's", "У жителей {city} {v} легковых машин."),
    ("Bedrijfsvestigingen", "В {city} {v} компаний и предприятий."),
)
_SHARES = (
    (("65 tot 80 jaar", "80 jaar of ouder"), "{p}% жителей {city} старше 65 лет."),
    (("0 tot 15 jaar",), "{p}% жителей {city} — дети до 15 лет."),
    (("25 tot 45 jaar",), "{p}% жителей {city} — от 25 до 45 лет."),
    (("Gehuwd",), "{p}% жителей {city} состоят в браке."),
)


def _get(path):
    started = time.monotonic()
    try:
        response = requests.get(f"{_BASE}{path}", headers=_HEADERS, timeout=_TIMEOUT)
        response.raise_for_status()
        rows = response.json().get("value")
    except (requests.RequestException, ValueError) as error:
        api_usage.record_request("cbs", ok=False, error=type(error).__name__, monitor_result=False)
        _log.info("cbs unavailable: %s", type(error).__name__)
        return None
    api_usage.record_request("cbs", ok=True, latency_ms=int((time.monotonic() - started) * 1000),
                             monitor_result=False)
    return rows if isinstance(rows, list) else None


def _number(value) -> str:
    value = float(value)
    return f"{round(value):,}".replace(",", " ") if value >= 100 else f"{value:g}".replace(".", ",")


def _year(period) -> str:
    match = re.match(r"(\d{4})", str(period or ""))
    return match.group(1) if match else ""


def build(city) -> list:
    """Факты о городе по-русски из последних заполненных данных; сбой — None, нет города в CBS — []."""
    name = " ".join(str(city or "").split())
    regions = _get(f"/RegioS?$filter=Title%20eq%20'{quote(name)}'&$select=Key,Title")
    if regions is None:
        return None
    code = next((str(row.get("Key") or "").strip() for row in regions if str(row.get("Key") or "").startswith("GM")), "")
    if not code:
        return []
    props = _get("/DataProperties?$select=Key,Title")
    rows = _get(f"/TypedDataSet?$filter=substringof('{code}',RegioS)")
    if props is None or rows is None:
        return None
    keys = {str(prop.get("Title") or "").strip().casefold(): str(prop.get("Key") or "")
            for prop in props if prop.get("Key")}
    rows = sorted(rows, key=lambda row: str(row.get("Perioden") or ""), reverse=True)

    def latest(title):
        key = keys.get(title.casefold())
        for row in rows:
            if key and row.get(key) is not None:
                return float(row[key]), _year(row.get("Perioden"))
        return None, ""

    facts = []
    for title, template in _FACTS:
        value, year = latest(title)
        if value:
            facts.append(template.format(city=name, v=_number(value), year=year))
    population, _year_total = latest("Totale bevolking")
    for titles, template in _SHARES:
        parts = [latest(title)[0] for title in titles]
        if population and all(parts):
            facts.append(template.format(city=name, p=round(sum(parts) / population * 100)))
    return facts


def facts(city, fetch=True) -> list:
    """Факты о городе из кэша (месяц); fetch=False — без сети."""
    key = " ".join(str(city or "").split()).casefold()
    if not key:
        return []
    saved = (store._load(config.CBS_FACTS_KEY) or {}).get(key) or {}
    if (saved.get("v") == _VERSION and time.time() - float(saved.get("ts") or 0) < _TTL) or not fetch:
        return list(saved.get("facts") or [])
    found = build(city)
    if found is None:
        return list(saved.get("facts") or [])  # сбой — прежние факты

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[key] = {"v": _VERSION, "ts": time.time(), "facts": found}
        return data, None

    store.mutate_kv(config.CBS_FACTS_KEY, mutate)
    return found


def fact_of_day(city, day) -> str:
    """Факт дня о городе — по кругу по датам, без сети; фактов нет — ""."""
    found = facts(city, fetch=False)
    return found[day.toordinal() % len(found)] if found else ""
