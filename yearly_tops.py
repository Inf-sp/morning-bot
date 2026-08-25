"""Перелистываемые топы контента за предыдущий календарный год."""

import asyncio
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

import config
import google_books
import igdb
import open_library
import tmdb
from ui import leisure as leisure_ui


_BOOKS_2025 = (
    {"title": "Flesh", "author": "David Szalay", "genre": "литературная проза",
     "summary": "Сдержанный роман о теле, амбициях и цене социального подъёма."},
    {"title": "The Loneliness of Sonia and Sunny", "author": "Kiran Desai", "genre": "семейная сага",
     "summary": "История двух людей между Индией и Америкой, любовью и семейной памятью."},
    {"title": "The Book of Records", "author": "Madeleine Thien", "genre": "историческая проза",
     "summary": "Многослойный роман о памяти, изгнании и людях, которых разделяют эпохи."},
    {"title": "My Friends", "author": "Fredrik Backman", "genre": "современная проза",
     "summary": "Тёплая история о дружбе, искусстве и следе, который люди оставляют друг в друге."},
    {"title": "One Day, Everyone Will Have Always Been Against This", "author": "Omar El Akkad", "genre": "эссе",
     "summary": "Личное и острое размышление о справедливости, принадлежности и политическом разочаровании."},
)

_GAMES_2025 = (
    {"title": "Clair Obscur: Expedition 33", "genre": "RPG",
     "summary": "Пошаговая RPG с реактивными боями, выразительным миром и сильной историей об обречённой экспедиции."},
    {"title": "Hades II", "genre": "рогалик · экшен",
     "summary": "Стремительный мифологический экшен, где каждый новый забег продолжает историю и открывает новые стили боя."},
    {"title": "Hollow Knight: Silksong", "genre": "метроидвания",
     "summary": "Точное платформенное приключение с быстрыми боями, сложными маршрутами и загадочным насекомым королевством."},
    {"title": "Kingdom Come: Deliverance II", "genre": "RPG · открытый мир",
     "summary": "Приземлённое средневековое приключение, в котором решения, репутация и подготовка важны не меньше владения мечом."},
    {"title": "Death Stranding 2: On the Beach", "genre": "приключение · экшен",
     "summary": "Необычное путешествие о связях между людьми с масштабными ландшафтами и глубокой системой доставки."},
)


def previous_year() -> int:
    return datetime.now(config.TZ).year - 1


async def get_items(kind):
    year = previous_year()
    if kind in ("movie", "tv"):
        items = await asyncio.to_thread(
            tmdb.discover, kind, None, 7.0, year, year, None, None,
            "vote_average.desc", 1,
        )
        items = [dict(item) for item in items
                 if str(item.get("year") or "") == str(year)
                 and int(item.get("vote_count") or 0) >= 500]
        items.sort(key=lambda item: (
            float(item.get("rating") or 0), int(item.get("vote_count") or 0),
        ), reverse=True)
        result = []
        for item in items:
            poster = await asyncio.to_thread(tmdb.english_poster, item.get("id"), kind)
            if not poster or not item.get("overview"):
                continue
            item["poster"] = poster
            item["title"] = item.get("name")
            item["genre"] = item.get("genres")
            result.append(item)
            if len(result) == 5:
                break
        return result
    if kind == "book":
        return await asyncio.gather(*(
            asyncio.to_thread(_enrich_book, item) for item in _BOOKS_2025
        ))
    if kind == "game":
        return await asyncio.gather(*(
            asyncio.to_thread(igdb.enrich_game_recommendation, item) for item in _GAMES_2025
        ))
    return []


def _enrich_book(item):
    enriched = google_books.enrich_book(item)
    if enriched.get("cover_url"):
        return enriched
    matches = open_library.search_books(
        item.get("title"), author=item.get("author"), year=previous_year(),
        max_results=5, english_only=True,
    )
    if not matches:
        return enriched
    result = dict(enriched)
    for field in ("cover_url", "info_link", "isbn"):
        if matches[0].get(field):
            result[field] = matches[0][field]
    return result


def _photo(item):
    return str(item.get("poster") or item.get("cover_url") or "").strip()


def _view(kind, items, page=0):
    page = page % len(items) if items else 0
    item = items[page] if items else None
    msg = leisure_ui.yearly_top_screen(kind, previous_year(), item)
    rows = []
    if items:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"yt:{kind}:{(page - 1) % len(items)}"),
            InlineKeyboardButton(f"{page + 1}/5", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"yt:{kind}:{(page + 1) % len(items)}"),
        ])
    back = "m_movie" if kind in ("movie", "tv") else f"m_{'books' if kind == 'book' else 'games'}"
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data=back),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    return msg, InlineKeyboardMarkup(rows), page


async def send(bot, cid, kind, *, status=None):
    items = await get_items(kind)
    msg, markup, _page = _view(kind, items)
    photo = _photo(items[0]) if items else ""
    if photo:
        await bot.send_photo(
            chat_id=cid, photo=photo, caption=msg.text,
            caption_entities=msg.entities, reply_markup=markup,
        )
        return
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=markup)
        return
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=markup,
    )


async def show_page(q, kind, page):
    items = await get_items(kind)
    msg, markup, page = _view(kind, items, page)
    photo = _photo(items[page]) if items else ""
    if photo:
        await q.edit_message_media(
            media=InputMediaPhoto(
                media=photo, caption=msg.text, caption_entities=msg.entities,
            ),
            reply_markup=markup,
        )
    else:
        await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=markup)
