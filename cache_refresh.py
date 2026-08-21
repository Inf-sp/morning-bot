"""Последовательное ручное обновление пользовательских кэшей."""

import asyncio
import logging
from datetime import datetime

import config
import store

_log = logging.getLogger(__name__)
_RUNNING = set()
_TASKS = {}
_STEP_DELAY_SECONDS = 10


def is_running(cid) -> bool:
    return str(cid) in _RUNNING


def start(bot, cid) -> bool:
    key = str(cid)
    if key in _RUNNING:
        return False
    _RUNNING.add(key)
    task = asyncio.create_task(_run(bot, cid))
    _TASKS[key] = task

    def finish(_task):
        _RUNNING.discard(key)
        _TASKS.pop(key, None)

    task.add_done_callback(finish)
    return True


async def _run(bot, cid):
    import learning
    import leisure_books
    import leisure_concerts
    import leisure_games
    import leisure_movies
    import leisure_music
    import myday
    import recipe_generation
    import travel
    import wardrobe
    import weather_provider

    async def weather_step():
        settings = store.get_settings(cid)
        lat, lon = settings.get("lat"), settings.get("lon")
        if lat is None or lon is None:
            return
        weather_provider.invalidate_weather_cache(lat, lon, 2)
        await asyncio.to_thread(weather_provider.fetch_weather, lat, lon, 2)

    async def wardrobe_step():
        store.clear_wardrobe_daylook(cid)
        await wardrobe.warm_home_cache(cid)

    async def cooking_step(hour):
        now = datetime.now(config.TZ).replace(hour=hour, minute=0, second=0, microsecond=0)
        await asyncio.to_thread(recipe_generation.get_cooking_home_idea, cid, now, True)

    async def learning_step():
        learning.reset_daily_material_cache(cid)
        await asyncio.to_thread(learning.warm_home_cache, cid)

    async def myday_step():
        myday.reset_day_cache(cid)
        await myday.warm_day_cache(cid)

    steps = (
        ("weather", weather_step),
        ("wardrobe", wardrobe_step),
        ("breakfast", lambda: cooking_step(8)),
        ("lunch", lambda: cooking_step(13)),
        ("dinner", lambda: cooking_step(18)),
        ("learning", learning_step),
        ("myday", myday_step),
        ("travel", lambda: travel.warm_home_cache(cid, refresh=True)),
        ("cinema", lambda: leisure_movies.get_local_now_playing(cid, limit=20, refresh=True)),
        ("books", lambda: leisure_books.warm_books_home_cache(cid, refresh=True)),
        ("music", lambda: leisure_music.warm_music_home_cache(cid)),
        ("games", lambda: asyncio.to_thread(leisure_games.pick_game, cid, refresh=True)),
        ("movie premieres", lambda: leisure_movies.warm_movie_premieres_cache(cid)),
        ("book premieres", leisure_books.warm_book_premieres_cache),
        ("game premieres", lambda: leisure_games.warm_game_premieres_cache(cid)),
        ("concerts", lambda: leisure_concerts.refresh_concerts_cache(cid)),
    )
    failures = []
    for index, (name, call) in enumerate(steps):
        try:
            await call()
        except Exception as error:
            failures.append(name)
            _log.warning("manual cache refresh failed cid=%s cache=%s: %r", cid, name, error)
        if index < len(steps) - 1:
            await asyncio.sleep(_STEP_DELAY_SECONDS)
    text = (
        "✅ Кэши обновлены."
        if not failures else
        "🟡 Кэши обновлены частично. Недоступные источники попробую обновить по расписанию."
    )
    await bot.send_message(chat_id=cid, text=text)
