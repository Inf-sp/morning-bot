"""Актуальная витрина раздела «Досуг»."""

from __future__ import annotations

from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import leisure_concerts
import leisure_movies
import store
from ui import leisure as leisure_ui
from ui.builder import MessageBuilder


def _keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎫 Концерты", callback_data="a_concerts_find")],
        [InlineKeyboardButton("🎬 Кино", callback_data="a_watch")],
        [InlineKeyboardButton("🎧 Музыка", callback_data="a_listen")],
        [InlineKeyboardButton("📖 Книги", callback_data="a_read")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def _month_concert(events):
    today = datetime.now().date()
    candidates = []
    for event in events or []:
        raw = str(event.get("dates", {}).get("start", {}).get("localDate") or "")
        try:
            event_date = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue
        if event_date < today or (event_date.year, event_date.month) != (today.year, today.month):
            continue
        venue = (event.get("_embedded", {}).get("venues") or [{}])[0]
        city = str((venue.get("city") or {}).get("name") or "").strip()
        candidates.append((event_date, str(event.get("_artist") or event.get("name") or "").strip(), city))
    return min(candidates, default=None, key=lambda item: item[0])


async def send_home(bot, cid, q=None):
    settings = store.get_settings(cid)
    city = str(settings.get("city") or "твой город").strip()
    movies = await leisure_movies.get_local_now_playing(cid, limit=3)
    events = await leisure_concerts._fetch_favorite_events(cid)
    concert = _month_concert(events)

    b = MessageBuilder()
    b.section(f"🍿 Досуг · {city}")
    b.spacer()
    b.bold("🎬 Сейчас в кино")
    b.newline()
    if movies:
        for movie in movies:
            leisure_ui._format_movie_row(b, movie)
    else:
        b.spacer()
        b.line(f"Сегодня не нашёл подтверждённых сеансов в {city}.")
    if concert:
        event_date, artist, venue_city = concert
        b.spacer()
        b.bold("🎫 В этом месяце")
        b.newline()
        b.line(f"{artist} · {event_date.day} {leisure_ui._MONTHS_RU[event_date.month]} · {venue_city}")
    msg = b.build_stripped(reply_markup=_keyboard())
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=msg.reply_markup)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=msg.reply_markup)
