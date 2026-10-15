import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_movies
import movie_recommendation
import leisure_music
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

    async def send_photo(self, **kwargs):
        self.messages.append(kwargs)


class _Query:
    def __init__(self):
        self.edits = []

    async def edit_message_media(self, **kwargs):
        self.edits.append(kwargs)


class _FailingQuery:
    async def edit_message_media(self, **_kwargs):
        raise RuntimeError("media edit failed")


def test_movie_add_prompt_names_movie_collection():
    bot = _Bot()

    asyncio.run(personal_collections.love_add_start(bot, "42", "movies"))

    assert bot.messages[0]["text"] == "Напиши фильм или сериал — добавлю в 🎚️ Моё кино."
    personal_collections.store.pending_input.pop("42", None)


def test_movie_recommendation_add_does_not_fail_when_markup_is_unchanged(monkeypatch):
    class Query:
        class Message:
            async def edit_reply_markup(self, **_kwargs):
                raise RuntimeError("message is not modified")

        message = Message()

    monkeypatch.setattr(leisure_movies.store, "last_recos", {
        "42": {"kind": "movie", "items": ["Патерсон (фильм, 2016)"]},
    })
    monkeypatch.setattr(leisure_movies.store, "get_list", lambda *_args: [])
    added = []
    monkeypatch.setattr(
        leisure_movies.store, "add_to_list", lambda *args: added.append(args),
    )
    monkeypatch.setattr(
        movie_recommendation, "normalize_movie_items",
        lambda items: list(items),
    )
    advanced = []

    async def advance(bot, cid):
        advanced.append((bot, cid))

    monkeypatch.setattr(leisure_movies, "_advance_movie", advance)
    monkeypatch.setattr(movie_recommendation, "_advance_movie", advance)

    asyncio.run(leisure_movies.movie_love(object(), "42", 0, Query()))

    assert added == [(config.FAVORITE_MOVIES_KEY, "42", "Патерсон (фильм, 2016)")]
    assert advanced and advanced[0][1] == "42"


def test_artist_add_prompt_names_its_collection():
    for key, expected in (
        ("artists", "Напиши артиста — добавлю в 🎚️ Мои артисты."),
    ):
        bot = _Bot()
        asyncio.run(personal_collections.love_add_start(bot, "42", key))
        assert bot.messages[0]["text"] == expected
        personal_collections.store.pending_input.pop("42", None)


def test_manual_artist_add_shows_a_card_and_collection_link(monkeypatch):
    monkeypatch.setattr(leisure_music, "_cached_artist", lambda _cid: None)
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie", "rock"])
    bot = _Bot()

    asyncio.run(leisure_music.send_favorite_artists_added_card(bot, "42", ["The National"]))

    message = bot.messages[0]
    assert message["text"] == (
        "✅ Добавлен в «🎚️ Мои артисты»\n\n"
        "🎸 The National\n\n"
        "Учту в подборках: Инди · Рок"
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
    labels = _labels(message["reply_markup"])
    assert ["🎚️ Моё кино"] not in labels
    assert labels[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert message["reply_markup"].inline_keyboard[-1][0].callback_data == "movie_favorites"


def test_collection_choice_saves_only_the_selected_candidate(monkeypatch):
    selected = []
    token = "choice123"
    personal_collections._add_choices[token] = {
        "cid": "42", "key": "movies", "origin": "base",
        "created_at": personal_collections.time.time(),
        "choices": [
            {"value": "Марсианин (фильм, 2015)", "label": "Фильм"},
            {"value": "Марсианин (сериал, 2020)", "label": "Сериал"},
        ],
    }

    async def save(_bot, cid, key, value, origin="base", *, confirmed=False):
        selected.append((cid, key, value, origin, confirmed))

    monkeypatch.setattr(personal_collections, "love_add_done", save)

    asyncio.run(personal_collections.confirm_collection_choice(
        _Bot(), "42", None, token, 0,
    ))

    assert selected == [
        ("42", "movies", "Марсианин (фильм, 2015)", "base", True),
    ]


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

    asyncio.run(personal_collections.love_add_done(
        _Bot(), "42", "artists", "The National", confirmed=True,
    ))

    assert added == ["The National"]
    assert cards == [["The National"]]


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


