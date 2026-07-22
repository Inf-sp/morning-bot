"""Актуальная витрина раздела «Досуг»."""

from __future__ import annotations

import asyncio
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import leisure_concerts
import leisure_books
import leisure_movies
import leisure_music
import store
from ui import leisure as leisure_ui
from ui.builder import MessageBuilder


def _keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎫 Концерты", callback_data="a_concerts_find"),
         InlineKeyboardButton("🎬 Кино", callback_data="a_watch")],
        [InlineKeyboardButton("🎧 Музыка", callback_data="a_listen"),
         InlineKeyboardButton("📖 Книги", callback_data="a_read")],
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
    city = str(settings.get("city") or leisure_movies._movie_city(cid) or "твой город").strip()
    movies, artist, book, events = await asyncio.gather(
        leisure_movies.get_local_now_playing(cid, limit=3),
        leisure_music.send_listen(None, cid, preview=True),
        leisure_books.get_current_book(cid),
        leisure_concerts._fetch_favorite_events(cid),
    )
    concert = _month_concert(events)

    b = MessageBuilder()
    b.section(f"🍿 Досуг · {city}")
    b.spacer()
    b.bold("🎬 В кино сегодня")
    b.newline()
    if movies:
        main_movie, *other_movies = movies
        rating = leisure_ui._format_rating(main_movie.get("rating")) if int(main_movie.get("vote_count") or 0) >= 25 else None
        title = str(main_movie.get("title") or "")
        b.line(f"{rating} · {title}" if rating else title)
        genre = leisure_ui._primary_genre(main_movie)
        if genre:
            b.line(genre)
        if other_movies:
            b.spacer()
            b.bold("Ещё в кино:")
            b.newline()
            for movie in other_movies[:2]:
                title = str(movie.get("title") or "")
                rating = leisure_ui._format_rating(movie.get("rating")) if int(movie.get("vote_count") or 0) >= 25 else None
                b.line(f"• {title}" + (f" · {rating.removeprefix('⭐ ')}" if rating else ""))
    else:
        b.spacer()
        b.line(f"Сегодня не нашёл подтверждённых сеансов в {city}.")
    if artist and artist.get("artist"):
        b.spacer()
        b.bold("🎧 Послушать")
        b.newline()
        tracks = artist.get("tracks") or []
        entry = str(tracks[0]).split(" - ", 1)[0].strip() if tracks else ""
        b.line(f"{artist['artist']} · {entry}" if entry else str(artist["artist"]))
        description = str(artist.get("desc") or "").split(".", 1)[0].strip()
        if description:
            b.line(description)
    if book and book.get("title"):
        b.spacer()
        b.bold("📖 Почитать")
        b.newline()
        author = str(book.get("author") or "").strip()
        title = str(book.get("title") or "").strip()
        b.line(f"{author} · «{title}»" if author else f"«{title}»")
        year = str(book.get("year") or "").strip()
        b.line(f"Новая книга {year}" if year else "Новая книга")
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
