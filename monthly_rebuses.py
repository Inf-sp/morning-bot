"""Месячные пулы ребусов для основных категорий."""

import asyncio
import calendar
from datetime import datetime

import ai
import config
import store


_LOCKS = {}
_LABELS = {
    "movies": "известные фильмы и сериалы",
    "books": "известные книги",
    "music": "известные песни, альбомы и исполнители",
    "games": "известные видеоигры и настольные игры",
    "travel": "известные города и страны",
}


def _month_key(day):
    return day.strftime("%Y-%m")


def _valid_items(values, required):
    items, answers = [], set()
    for value in values or []:
        if not isinstance(value, dict):
            continue
        emoji = " ".join(str(value.get("emoji") or "").split()).strip()
        answer = " ".join(str(value.get("answer") or "").split()).strip()
        fact = " ".join(str(value.get("fact") or "").split()).strip()
        key = answer.casefold()
        if not emoji or not answer or key in answers or (fact and key in fact.casefold()):
            continue
        answers.add(key)
        items.append({"emoji": emoji, "answer": answer, **({"fact": fact} if fact else {})})
    return items[:required] if len(items) >= required else []


def cached_for_day(category, day, fallback=()):
    data = store._load(config.MONTHLY_REBUSES_CACHE_KEY)
    entry = (data or {}).get(category) if isinstance(data, dict) else None
    required = calendar.monthrange(day.year, day.month)[1]
    items = _valid_items((entry or {}).get("items"), required) if (
        isinstance(entry, dict) and entry.get("month") == _month_key(day)
    ) else []
    if items:
        return dict(items[day.day - 1])
    seeds = [dict(item) for item in fallback or [] if isinstance(item, dict)]
    return dict(seeds[(day.timetuple().tm_yday - 1) % len(seeds)]) if seeds else {}


async def for_day(category, day, fallback=(), *, refresh=False):
    if not refresh:
        cached = cached_for_day(category, day)
        if cached:
            return cached
    lock = _LOCKS.setdefault(category, asyncio.Lock())
    async with lock:
        if not refresh:
            cached = cached_for_day(category, day)
            if cached:
                return cached
        required = calendar.monthrange(day.year, day.month)[1]
        data = store._load(config.MONTHLY_REBUSES_CACHE_KEY)
        current_entry = (data or {}).get(category) if isinstance(data, dict) else None
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
            def change(current):
                current = current if isinstance(current, dict) else {}
                current[category] = {"month": _month_key(day), "items": items, "attempted": True}
                return current, None
            store.mutate_kv(config.MONTHLY_REBUSES_CACHE_KEY, change)
            return dict(items[day.day - 1])
        def mark_attempted(current):
            current = current if isinstance(current, dict) else {}
            current[category] = {"month": _month_key(day), "items": [], "attempted": True}
            return current, None
        store.mutate_kv(config.MONTHLY_REBUSES_CACHE_KEY, mark_attempted)
        return cached_for_day(category, day, fallback)
