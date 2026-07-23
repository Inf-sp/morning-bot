"""Память пользователя («бот учится на тебе»): предпочтения.

Тонкий доменный слой поверх профиля в store (config.PROFILE_KEY). Без LLM и сети.
Профиль - dict на пользователя: {"prefs": [...]}.
"""
import store
import config


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


def get_lagom(cid) -> list:
    """Return saved personal principles, including legacy profiles."""
    profile = store.get_profile(cid) or {}
    values = profile.get("lagom") or profile.get("principles")
    if values is not None:
        return list(values) if isinstance(values, (list, tuple)) else [str(values)]
    try:
        legacy = store._load(config.LEGACY_LAGOM_KEY) or {}
        values = legacy.get(str(cid), []) if isinstance(legacy, dict) else []
        return list(values) if isinstance(values, (list, tuple)) else []
    except Exception:
        return []
