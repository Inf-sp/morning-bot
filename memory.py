"""Память пользователя («бот учится на тебе»): Лагом, предпочтения.

Тонкий доменный слой поверх профиля в store (config.PROFILE_KEY). Без LLM и сети.
Профиль - dict на пользователя: {"lagom": [...], "prefs": [...]}.
"""
import re
import config
import store


# ---------- Лагом (ценности/установки пользователя) ----------
def get_lagom(cid) -> list:
    """Список Лагом-принципов пользователя. Для нового — пусто."""
    prof = store.get_profile(cid)
    if "lagom" not in prof:
        # Миграция из старого ключа (get_list сам обработает flat-list для CHAT_ID)
        old = store.get_list(config.LAGOM_KEY, cid)
        prof["lagom"] = list(old)
        store.set_profile(cid, prof)
    return prof.get("lagom", [])


def set_lagom(cid, items: list):
    prof = store.get_profile(cid)
    prof["lagom"] = list(items)
    store.set_profile(cid, prof)


def add_lagom(cid, item: str):
    item = (item or "").strip()
    if not item:
        return
    items = get_lagom(cid)
    items.append(item)
    set_lagom(cid, items)


def _split_items(text: str) -> list:
    """Разбивает ввод на отдельные принципы: сначала по строкам, затем по предложениям."""
    lines = [re.sub(r'^[\s*\-•·→\d]+[\.\)\:]?\s*', '', l).strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    if not lines:
        return []
    # Одна строка без переносов → разбить по предложениям
    if len(lines) == 1:
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', lines[0]) if s.strip()]
        return sentences if len(sentences) > 1 else lines
    return lines


def seed_owner_lagom() -> bool:
    """Разово: вливает принципы из lagom.json в профиль владельца (CHAT_ID).
    Маркер в store не даёт повторить — удалённые принципы не возвращаются."""
    if not config.CHAT_ID:
        return False
    marker = f"lagom_{config.CHAT_ID}"
    flags = store._load("_seed_flags") or {}
    if flags.get(marker):
        return False
    disk = config._LAGOM_ITEMS or []
    cur = get_lagom(config.CHAT_ID)
    seen = {str(x).strip().lower() for x in cur}
    merged = list(cur) + [it for it in disk
                          if isinstance(it, str) and it.strip()
                          and it.strip().lower() not in seen]
    set_lagom(config.CHAT_ID, merged)
    flags[marker] = True
    store._save("_seed_flags", flags)
    return True


def add_lagom_batch(cid, text: str) -> list:
    """Парсит текст, добавляет каждый принцип отдельно. Возвращает список добавленных."""
    parts = _split_items(text)
    existing = set(get_lagom(cid))
    added = []
    for it in parts:
        if it and it not in existing:
            add_lagom(cid, it)
            existing.add(it)
            added.append(it)
    return added


# ---------- Предпочтения пользователя (Memory Agent) ----------
def get_preferences(cid) -> list:
    """Список сохранённых фактов о пользователе."""
    return store.get_profile(cid).get("prefs", [])


def profile_hints(cid) -> str:
    """Компактная строка предпочтений для подмешивания в LLM-промпты. '' если пусто."""
    prefs = get_preferences(cid)
    if not prefs:
        return ""
    return "Знаешь о пользователе: " + "; ".join(prefs[:20]) + "."
