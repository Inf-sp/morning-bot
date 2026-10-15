"""Перелистываемые топы контента за предыдущий календарный год."""

import asyncio
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

import config
import tmdb
from ui import leisure as leisure_ui
from ui.navigation import nav_row


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
    return []


def _photo(item):
    return str(item.get("poster") or "").strip()


def _view(kind, items, page=0):
    page = page % len(items) if items else 0
    item = items[page] if items else None
    msg = leisure_ui.yearly_top_screen(kind, previous_year(), item)
    rows = []
    if items:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"yt:{kind}:{(page - 1) % len(items)}"),
            InlineKeyboardButton(f"{page + 1}/{len(items)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"yt:{kind}:{(page + 1) % len(items)}"),
        ])
    rows.append(nav_row("m_leisure"))
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
