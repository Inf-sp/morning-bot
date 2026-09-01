"""Общие чистые правила ротации персональных рекомендаций.

Модуль не читает store и не знает о Telegram: разделы сохраняют существующие поля
профиля, но одинаково нормализуют историю, исключают повторы и начинают новый цикл.
"""

from collections.abc import Callable, Iterable


def identity(value) -> str:
    """Стабильное текстовое сравнение для сущностей без внешнего ID."""
    return " ".join(str(value or "").casefold().split()).strip()


def recent(values: Iterable, *, limit=50, key: Callable = identity) -> list:
    """Уникальная история в исходном виде; сохраняется первое написание."""
    result = []
    seen = set()
    for value in values or []:
        marker = key(value)
        if marker and marker not in seen:
            seen.add(marker)
            result.append(value)
    return result[-max(1, int(limit)):]


def remember(values: Iterable, value, *, limit=50, key: Callable = identity) -> list:
    """Перемещает показанный результат в конец ограниченной истории."""
    marker = key(value)
    kept = [item for item in recent(values, limit=limit, key=key) if key(item) != marker]
    return ([*kept, value] if marker else kept)[-max(1, int(limit)):]


def markers(values: Iterable, *, key: Callable = identity) -> set[str]:
    return {marker for value in (values or []) if (marker := key(value))}


def candidates_for_cycle(candidates: Iterable, history: Iterable, *, current=None,
                         key: Callable = identity) -> list:
    """Свежие кандидаты; после полного круга — все, кроме текущего."""
    pool = list(candidates or [])
    used = markers(history, key=key)
    fresh = [item for item in pool if key(item) not in used]
    if fresh:
        return fresh
    current_marker = key(current)
    without_current = [item for item in pool if not current_marker or key(item) != current_marker]
    return without_current or pool


def search_exclusions(values: Iterable, *, limit=10, key: Callable = identity) -> str:
    """Безопасная строка отрицательных точных фраз для внешнего поиска."""
    names = recent(values, limit=limit, key=key)
    return " ".join(
        f'-"{str(value).replace(chr(34), "").strip()}"'
        for value in names if str(value).strip()
    )


def cache_history(values: Iterable, *, limit=50, key: Callable = identity) -> list[str]:
    """Нормализованная история для ключа AI-кэша."""
    return [key(value) for value in recent(values, limit=limit, key=key) if key(value)]
