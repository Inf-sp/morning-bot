"""Хаб «🍿 Досуг»: премьеры кино и книг, новая музыка, подборки и библиотека.

Открытие хаба читает готовые кэши премьер — без AI; «Новая музыка» берётся из
ленты Apple Music с кэшем на 6 часов. Кэши обновляет ночной прогрев
``warm_hub_cache`` (задача ``leisure`` в bot_maintenance.job_warm_home_pages).
Концерты любимых артистов приходят рассылкой «Концерты недели».
"""
import asyncio
import logging
from datetime import datetime

import apple_music
import config
import leisure_books
import leisure_concerts
import leisure_movies
import leisure_music
import store
from ui import leisure as leisure_ui

_log = logging.getLogger(__name__)


def _today_and_cc(cid):
    return datetime.now(config.TZ).date(), str(store.get_settings(cid).get("cc") or "NL").upper()


def new_music(cid, cc, today) -> list[dict]:
    """Свежие альбомы по музыкальным стилям пользователя (без стилей — по всем), разные артисты."""
    styles = leisure_music._music_styles(cid) or list(apple_music.GENRE_IDS)
    labels = {key: label for key, label, _prompt in leisure_music._MUSIC_GENRES}
    releases = sorted(
        (item for style in styles for item in apple_music.new_releases(style, cc.lower(), today=today)),
        key=lambda item: item["date"], reverse=True,
    )
    items, artists = [], set()
    for item in releases:
        if item["artist"].casefold() in artists:
            continue
        artists.add(item["artist"].casefold())
        items.append({**item, "genre_label": labels.get(item["genre"], "")})
    return items


def hub_data(cid) -> dict:
    """Блоки хаба; пустой список — блок скрыт. «Новая музыка» может сходить в сеть — вызывать в потоке."""
    today, cc = _today_and_cc(cid)
    limit = leisure_ui.LEISURE_HUB_LIMIT
    return {
        "movies": list(leisure_movies._movie_premieres_cache_get(cc, today, allow_stale=True) or [])[:limit],
        "books": list(leisure_books._book_premieres_cache_get(today, allow_stale=True) or [])[:limit],
        "music": new_music(cid, cc, today)[:limit],
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
    today, _cc = _today_and_cc(cid)
    data = await asyncio.to_thread(hub_data, cid)
    msg = leisure_ui.leisure_hub_screen(
        **data, reply_markup=leisure_ui.leisure_hub_kb(),
        month=today.month, city=store.get_settings(cid).get("city") or "",
    )
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
        ("new_music", lambda: asyncio.to_thread(apple_music._feed, _today_and_cc(cid)[1].lower())),
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
        and leisure_movies._cached_movie(cid) is not None
    )
