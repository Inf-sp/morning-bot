"""Память пользователя («бот учится на тебе»): предпочтения.

Тонкий доменный слой поверх профиля в store (config.PROFILE_KEY). Без LLM и сети.
Профиль - dict на пользователя: {"prefs": [...]}.
"""
import store


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
