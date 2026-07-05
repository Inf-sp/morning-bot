"""Статусы онбординга разделов.

Единый источник истины: пропуск опроса больше не равен «настройка завершена».
Статус каждого раздела хранится в профиле пользователя в общем словаре
``prof["onboarding"] = {section: status}``.

Значения статуса:
- ``not_started``    — опрос ещё не показывался;
- ``skipped``        — пользователь видел опрос и нажал «Пропустить»;
- ``completed``      — пользователь прошёл опрос и данные сохранены;
- ``auto_configured``— в разделе уже были данные, опрос не требовался.
"""
import store

# Каноничные статусы
NOT_STARTED = "not_started"
SKIPPED = "skipped"
COMPLETED = "completed"
AUTO_CONFIGURED = "auto_configured"

_VALID = frozenset({NOT_STARTED, SKIPPED, COMPLETED, AUTO_CONFIGURED})

# Разделы, участвующие в онбординге. `balance` исторически объединял
# «Здоровье» и «Готовку» — теперь это два независимых раздела: health и cooking.
SECTIONS = ("wardrobe", "learning", "leisure", "health", "cooking")

# Статусы, при которых опрос считается «уже пройден» и не показывается автоматически
# при первом входе, но остаётся доступен через «Настроить раздел заново».
_SETTLED = frozenset({SKIPPED, COMPLETED, AUTO_CONFIGURED})

# Легаси-поля булевого онбординга (firstvisit v1) -> новый раздел(ы).
# Старый `balance` покрывал и здоровье, и готовку, поэтому мигрирует в оба.
_LEGACY_FLAG_MAP = {
    "_fv_wardrobe": ("wardrobe",),
    "_fv_learn": ("learning",),
    "_fv_leisure": ("leisure",),
    "_fv_balance": ("health", "cooking"),
}


def _migrate_legacy(prof: dict) -> dict:
    """Лениво переносит булевы ``_fv_*`` в ``prof["onboarding"]``.

    Возвращает (возможно новый) словарь onboarding. Не сохраняет профиль сам —
    это делает вызывающая функция при необходимости.
    """
    ob = dict(prof.get("onboarding") or {})
    for flag, sections in _LEGACY_FLAG_MAP.items():
        if prof.get(flag):
            for section in sections:
                ob.setdefault(section, COMPLETED)
    return ob


def get(cid, section: str) -> str:
    """Текущий статус раздела. Учитывает ленивую миграцию легаси-полей."""
    prof = store.get_profile(cid)
    ob = _migrate_legacy(prof)
    return ob.get(section, NOT_STARTED)


def set_status(cid, section: str, status: str) -> None:
    """Проставляет статус раздела, попутно закрепляя миграцию легаси-полей."""
    if status not in _VALID:
        raise ValueError(f"unknown onboarding status: {status!r}")
    prof = store.get_profile(cid)
    ob = _migrate_legacy(prof)
    ob[section] = status
    prof["onboarding"] = ob
    store.set_profile(cid, prof)


def is_settled(cid, section: str) -> bool:
    """True, если опрос уже пройден/пропущен/раздел заполнен автоматически."""
    return get(cid, section) in _SETTLED


def is_skipped(cid, section: str) -> bool:
    """True, если раздел именно пропущен (для заметного баннера-предложения)."""
    return get(cid, section) == SKIPPED
