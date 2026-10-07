"""Редакционный месячный пул ребусов (data/monthly_rebuses.json): без сети и AI."""

import calendar
import hashlib
import html
import json
import re
from pathlib import Path


_CATALOG_PATH = Path(__file__).with_name("data") / "monthly_rebuses.json"
_EDITORIAL_CATALOG = None


def _load_editorial_catalog():
    global _EDITORIAL_CATALOG
    if _EDITORIAL_CATALOG is None:
        try:
            payload = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        _EDITORIAL_CATALOG = payload if isinstance(payload, dict) else {}
    return _EDITORIAL_CATALOG


def local_pool(category):
    """Проверенный локальный запас карточек категории."""
    values = _load_editorial_catalog().get(category) or []
    return tuple(dict(item) for item in values if isinstance(item, dict))


def _clean_generated_text(value):
    text = html.unescape(str(value or ""))
    text = text.replace("\ufffd", "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_generated_fact(value):
    text = _clean_generated_text(value)
    return re.sub(r"^(?:[^\wА-Яа-яЁё]+)?(?:интересно|факт)\s*:\s*", "", text, count=1, flags=re.I).strip()


def _month_key(day):
    return day.strftime("%Y-%m")


def _valid_items(values, required):
    items, answers = [], set()
    for value in values or []:
        if not isinstance(value, dict):
            continue
        emoji = _clean_generated_text(value.get("emoji"))
        answer = _clean_generated_text(value.get("answer"))
        fact = _clean_generated_fact(value.get("fact"))
        key = answer.casefold()
        if not emoji or not answer or key in answers or (fact and key in fact.casefold()):
            continue
        answers.add(key)
        items.append({"emoji": emoji, "answer": answer, **({"fact": fact} if fact else {})})
    return items[:required] if len(items) >= required else []


def _editorial_month_items(category, day, fallback):
    required = calendar.monthrange(day.year, day.month)[1]
    source = list(fallback or [])
    items = _valid_items(source, len(source))
    if len(items) < required:
        return []
    seed = f"{category}:{_month_key(day)}".encode("utf-8")
    shift = int(hashlib.sha256(seed).hexdigest()[:8], 16) % len(items)
    rotated = items[shift:] + items[:shift]
    return rotated[:required]


def cached_for_day(category, day, fallback=()):
    """Ребус дня из редакционного пула месяца; без пула — по кругу из ``fallback``."""
    editorial = _editorial_month_items(category, day, fallback)
    if editorial:
        return dict(editorial[day.day - 1])
    seeds = [dict(item) for item in fallback or [] if isinstance(item, dict)]
    return dict(seeds[(day.timetuple().tm_yday - 1) % len(seeds)]) if seeds else {}
