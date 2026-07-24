"""Актуальная витрина раздела «Досуг»."""

from __future__ import annotations

from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity

import leisure_concerts
import leisure_books
import leisure_movies
import leisure_music
import myday
import store
from ui import leisure as leisure_ui
from ui.builder import MessageBuilder
from util import esc


def _keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Кино", callback_data="a_watch")],
        [InlineKeyboardButton("🎧 Музыка", callback_data="a_listen")],
        [InlineKeyboardButton("📖 Книги", callback_data="a_read")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def _next_concert(events):
    today = datetime.now().date()
    candidates = []
    for event in events or []:
        raw = str(event.get("dates", {}).get("start", {}).get("localDate") or "")
        try:
            event_date = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue
        if event_date < today:
            continue
        venue = (event.get("_embedded", {}).get("venues") or [{}])[0]
        city = str((venue.get("city") or {}).get("name") or "").strip()
        candidates.append((event_date, event, city))
    return min(candidates, default=None, key=lambda item: item[0])


async def send_home(bot, cid, q=None):
    settings = store.get_settings(cid)
    city = str(settings.get("city") or leisure_movies._movie_city(cid) or "твой город").strip()
    cc = str(settings.get("cc") or "NL").upper()
    concert = _next_concert(leisure_concerts._concerts_cache_get(cid, cc) or [])
    artist = leisure_music._cached_artist(cid)
    book = leisure_books._cached_book(cid)
    now_playing = await leisure_movies.get_local_now_playing(cid, limit=3)

    b = MessageBuilder()
    b.section(f"🍿 Досуг · {city}")
    b.spacer()
    if concert:
        event_date, event, venue_city = concert
        artist_name = str(event.get("_artist") or event.get("name") or "").strip()
        b.bold("🎫 Ближайшее событие")
        b.newline()
        b.line(f"{artist_name} · {event_date.day} {leisure_ui._MONTHS_RU[event_date.month]} · {venue_city}")
        context = leisure_concerts._concert_context(event)
        genre = leisure_concerts._concert_genre(event)
        price = leisure_concerts._concert_min_price(event)
        details = " · ".join(value for value in (context, genre, price) if value)
        if details:
            b.line(details)
    elif artist and artist.get("artist"):
        b.bold("🎧 Послушать")
        b.newline()
        tracks = artist.get("tracks") or []
        track = str(tracks[0]).split(" - ", 1)[0].strip() if tracks else ""
        b.line(f"{artist['artist']} · {track}" if track else str(artist["artist"]))
        description = str(artist.get("desc") or "").split(".", 1)[0].strip()
        if description:
            b.line(description)
    elif book and book.get("title"):
        b.bold("📖 Почитать")
        b.newline()
        author = str(book.get("author") or "").strip()
        title = str(book.get("title") or "").strip()
        b.line(f"{author} · «{title}»" if author else f"«{title}»")
        year = str(book.get("year") or "").strip()
        b.line(f"Новая книга {year}" if year else "Новая книга")
    elif now_playing:
        b.line("Три фильма, которые сейчас идут в кино.")
    else:
        b.line("Выбери кино, музыку или книгу — подберу что-то на сегодня.")
    if now_playing:
        b.section("🎟️ Сейчас в кино")
        for movie in now_playing:
            leisure_ui._format_movie_row(b, movie, with_description=True)
    try:
        quote_data = myday._fetch_quote(cid)
    except Exception:
        quote_data = {}
    quote = myday._clip_quote(myday._strip_quotes(quote_data.get("quote", "")))
    if quote and myday._quote_valid(quote):
        author = esc(quote_data.get("src", "")).strip()
        quote_line = f"«{esc(quote)}»" + (f" — по {author}" if author else "")
        b.spacer()
        b.add(f"💭 {quote_line}", MessageEntity.ITALIC)
        b.newline()
    msg = b.build_stripped(reply_markup=_keyboard())
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=msg.reply_markup)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=msg.reply_markup)
