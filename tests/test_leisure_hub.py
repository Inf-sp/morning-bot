"""Хаб «🍿 Досуг»: меню, блоки из кэшей, кнопки, старые callbacks и прогрев."""
import asyncio
import os
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot_callbacks
import cleanup
import config
import home_cache
import leisure_books
import leisure_concerts
import leisure_hub
import leisure_movies
import menu
import routing
import store
from ui import leisure as leisure_ui
from ui import menu as menu_ui
from fakes import RecordingBot


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def _boom(*_args, **_kwargs):
    raise AssertionError("network or AI called on hub open")


def _memory_store(monkeypatch):
    memory = {config.SETTINGS_FILE: {"42": {"cc": "NL", "city": "Alkmaar"}}}

    def load(key):
        return memory.get(key, {})

    def save(key, value):
        memory[key] = value

    def mutate(key, callback):
        data, result = callback(memory.get(key, {}))
        memory[key] = data
        return result

    monkeypatch.setattr(store, "_load", load)
    monkeypatch.setattr(store, "_save", save)
    monkeypatch.setattr(store, "mutate_kv", mutate)
    return memory


def _seed_caches(monkeypatch):
    today = datetime.now(config.TZ).date()
    soon = (today + timedelta(days=5)).isoformat()
    monkeypatch.setattr(leisure_concerts, "_ensure_artists", lambda _cid: ["Muse"])
    leisure_concerts._concerts_cache_set("42", "NL", [{
        "_artist": "Muse", "url": "https://tickets.example/muse",
        "dates": {"start": {"localDate": soon}},
        "_embedded": {"venues": [{"city": {"name": "Amsterdam"}, "country": {"countryCode": "NL"}}]},
    }])
    leisure_movies._movie_premieres_cache_set("NL", today + timedelta(days=7), [
        {"id": 1, "title": "Дюна", "trailer_url": "https://youtube.example/dune"},
    ])
    leisure_books._book_premieres_cache_set(today, [
        {"title": "Новая книга", "url": "https://books.example/new"},
    ])



def test_main_menu_has_leisure_hub_instead_of_four_sections():
    assert _labels(menu.main_menu_kb()) == [
        ["☀️ Мой день"],
        ["👔 Гардероб", "🥣 Готовка"],
        ["🧠 Обучение", "🍿 Досуг"],
        ["🎚️ Настройки"],
    ]
    assert "m_leisure" in _callbacks(menu.main_menu_kb())
    assert menu.is_main_menu_markup(menu.main_menu_kb())
    old_menu = menu_ui.ikb([
        [("☀️ Мой день", "m_myday")], [("👔", "m_wardrobe"), ("🥣", "m_food")],
        [("🧠", "m_learn")], [("🎬", "m_movie"), ("🎧", "m_music")], [("🎚️", "m_settings")],
    ])
    assert menu.is_main_menu_markup(old_menu)


def test_hub_renders_all_blocks_with_links_and_one_column_buttons():
    msg = leisure_ui.leisure_hub_screen(
        [{"title": "Muse", "date": "2026-10-12", "url": "https://t.example"}],
        [{"title": "Дюна", "trailer_url": "https://y.example"}],
        [{"title": "Книга", "url": "https://b.example"}] * 5,
        reply_markup=leisure_ui.leisure_hub_kb(),
    )

    assert msg.text.startswith("🍿 Досуг\n\n🎫 Концерты\n• Muse")
    for title in ("🎟️ Премьеры кино", "📚 Новые книги", "«Дюна»", "«Книга»"):
        assert title in msg.text
    assert "игр" not in msg.text
    assert msg.text.count("«Книга»") == leisure_ui.LEISURE_HUB_LIMIT
    assert {entity.url for entity in msg.entities if entity.url} >= {
        "https://t.example", "https://y.example", "https://b.example",
    }
    assert _labels(msg.reply_markup) == [
        ["🎬 Подобрать кино"], ["📚 Подобрать книгу"],
        ["🎧 Подобрать музыку"], ["#️⃣ Главная", "🎚️ Настроить"],
    ]


def test_hub_hides_empty_blocks():
    only_books = leisure_ui.leisure_hub_screen([], [], [{"title": "Книга"}])
    empty = leisure_ui.leisure_hub_screen([], [], [])

    assert "📚 Новые книги" in only_books.text
    for title in ("🎫 Концерты", "🎟️ Премьеры кино"):
        assert title not in only_books.text
        assert title not in empty.text
    assert empty.text.startswith("🍿 Досуг\n\n")


def test_hub_open_reads_caches_without_network_or_ai(monkeypatch):
    _memory_store(monkeypatch)
    _seed_caches(monkeypatch)
    for module, name in (
        (leisure_concerts, "_fetch_concerts"), (leisure_concerts, "refresh_concerts_cache"),
        (leisure_movies.tmdb, "get_now_playing"), (leisure_movies.tmdb, "get_upcoming_theatrical_releases"),
        (leisure_books.google_books, "search_new_releases"),
    ):
        monkeypatch.setattr(module, name, _boom)
    bot = RecordingBot()

    asyncio.run(leisure_hub.send_hub(bot, "42"))

    text = bot.sent[0]["text"]
    for value in ("Muse", "«Дюна»", "«Новая книга»"):
        assert value in text
    # Раздела игр больше нет.
    assert "Новые игры" not in text and "Подобрать игру" not in str(bot.sent[0]["reply_markup"])
    assert bot.sent[0]["disable_web_page_preview"] is True


