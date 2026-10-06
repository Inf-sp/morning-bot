"""Готовность дневных кэшей главных экранов и статистика их открытия.

Только чтение store: без сети и AI. Используется ночным повторным прогревом
(перестраивает лишь отсутствующие разделы), логом ``home_open`` и строкой
времени открытия разделов в админке.
"""
import logging
from datetime import datetime
from statistics import median

import config
import tracking

_log = logging.getLogger(__name__)

SECTION_BY_CALLBACK = {
    "m_myday": "myday", "m_wardrobe": "wardrobe", "m_food": "cooking",
    "m_learn": "learning", "m_travel": "travel", "m_movie": "cinema",
    "m_music": "music", "m_books": "books", "m_games": "games",
}
SECTION_LABELS = {
    "myday": "Мой день", "wardrobe": "Гардероб", "cooking": "Готовка",
    "learning": "Обучение", "travel": "Поездки", "cinema": "Кино",
    "music": "Музыка", "books": "Книги", "games": "Игры",
}


def _wardrobe(cid):
    import wardrobe
    return wardrobe._get_cached_look(cid) is not None or not wardrobe.has_wardrobe_items(cid)


def _cooking(cid):
    import restaurant_discovery
    return bool(restaurant_discovery.cached_restaurant_preview(cid))


def _travel(cid):
    import travel
    return travel.cached_home_idea(cid) is not None


def _cinema(cid):
    import leisure_movies
    today = datetime.now(config.TZ).date()
    return (
        leisure_movies._now_playing_catalog_get(cid, leisure_movies._movie_city(cid)) is not None
        and leisure_movies._cached_movie(cid) is not None
        and leisure_movies._cinema_birthday_cache_get(today) is not None
    )


def _music(cid):
    import leisure_music
    return leisure_music._music_home_cache_get(cid) is not None


def _books(_cid):
    import leisure_books
    return leisure_books._weekly_book_cache_get() is not None


def _games(cid):
    import leisure_games
    return leisure_games.has_seasonal_premieres_cache(cid)


def _myday(cid):
    import myday
    cache = myday._load_day_cache(cid, datetime.now(config.TZ).strftime("%Y-%m-%d"))
    return cache is not None and not myday._cache_misses_ready_sections(cache, cid)


_CHECKS = {
    "wardrobe": _wardrobe, "cooking": _cooking, "learning": lambda _cid: True,
    "travel": _travel, "cinema": _cinema, "music": _music, "books": _books,
    "games": _games, "myday": _myday,
}


def is_ready(section, cid) -> bool:
    """Есть ли у раздела готовый кэш на сегодняшний локальный день; сбой → False."""
    check = _CHECKS.get(section)
    try:
        return bool(check and check(cid))
    except Exception:
        _log.warning("home cache check failed section=%s", section, exc_info=True)
        return False


def _fmt_seconds(value):
    return f"{value:.1f}".replace(".", ",")


def today_open_stats_line(now=None) -> str:
    """«⏱ Разделы сегодня: …» из журнала задержек действий; пусто без данных."""
    now = now or datetime.now(config.TZ)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    rows = [
        (row.get("duration_ms") / 1000, SECTION_BY_CALLBACK[row.get("action")])
        for row in tracking.get_action_latencies(limit=500)
        if row.get("action") in SECTION_BY_CALLBACK
        and isinstance(row.get("duration_ms"), (int, float))
        and float(row.get("ts") or 0) >= day_start
    ]
    if not rows:
        return ""
    worst_seconds, worst_section = max(rows)
    return (
        f"⏱ Разделы сегодня: медиана {_fmt_seconds(median(s for s, _ in rows))} с · "
        f"худший {_fmt_seconds(worst_seconds)} с ({SECTION_LABELS[worst_section]})"
    )
