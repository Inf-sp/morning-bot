import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot as bot_module
import leisure
import menu
import tmdb
from ui import menu as menu_ui


def _buttons(markup):
    return [[(btn.text, btn.callback_data) for btn in row] for row in markup.inline_keyboard]


def _flat_buttons(markup):
    return [item for row in _buttons(markup) for item in row]


def _assert_button(markup, text, callback_data):
    assert (text, callback_data) in _flat_buttons(markup), (text, callback_data, _buttons(markup))


def _fake_update(data):
    message = SimpleNamespace(chat_id=123, edit_text=AsyncMock())
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        message=message,
        edit_message_reply_markup=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    return update, context


async def _dispatch(data, **patched):
    update, context = _fake_update(data)
    with patch("access.is_allowed", return_value=True), \
         patch("tracking.touch", return_value=None), \
         patch("firstvisit.needs_setup", return_value=False), \
         patch.object(bot_module, "_ack", AsyncMock()), \
         patch.multiple(leisure, **patched):
        await bot_module.answer_callback(update, context)
    return update, context


async def main():
    leisure_menu = menu_ui.menu_screen("m_leisure")
    assert leisure_menu.text == (
        "🍿 Досуг\n\n"
        "Фильмы, музыка и книги - под твой вкус.\n"
        "Предпочтения и сохранённое - в настройках."
    )
    assert _buttons(leisure_menu.reply_markup) == [
        [("🎸 Концерты", "a_concerts_find")],
        [("🎬 Сейчас в кино", "a_now_playing")],
        [("🍿 Посмотреть дома", "a_watch")],
        [("🎧 Музыка", "a_listen")],
        [("📖 Книги", "a_read")],
        [("📰 Новости", "a_news_home")],
        [("🎚️ Настройки досуга", "m_leisure_settings")],
    ]
    menu_blob = leisure_menu.text + " " + " ".join(text for text, _ in _flat_buttons(leisure_menu.reply_markup))
    for legacy in ("Подбор музыкантов", "Подбор кино", "Подбор книг"):
        assert legacy not in menu_blob, legacy
    event_client = "_event" + "brite_events"
    assert not hasattr(leisure, event_client)
    assert not hasattr(leisure, event_client + "_many")

    _assert_button(leisure._movie_home_kb(), "⬅️ Назад", "m_leisure")
    _assert_button(leisure._movie_kb(0), "⬅️ Назад", "m_leisure")
    _assert_button(leisure._movie_genre_menu_kb(), "⬅️ Назад", "m_leisure")
    _assert_button(leisure._movie_mood_menu_kb(), "⬅️ Назад", "m_leisure")
    _assert_button(leisure._book_kb(0), "⬅️ Назад", "m_leisure")
    _assert_button(leisure._listen_kb(), "⬅️ Назад", "m_leisure")
    _assert_button(menu_ui.menu_screen("m_leisure_settings").reply_markup, "⬅️ Назад", "m_leisure")

    concerts_mock = AsyncMock()
    update, context = await _dispatch("a_concerts_find", find_concerts=concerts_mock)
    concerts_mock.assert_awaited_once_with(context.bot, "123", "home")

    now_playing_mock = AsyncMock()
    update, context = await _dispatch("a_now_playing", send_now_playing=now_playing_mock)
    now_playing_mock.assert_awaited_once_with(context.bot, "123", update.callback_query)

    cinema_page_mock = AsyncMock()
    update, context = await _dispatch("a_cinema_page_1", send_now_playing=cinema_page_mock)
    cinema_page_mock.assert_awaited_once_with(context.bot, "123", update.callback_query, 1)

    cinema_open_mock = AsyncMock()
    update, context = await _dispatch("a_cinema_open_42", open_cinema_movie=cinema_open_mock)
    cinema_open_mock.assert_awaited_once_with(context.bot, "123", "42")

    movie_home_mock = AsyncMock()
    update, context = await _dispatch("a_watch", send_movie_home=movie_home_mock)
    movie_home_mock.assert_awaited_once_with(context.bot, "123", update.callback_query)

    listen_mock = AsyncMock()
    update, context = await _dispatch("a_listen", send_listen=listen_mock)
    listen_mock.assert_awaited_once_with(context.bot, "123")

    read_mock = AsyncMock()
    update, context = await _dispatch("a_read", send_recos=read_mock)
    read_mock.assert_awaited_once_with(context.bot, "123", "book")

    update, context = _fake_update("m_leisure_settings")
    text, entities, reply_markup = menu.menu_screen("m_leisure_settings")
    with patch("access.is_allowed", return_value=True), \
         patch("tracking.touch", return_value=None), \
         patch("firstvisit.needs_setup", return_value=False):
        await bot_module.answer_callback(update, context)
    update.callback_query.message.edit_text.assert_awaited_once_with(
        text,
        reply_markup=reply_markup,
        entities=entities,
    )

    fake_movies = [
        tmdb.CinemaMovie(
            id=idx,
            title=f"Фильм номер {idx}",
            original_title=None,
            overview=None,
            poster_url=None,
            release_date=date(2026, 7, 10),
            genres=["ужасы" if idx == 1 else "драма"],
            rating=7.1 if idx == 1 else 6.5,
            popularity=100 - idx,
            country_code="NL",
            is_theatrical=True,
        )
        for idx in range(1, 13)
    ]
    fake_bot = SimpleNamespace(send_message=AsyncMock())
    with patch("store.get_settings", return_value={"cc": "NL", "country": "Нидерланды"}), \
         patch.object(leisure.tmdb, "get_now_playing", return_value=fake_movies), \
         patch.object(leisure.config, "TMDB_API_KEY", "tmdb-key"):
        await leisure.send_now_playing(fake_bot, "123")
    now_playing_text = fake_bot.send_message.await_args.kwargs["text"]
    now_playing_markup = fake_bot.send_message.await_args.kwargs["reply_markup"]
    assert "🎬 В кино сейчас · Нидерланды" in now_playing_text
    assert "• Фильм номер 1 · ужасы · ⭐ 7.1" in now_playing_text
    _assert_button(now_playing_markup, "⬅️ Назад", "m_leisure")
    assert _buttons(now_playing_markup)[-2] == [("◀️", "noop"), ("1 / 2", "noop"), ("▶️", "a_cinema_page_1")]
    assert _buttons(now_playing_markup)[0] == [("Фильм номер 1", "a_cinema_open_1")]

    empty_bot = SimpleNamespace(send_message=AsyncMock())
    with patch("store.get_settings", return_value={"cc": "NL", "country": "Нидерланды"}), \
         patch.object(leisure.tmdb, "get_now_playing", return_value=[]), \
         patch.object(leisure.config, "TMDB_API_KEY", "tmdb-key"):
        await leisure.send_now_playing(empty_bot, "123")
    assert empty_bot.send_message.await_args.kwargs["text"] == (
        "🎬 В кино сейчас · Нидерланды\n\nПока не удалось найти фильмы в прокате."
    )

    concert_bot = SimpleNamespace(send_message=AsyncMock())
    with patch.object(leisure.config, "TICKETMASTER_API_KEY", "token"), \
         patch("store.get_settings", return_value={"cc": "NL", "country": "Нидерланды"}), \
         patch.object(leisure, "_ensure_artists", return_value=["Within Temptation"]), \
         patch.object(leisure, "_concerts_cache_get", return_value=[]):
        await leisure.find_concerts(concert_bot, "123", "home")
    concerts_markup = concert_bot.send_message.await_args.kwargs["reply_markup"]
    _assert_button(concerts_markup, "⬅️ Назад", "m_leisure")

    country_bot = SimpleNamespace(send_message=AsyncMock())
    await leisure.concert_pick_country(country_bot, "123")
    country_markup = country_bot.send_message.await_args.kwargs["reply_markup"]
    _assert_button(country_markup, "⬅️ Назад", "m_leisure")

    weekly_bot = SimpleNamespace(send_message=AsyncMock())
    upcoming_mock = Mock(return_value=fake_movies[:2])
    with patch("store.get_settings", return_value={"cc": "NL", "country": "Нидерланды"}), \
         patch.object(leisure.config, "TICKETMASTER_API_KEY", ""), \
         patch.object(leisure.config, "TMDB_API_KEY", "tmdb-key"), \
         patch.object(leisure.tmdb, "get_upcoming_theatrical_releases", upcoming_mock):
        await leisure.send_weekly_events(weekly_bot, "123")
    upcoming_mock.assert_called_once()
    weekly_text = weekly_bot.send_message.await_args.kwargs["text"]
    assert "🎬 Кино" in weekly_text
    assert "• Фильм номер 1 · ужасы" in weekly_text

    print("ok")


if __name__ == "__main__":
    asyncio.run(main())