def test_hub_is_ready_only_with_fresh_caches_and_movie_card(monkeypatch):
    _memory_store(monkeypatch)
    assert home_cache.is_ready("leisure", "42") is False

    _seed_caches(monkeypatch)
    monkeypatch.setattr(leisure_movies, "_cached_movie", lambda _cid: ({"title": "A"}, {}))

    assert home_cache.is_ready("leisure", "42") is True


def test_warm_runs_every_step_and_reports_a_failed_one(monkeypatch):
    calls = []

    def step(name, fail=False):
        async def call(*_args, **_kwargs):
            calls.append(name)
            if fail:
                raise RuntimeError("down")
            return []
        return call

    monkeypatch.setattr(leisure_hub, "_warm_concerts", step("concerts", fail=True))
    monkeypatch.setattr(leisure_hub, "_warm_movie_premieres", step("movies"))
    monkeypatch.setattr(leisure_books, "get_book_premieres", step("books"))
    monkeypatch.setattr(leisure_movies, "get_current_movie", step("movie_reco"))

    assert asyncio.run(leisure_hub.warm_hub_cache("42")) is False
    assert calls == ["concerts", "movies", "books", "movie_reco"]


def test_premieres_and_library_submenus():
    premieres = leisure_ui.leisure_premieres_menu()
    library = leisure_ui.leisure_library_menu()

    assert _labels(premieres.reply_markup) == [
        ["🎟️ Премьеры кино"], ["🆕 Премьеры книг"], ["🎫 Концерты"], ["⬅️ Назад", "#️⃣ Главная"],
    ]
    assert _callbacks(premieres.reply_markup) == [
        "movie_premieres", "book_premieres", "a_concerts_find", "m_leisure", "m_menu",
    ]
    assert _labels(library.reply_markup) == [
        ["🎬 Кино"], ["📚 Книги"], ["🎧 Музыка"], ["🎫 Концерты"], ["⬅️ Назад", "#️⃣ Главная"],
    ]
    assert _callbacks(library.reply_markup) == [
        "movie_favorites", "book_favorites", "artist_favorites", "a_concerts_find", "m_leisure", "m_menu",
    ]
    for collection in ("cinema_favorites", "books_favorites", "music_favorite_artists"):
        assert cleanup.COLLECTIONS[collection]["back"] == "lz_lib"


def test_every_hub_and_submenu_button_has_a_route():
    markups = (
        leisure_ui.leisure_hub_kb(),
        leisure_ui.leisure_premieres_menu().reply_markup,
        leisure_ui.leisure_library_menu().reply_markup,
    )
    for data in {data for markup in markups for data in _callbacks(markup)}:
        assert len(data.encode()) <= 64
        assert routing.resolve_callback_handler(data)["handled"], data


def _dispatch(monkeypatch, data, module, name):
    calls = []

    async def handler(*args, **kwargs):
        calls.append((args, kwargs))
        status = kwargs.get("status")
        if status is not None:
            await status.replace("ok")

    class Status:
        async def replace(self, *_args, **_kwargs):
            return None

        async def stop(self, delete=True):
            return None

    async def start_inline(*_args, **_kwargs):
        return Status()

    monkeypatch.setattr(module, name, handler)
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    query = type("Query", (), {
        "data": data, "message": type("Message", (), {"chat_id": "42", "message_id": 7})(),
    })()
    update = type("Update", (), {"callback_query": query})()
    asyncio.run(bot_callbacks.handle(update, type("Context", (), {"bot": object()})(), None))
    return calls


@pytest.mark.parametrize("data, module, name", [
    ("m_leisure", leisure_hub, "send_hub"),
    ("lz_prem", leisure_hub, "send_premieres_menu"),
    ("lz_lib", leisure_hub, "send_library_menu"),
    ("movie_reco", leisure_movies, "send_current_movie"),
    ("movie_next", leisure_movies, "send_recos"),
    ("book_reco", leisure_books, "send_books_reco"),
    ("book_next", leisure_books, "_advance_book"),
    ("music_reco", bot_callbacks.leisure_music, "send_listen"),
    ("music_next", bot_callbacks.leisure_music, "listen_next"),
    # Старые кнопки игр из истории чата открывают хаб.
    ("vg_reco", leisure_hub, "send_hub"),
    ("vg_premieres", leisure_hub, "send_hub"),
    ("nov_game", leisure_hub, "send_hub"),
])
def test_hub_buttons_route_to_existing_flows(monkeypatch, data, module, name):
    assert len(_dispatch(monkeypatch, data, module, name)) == 1


@pytest.mark.parametrize("data", [
    "m_movie", "m_music", "m_books", "m_games", "movie_now_playing", "a_watch", "a_read", "a_listen",
])
def test_old_section_callbacks_open_the_hub(monkeypatch, data):
    assert len(_dispatch(monkeypatch, data, leisure_hub, "send_hub")) == 1


def test_what_to_watch_opens_cached_daily_card_without_new_pick(monkeypatch):
    sent = []

    async def card(_bot, _cid, it, _i, tm=None, status=None):
        sent.append((it["title"], tm["name"]))

    monkeypatch.setattr(leisure_movies, "_cached_movie", lambda _cid: ({"title": "Дюна"}, {"name": "Dune"}))
    monkeypatch.setattr(leisure_movies, "send_recos", _boom)
    monkeypatch.setattr(leisure_movies, "_send_movie_card", card)
    monkeypatch.setattr(leisure_movies.movie_engine, "mark_shown", lambda *_args: None)

    asyncio.run(leisure_movies.send_current_movie(object(), "42"))

    assert sent == [("Дюна", "Dune")]
