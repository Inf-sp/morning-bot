"""Готовность дневных кэшей главных экранов и статистика их открытия.

Только чтение store: без сети и AI. Используется ночным повторным прогревом
(перестраивает лишь отсутствующие разделы), логом ``home_open`` и строкой
времени открытия разделов в админке.
"""
import logging
from datetime import datetime

import config

_log = logging.getLogger(__name__)

SECTION_BY_CALLBACK = {
    "m_myday": "myday", "m_wardrobe": "wardrobe", "m_food": "cooking",
    "m_learn": "learning", "m_leisure": "leisure",
}


def _wardrobe(cid):
    import wardrobe
    return wardrobe._get_cached_look(cid) is not None or not wardrobe.has_wardrobe_items(cid)


def _cooking(cid):
    import day_menu
    return day_menu.get_cached_day_menu(cid) is not None


def _leisure(cid):
    import leisure_hub
    return leisure_hub.is_ready(cid)


def _myday(cid):
    import myday
    cache = myday._load_day_cache(cid, datetime.now(config.TZ).strftime("%Y-%m-%d"))
    return cache is not None and not myday._cache_misses_ready_sections(cache, cid)


_CHECKS = {
    "wardrobe": _wardrobe, "cooking": _cooking, "learning": lambda _cid: True,
    "leisure": _leisure, "myday": _myday,
}


def is_ready(section, cid) -> bool:
    """Есть ли у раздела готовый кэш на сегодняшний локальный день; сбой → False."""
    check = _CHECKS.get(section)
    try:
        return bool(check and check(cid))
    except Exception:
        _log.warning("home cache check failed section=%s", section, exc_info=True)
        return False


