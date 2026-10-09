"""«Новинка» в меню «Выбрать жанр»: премьера кино, книги или свежий альбом.

Данные — только из реальных источников, которые уже собирает бот (TMDB,
книжная витрина премьер) и Apple Music для музыки. Показанные новинки
идут по кругу без повторов; «Не нравится» убирает новинку навсегда.
"""
import asyncio
import logging
from datetime import date
from itertools import zip_longest

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import apple_music
import leisure_books
import leisure_movies
import leisure_music
import recommendation_rotation as rotation
import recommendation_stoplist
import store
import tmdb
from ui import leisure as leisure_ui
from ui.navigation import nav_row

_log = logging.getLogger(__name__)

KINDS = ("movie", "book", "music")
_OTHER_LABEL = {
    "movie": "✨ Другой фильм", "book": "✨ Другая книга",
    "music": "✨ Другой артист",
}


def genre_picker(cid, kind, back="m_leisure"):
    """Тот же выбор жанра, что под карточкой рекомендации (с «Новинкой» и «Любым жанром»)."""
    if kind == "movie":
        return leisure_movies._movie_genre_menu_kb(back=back)
    if kind == "book":
        return leisure_books._book_genre_menu_kb(back=back)
    return leisure_music._music_genre_menu_kb(cid, back=back)
# Тип записи стоп-листа: альбом музыки не должен скрывать артиста.
_STOP_KIND = {"movie": "movie", "book": "book", "music": "album"}
_SEEN_KEY = "novelty_seen"
_SEEN_LIMIT = 50


def _key(item) -> str:
    return rotation.identity(f"{item.get('title', '')} {item.get('artist', '')}")


def _marker(value) -> str:
    """История хранит готовые ключи, кандидаты — словари."""
    return _key(value) if isinstance(value, dict) else str(value or "")


async def _items(cid, kind) -> list[dict]:
    if kind == "movie":
        # Только фильмы, которые уже идут в кино: будущие премьеры не советуем.
        today = date.today().isoformat()
        return [dict(item) for item in await leisure_movies.get_movie_premieres(cid)
                if str(item.get("date") or "")[:10] <= today]
    if kind == "book":
        return [dict(item) for item in await leisure_books._book_premieres_with_covers()]
    styles = leisure_music._music_styles(cid) or list(apple_music.GENRE_IDS)
    cc = str(store.get_settings(cid).get("cc") or "nl")
    # Одна лента страны на все жанры: после первого запроса остальные берут её из кэша.
    lists = await asyncio.to_thread(lambda: [apple_music.new_releases(key, cc) for key in styles])
    # По очереди из каждого выбранного жанра, чтобы один жанр не забивал круг.
    return [item for group in zip_longest(*lists) for item in group if item]


def _pick(cid, kind, items):
    hidden = {rotation.identity(value) for value in recommendation_stoplist.values(cid, _STOP_KIND[kind])}
    pool = [item for item in items if rotation.identity(item.get("title")) not in hidden]
    seen = (store.get_profile(cid).get(_SEEN_KEY) or {}).get(kind) or []
    current = seen[-1] if seen else None
    fresh = rotation.candidates_for_cycle(pool, seen, current=current, key=_marker)
    return fresh[0] if fresh else None


def _remember(cid, kind, item):
    def change(profile):
        seen = dict(profile.get(_SEEN_KEY) or {})
        seen[kind] = rotation.remember(seen.get(kind) or [], _key(item), limit=_SEEN_LIMIT)
        profile[_SEEN_KEY] = seen
        return profile, None

    store.mutate_profile(cid, change)


def card_keyboard(kind):
    """«Другой…» открывает выбор жанра под карточкой (там же следующая «Новинка»)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Не нравится", callback_data=f"nov_no_{kind}",
                              api_kwargs={"style": "danger"})],
        [InlineKeyboardButton(_OTHER_LABEL[kind], callback_data=f"nov_pick_{kind}")],
        [InlineKeyboardButton("🎚️ Настроить", callback_data=f"lz_cfg_{kind}")],
        nav_row("m_leisure"),
    ])


async def _image(kind, item) -> str:
    if kind == "movie":
        return str(await asyncio.to_thread(tmdb.english_poster, item.get("id"), "movie") or "").strip()
    return str(item.get("cover_url") or item.get("cover") or item.get("poster") or "").strip()


async def send_novelty(bot, cid, kind, *, status=None):
    """Карточка новинки; без данных — короткое сообщение с возвратом к жанрам."""
    if kind not in KINDS:
        return
    item = _pick(cid, kind, await _items(cid, kind))
    if not item:
        await _deliver(bot, cid, leisure_ui.novelty_empty(kind), genre_picker(cid, kind), status=status)
        return
    _remember(cid, kind, item)
    store.last_recos[str(cid)] = {"kind": f"novelty_{kind}", "items": [item.get("title")]}
    msg = leisure_ui.novelty_card(kind, item)
    kb = card_keyboard(kind)
    image = await _image(kind, item)
    if image:
        try:
            await bot.send_photo(chat_id=cid, photo=image, caption=msg.text,
                                 caption_entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            _log.debug("send_novelty: photo failed, sending text", exc_info=True)
    await _deliver(bot, cid, msg, kb, status=status)


async def dislike_novelty(bot, cid, kind, *, status=None):
    """«Не нравится»: новинка уходит в стоп-лист, сразу показывается следующая."""
    rec = store.last_recos.get(str(cid))
    if kind in KINDS and isinstance(rec, dict) and rec.get("kind") == f"novelty_{kind}" and rec.get("items"):
        recommendation_stoplist.add(cid, _STOP_KIND[kind], rec["items"][0], "hidden")
    await send_novelty(bot, cid, kind, status=status)


async def _deliver(bot, cid, msg, kb, *, status=None):
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb,
                             disable_web_page_preview=True)
        return
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=kb, disable_web_page_preview=True)
