"""Месячные пулы ребусов для основных категорий."""

import asyncio
import calendar
import hashlib
import html
import json
import re
from datetime import datetime
from pathlib import Path

import ai
import config
import store


_LOCKS = {}
_CATALOG_PATH = Path(__file__).with_name("data") / "monthly_rebuses.json"
_EDITORIAL_CATALOG = None
_EDITORIAL_VERSION = 1
_LABELS = {
    "movies": "известные фильмы и сериалы",
    "books": "известные книги",
    "music": "известные песни, альбомы и исполнители",
    "games": "известные видеоигры и настольные игры",
    "travel": "известные города и страны",
}


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


def _save_month(category, day, items, source):
    def change(current):
        current = current if isinstance(current, dict) else {}
        current[category] = {
            "month": _month_key(day),
            "items": items,
            "attempted": True,
            "source": source,
            "version": _EDITORIAL_VERSION if source == "editorial" else 0,
        }
        return current, None

    store.mutate_kv(config.MONTHLY_REBUSES_CACHE_KEY, change)


def cached_for_day(category, day, fallback=()):
    data = store._load(config.MONTHLY_REBUSES_CACHE_KEY)
    entry = (data or {}).get(category) if isinstance(data, dict) else None
    required = calendar.monthrange(day.year, day.month)[1]
    editorial = _editorial_month_items(category, day, fallback)
    cache_allowed = not editorial or (
        isinstance(entry, dict)
        and entry.get("source") == "editorial"
        and entry.get("version") == _EDITORIAL_VERSION
    )
    items = _valid_items((entry or {}).get("items"), required) if (
        cache_allowed and isinstance(entry, dict) and entry.get("month") == _month_key(day)
    ) else []
    if items:
        return dict(items[day.day - 1])
    if editorial:
        return dict(editorial[day.day - 1])
    seeds = [dict(item) for item in fallback or [] if isinstance(item, dict)]
    return dict(seeds[(day.timetuple().tm_yday - 1) % len(seeds)]) if seeds else {}


async def for_day(category, day, fallback=(), *, refresh=False):
    editorial = _editorial_month_items(category, day, fallback)
    if not refresh and not editorial:
        cached = cached_for_day(category, day)
        if cached:
            return cached
    lock = _LOCKS.setdefault(category, asyncio.Lock())
    async with lock:
        if not refresh and not editorial:
            cached = cached_for_day(category, day)
            if cached:
                return cached
        required = calendar.monthrange(day.year, day.month)[1]
        data = store._load(config.MONTHLY_REBUSES_CACHE_KEY)
        current_entry = (data or {}).get(category) if isinstance(data, dict) else None
        if editorial:
            current_items = _valid_items((current_entry or {}).get("items"), required) if (
                isinstance(current_entry, dict)
                and current_entry.get("month") == _month_key(day)
                and current_entry.get("source") == "editorial"
                and current_entry.get("version") == _EDITORIAL_VERSION
            ) else []
            if current_items and not refresh:
                return dict(current_items[day.day - 1])
            _save_month(category, day, editorial, "editorial")
            return dict(editorial[day.day - 1])
        if (not refresh and isinstance(current_entry, dict)
                and current_entry.get("month") == _month_key(day)
                and current_entry.get("attempted")):
            return cached_for_day(category, day, fallback)
        previous_answers = []
        if isinstance(data, dict):
            for entry in data.values():
                for item in (entry or {}).get("items") or []:
                    if isinstance(item, dict) and item.get("answer"):
                        previous_answers.append(str(item["answer"]))
        prompt = f"""Составь {required} разных эмодзи-ребусов на каждый день месяца.
Категория: {_LABELS.get(category, category)}.
Ответы не должны повторяться. Не используй недавние ответы: {', '.join(previous_answers[-80:])}.
emoji: 2–4 понятных эмодзи. answer: короткий узнаваемый ответ.
fact: 1–2 коротких интересных предложения, не содержащих сам ответ.
Верни только JSON: {{"items":[{{"emoji":"","answer":"","fact":""}}]}}"""
        try:
            payload = await ai.allm_json(
                prompt, 4200, tier="leisure", module="monthly_rebuses",
            )
            items = _valid_items((payload or {}).get("items"), required)
        except Exception:
            items = []
        if items:
            _save_month(category, day, items, "generated")
            return dict(items[day.day - 1])
        def mark_attempted(current):
            current = current if isinstance(current, dict) else {}
            current[category] = {"month": _month_key(day), "items": [], "attempted": True}
            return current, None
        store.mutate_kv(config.MONTHLY_REBUSES_CACHE_KEY, mark_attempted)
        return cached_for_day(category, day, fallback)
