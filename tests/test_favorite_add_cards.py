import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_movies
import leisure_music
import personal_collections


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
