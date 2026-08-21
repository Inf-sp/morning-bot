import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_movies
import leisure_music
import leisure_games
import leisure_collection
import personal_collections
import config


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


class _Bot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


def test_manual_artist_add_shows_a_card_and_collection_link(monkeypatch):
    monkeypatch.setattr(leisure_music, "_cached_artist", lambda _cid: None)
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie", "rock"])
    bot = _Bot()

    asyncio.run(leisure_music.send_favorite_artists_added_card(bot, "42", ["The National"]))

    message = bot.messages[0]
    assert message["text"] == (
        "✅ Добавлен в «🎚️ Мои артисты»\n\n"
        "🎸 The National\n\n"
        "Учту в подборках: 🌿 Инди · 🎸 Рок"
    )
    assert _labels(message["reply_markup"]) == [
        ["🎚️ Мои артисты"], ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_manual_movie_add_uses_verified_metadata_when_available(monkeypatch):
    monkeypatch.setattr(leisure_movies.config, "TMDB_API_KEY", "test-key")
    monkeypatch.setattr(leisure_movies.tmdb, "lookup_title", lambda _title: {
        "name": "Прибытие", "year": "2016", "kind": "movie", "genres": "фантастика, драма",
    })
    bot = _Bot()

    asyncio.run(leisure_movies.send_favorite_movies_added_card(bot, "42", ["Arrival"]))

    message = bot.messages[0]
    assert "✅ Добавлен в «🎚️ Моё кино»" in message["text"]
    assert "🎬 Прибытие · 2016 · Фильм · фантастика, драма" in message["text"]
    assert _labels(message["reply_markup"])[0] == ["🎚️ Моё кино"]


def test_manual_collection_add_routes_to_artist_card(monkeypatch):
    added = []
    cards = []
    monkeypatch.setattr(personal_collections, "_love_items", lambda _cid, _key: [])
    monkeypatch.setattr(personal_collections.store, "add_to_list", lambda _key, _cid, value: added.append(value))

    import leisure_concerts

    monkeypatch.setattr(leisure_concerts, "invalidate_user_concerts_cache", lambda _cid: None)
    monkeypatch.setattr(leisure_music, "_kick_off_new_artist_concert_check", lambda _cid, _artists: None)

    async def send_card(_bot, _cid, artists):
        cards.append(artists)

    monkeypatch.setattr(leisure_music, "send_favorite_artists_added_card", send_card)

    asyncio.run(personal_collections.love_add_done(_Bot(), "42", "artists", "The National"))

    assert added == ["The National"]
    assert cards == [["The National"]]


def test_manual_game_add_saves_detected_genre_and_platform(monkeypatch):
    added = []
    monkeypatch.setattr(personal_collections, "_love_items", lambda _cid, _key: [])
    monkeypatch.setattr(
        personal_collections.store, "add_to_list",
        lambda _key, _cid, value: added.append(value),
    )
    monkeypatch.setattr(
        leisure_games.igdb,
        "enrich_game_recommendation",
        lambda item: {
            **item,
            "genres": ["adventure"],
            "platforms": ["ps5"],
            "year": 2025,
        },
    )
    monkeypatch.setattr(leisure_games, "_reset_game_daily", lambda _cid: None)

    async def send_card(_bot, _cid, _items):
        return None

    monkeypatch.setattr(leisure_games, "send_favorite_games_added_card", send_card)

    asyncio.run(personal_collections.love_add_done(
        _Bot(), "42", "games", "Unknown Adventure",
    ))

    assert added[0]["genres"] == ["adventure"]
    assert added[0]["platforms"] == ["ps5"]


def test_collection_migration_uses_a_plain_russian_movie_label(monkeypatch):
    """Старые названия кино приводятся к формату без эмодзи и разметки."""
    items = ["🎬 **Укрытие (2023)**"]

    monkeypatch.setattr(
        leisure_collection,
        "_resolve_movie_label",
        lambda _title: {"name": "Укрытие", "kind": "tv", "year": "2023"},
    )

    assert leisure_collection.normalize_movie_items(items) == ["Укрытие (сериал, 2023)"]


def test_collection_migration_updates_saved_movie_list(monkeypatch):
    stored = {
        config.FAVORITE_MOVIES_KEY: {"42": ["🎬 **Укрытие (2023)**"]},
        config.FAVORITE_BOOKS_KEY: {},
        config.FAVORITE_ARTISTS_KEY: {},
    }
    saved = {}
    monkeypatch.setattr(leisure_collection.store, "_load", lambda key: stored[key])
    monkeypatch.setattr(leisure_collection.store, "_save", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(
        leisure_collection,
        "_resolve_movie_label",
        lambda _title: {"name": "Укрытие", "kind": "tv", "year": "2023"},
    )

    assert leisure_collection.normalize_favorite_collections(resolve_movies=True) is True
    assert saved[config.FAVORITE_MOVIES_KEY]["42"] == ["Укрытие (сериал, 2023)"]
