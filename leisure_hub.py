"""Хаб «🍿 Досуг»: концерты, премьеры, подборки и библиотека.

Открытие хаба только читает готовые кэши (концерты любимых артистов, премьеры
кино/книг, сезонные игры) — без сети и AI. Кэши обновляет ночной прогрев
``warm_hub_cache`` (задача ``leisure`` в bot_maintenance.job_warm_home_pages).
"""
import logging
from datetime import datetime

import config
import leisure_books
import leisure_concerts
import leisure_games
import leisure_movies
import store
from ui import leisure as leisure_ui

_log = logging.getLogger(__name__)


def _today_and_cc(cid):
    return datetime.now(config.TZ).date(), str(store.get_settings(cid).get("cc") or "NL").upper()


def hub_data(cid) -> dict:
    """Блоки хаба из кэшей; пустой список — блок скрыт."""
    today, cc = _today_and_cc(cid)
    limit = leisure_ui.LEISURE_HUB_LIMIT
    return {
        "concerts": leisure_concerts.cached_favorite_concerts(cid, limit),
        "movies": leisure_movies._movie_premieres_cache_get(cc, today, allow_stale=True) or [],
        "books": leisure_books._book_premieres_cache_get(today, allow_stale=True) or [],
        "games": leisure_games.cached_season_premieres(cid) or [],
    }


async def _show(bot, cid, msg, q=None):
    """Меню хаба заменяет текущее меню на месте; с фото-карточки — новым сообщением."""
    if q is not None:
        try:
            await q.message.edit_text(
                msg.text, entities=msg.entities, reply_markup=msg.reply_markup,
                disable_web_page_preview=True,
            )
            return
        except Exception:
            _log.debug("_show: ignored error", exc_info=True)
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=msg.reply_markup,
        disable_web_page_preview=True,
    )


async def send_hub(bot, cid, q=None):
    msg = leisure_ui.leisure_hub_screen(**hub_data(cid), reply_markup=leisure_ui.leisure_hub_kb())
    await _show(bot, cid, msg, q)


async def send_premieres_menu(bot, cid, q=None):
    await _show(bot, cid, leisure_ui.leisure_premieres_menu(), q)


async def send_library_menu(bot, cid, q=None):
    await _show(bot, cid, leisure_ui.leisure_library_menu(), q)


async def _warm_concerts(cid):
    _today, cc = _today_and_cc(cid)
    if leisure_concerts._ensure_artists(cid) and leisure_concerts._concerts_cache_get(cid, cc) is None:
        await leisure_concerts.refresh_concerts_cache(cid)


async def _warm_movie_premieres(cid):
    today, cc = _today_and_cc(cid)
    if leisure_movies._movie_premieres_cache_get(cc, today) is None:
        await leisure_movies.warm_movie_premieres_cache(cid)


async def warm_hub_cache(cid):
    """Ночной прогрев хаба и карточки «Что посмотреть»; сеть только для устаревших кэшей.

    Ошибка одного источника не мешает остальным; False — хотя бы один не обновился.
    """
    steps = (
        ("concerts", lambda: _warm_concerts(cid)),
        ("movie_premieres", lambda: _warm_movie_premieres(cid)),
        # refresh=True возвращает свежий кэш без запроса; внешний поиск — только без него.
        ("book_premieres", lambda: leisure_books.get_book_premieres(refresh=True)),
        ("game_premieres", lambda: leisure_games.get_game_premieres(cid, refresh=True, seasonal=True)),
        ("movie_reco", lambda: leisure_movies.get_current_movie(cid)),
    )
    ok = True
    for name, call in steps:
        try:
            await call()
        except Exception:
            ok = False
            _log.exception("leisure hub warm failed cid=%s step=%s", cid, name)
    return ok


def is_ready(cid) -> bool:
    """Свежие (не stale) кэши хаба и карточка дня «Что посмотреть»."""
    today, cc = _today_and_cc(cid)
    return (
        leisure_movies._movie_premieres_cache_get(cc, today) is not None
        and leisure_books._book_premieres_cache_get(today) is not None
        and leisure_games.cached_season_premieres(cid, allow_stale=False) is not None
        and leisure_movies._cached_movie(cid) is not None
    )
