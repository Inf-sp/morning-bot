"""Единый внутренний стоп-лист для персональных рекомендаций."""

import re

import config
import store


CATEGORY = "Не рекомендовать"

_LEGACY_SOURCES = (
    (config.MOVIE_BLACKLIST_KEY, "movie", "hidden"),
    (config.MOVIE_SEEN_KEY, "movie", "seen"),
    (config.BOOK_BLACKLIST_KEY, "book", "hidden"),
    (config.BOOK_SEEN_KEY, "book", "seen"),
    (config.MUSIC_DISLIKE_KEY, "artist", "hidden"),
    (config.MUSIC_SEEN_KEY, "artist", "seen"),
)


def _text(value, kind: str | None = None) -> str:
    if isinstance(value, dict):
        for key in ("name", "title", "value", "text"):
            if value.get(key):
                value = value[key]
                break
        else:
            return ""
    text = str(value or "").strip()
    if "\n" in text:
        text = next((line.strip() for line in text.splitlines() if line.strip()), text)
    text = re.sub(r"<[^>]+>", "", text).strip()
    text = re.sub(r"^[^\wа-яё«]+", "", text, flags=re.IGNORECASE).strip()
    if kind == "book":
        quoted = re.findall(r"«([^»]+)»", text)
        if quoted:
            text = quoted[-1].strip()
    return text


def _identity(kind: str, value) -> tuple[str, str]:
    kind = str(kind or "other").strip().lower()
    return kind, _text(value, kind).casefold()


def _normalized_entry(kind: str, value, reason: str) -> dict | None:
    kind = str(kind or "other").strip().lower()
    text = _text(value, kind)
    if not text:
        return None
    return {
        "category": CATEGORY,
        "type": kind,
        "value": text,
        "reason": str(reason or "removed").strip().lower(),
    }


def entries(cid) -> list[dict]:
    result = []
    seen = set()
    for item in store.get_list(config.RECOMMENDATION_STOPLIST_KEY, cid):
        if not isinstance(item, dict):
            continue
        entry = _normalized_entry(
            item.get("type", "other"),
            item.get("value", item.get("name", "")),
            item.get("reason", "removed"),
        )
        if not entry:
            continue
        identity = _identity(entry["type"], entry["value"])
        if identity not in seen:
            seen.add(identity)
            result.append({**item, **entry})
    return result


def add(cid, kind: str, value, reason: str = "removed") -> bool:
    entry = _normalized_entry(kind, value, reason)
    if not entry:
        return False
    current = entries(cid)
    identity = _identity(entry["type"], entry["value"])
    if any(_identity(item["type"], item["value"]) == identity for item in current):
        return False
    current.append(entry)
    store.set_list(config.RECOMMENDATION_STOPLIST_KEY, cid, current)
    return True


def remove(cid, kind: str, value) -> bool:
    identity = _identity(kind, value)
    current = entries(cid)
    kept = [item for item in current if _identity(item["type"], item["value"]) != identity]
    if len(kept) == len(current):
        return False
    store.set_list(config.RECOMMENDATION_STOPLIST_KEY, cid, kept)
    return True


def values(cid, kind: str) -> list[str]:
    """Значения стоп-листа плюс записи отдельных hidden/seen-списков."""
    kind = str(kind or "").strip().lower()
    result = [item["value"] for item in entries(cid) if item["type"] == kind]
    seen = {value.casefold() for value in result}
    for key, legacy_kind, _reason in _LEGACY_SOURCES:
        if legacy_kind != kind:
            continue
        for item in store.get_list(key, cid):
            text = _text(item, kind)
            if text and text.casefold() not in seen:
                seen.add(text.casefold())
                result.append(text)
    return result


