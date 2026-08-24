import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_movies
import leisure_music
import leisure_games
import leisure_books
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


def test_book_add_prompt_says_author_and_year_are_optional():
    bot = _Bot()

    asyncio.run(personal_collections.love_add_start(bot, "42", "books"))

    assert bot.messages[0]["text"] == (
        "Напиши название книги. Автора и год можно не указывать — покажу варианты.\n\n"
        "Например: Марсианин"
    )
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
    assert _labels(message["reply_markup"])[0] == ["🎚️ Моё кино"]


def test_manual_book_add_shows_one_verified_card_before_saving(monkeypatch):
    added = []
    monkeypatch.setattr(personal_collections.store, "add_to_list", lambda *_args: added.append(_args))

    async def analyze(_text):
        return {"title": "Марсианин", "alternative_title": "The Martian",
                "author": "", "year": "2011"}

    async def find(_query):
        return [{
            "title": "Марсианин", "value": "Марсианин", "author": "Энди Вейер",
            "year": "2011", "categories": ["Science Fiction"],
            "description": "Инженер выживает на Марсе.",
            "cover_url": "https://images.test/martian.jpg",
        }]

    leisure_books._manual_book_choices.clear()
    monkeypatch.setattr(leisure_books, "_analyze_manual_book_query", analyze)
    monkeypatch.setattr(leisure_books, "_find_manual_book_candidates", find)
    bot = _Bot()

    asyncio.run(personal_collections.love_add_done(bot, "42", "books", "Марсианин 2011"))

    assert added == []
    message = bot.messages[0]
    assert message["photo"] == "https://images.test/martian.jpg"
    assert "Марсианин" in message["caption"]
    assert "Энди Вейер · 2011" in message["caption"]
    assert _labels(message["reply_markup"]) == [["✅ Добавить", "❌ Другая"]]


def test_manual_book_add_accepts_bare_title_even_from_existing_choice(monkeypatch):
    async def analyze(text):
        assert text == "Марсианин"
        return {"title": text, "alternative_title": "The Martian",
                "author": "", "year": ""}

    async def find(query):
        assert query["author"] == ""
        assert query["year"] == ""
        return [{
            "title": "Марсианин", "value": "Марсианин", "author": "Энди Вейер",
            "year": "2011", "cover_url": "https://images.test/martian.jpg",
        }]

    leisure_books._manual_book_choices.clear()
    monkeypatch.setattr(leisure_books, "_analyze_manual_book_query", analyze)
    monkeypatch.setattr(leisure_books, "_find_manual_book_candidates", find)
    bot = _Bot()

    asyncio.run(personal_collections.love_add_done(
        bot, "42", "books", "Марсианин", confirmed=True,
    ))

    assert bot.messages[0]["photo"] == "https://images.test/martian.jpg"
    assert _labels(bot.messages[0]["reply_markup"]) == [["✅ Добавить", "❌ Другая"]]


def test_manual_book_other_edits_card_to_next_author_without_saving(monkeypatch):
    token = "booknext"
    leisure_books._manual_book_choices[token] = {
        "cid": "42", "created_at": leisure_books.time.time(), "choices": [
            {"title": "Марсианин", "author": "Энди Вейер", "year": "2011",
             "cover_url": "https://images.test/weir.jpg"},
            {"title": "Марсианин", "author": "Джордж Дюморье", "year": "1897",
             "cover_url": "https://images.test/dumaurier.jpg"},
        ],
    }
    added = []
    monkeypatch.setattr(leisure_books.store, "add_to_list", lambda *_args: added.append(_args))
    query = _Query()

    asyncio.run(leisure_books.handle_manual_book_add_callback(
        _Bot(), "42", query, f"book_add_next:{token}:0",
    ))

    assert added == []
    assert query.edits[0]["media"].media == "https://images.test/dumaurier.jpg"
    assert "Джордж Дюморье · 1897" in query.edits[0]["media"].caption
    assert _labels(query.edits[0]["reply_markup"]) == [["✅ Добавить", "❌ Другая"]]


def test_manual_book_candidates_keep_one_best_edition_per_author(monkeypatch):
    monkeypatch.setattr(leisure_books.google_books, "find_volumes", lambda *_args, **_kwargs: [
        {"title": "Марсианин", "author": "Энди Вейер", "year": "2011",
         "cover_url": "https://images.test/weir-popular.jpg"},
        {"title": "The Martian", "author": "Andy Weir", "year": "2014",
         "cover_url": "https://images.test/weir-edition.jpg"},
        {"title": "Марсианин", "author": "Джордж Дюморье", "year": "1897",
         "cover_url": "https://images.test/dumaurier.jpg"},
    ])

    choices = asyncio.run(leisure_books._find_manual_book_candidates({
        "title": "Марсианин", "alternative_title": "", "author": "", "year": "2011",
    }))

    assert [item["author"] for item in choices] == ["Энди Вейер", "Джордж Дюморье"]
    assert choices[0]["cover_url"] == "https://images.test/weir-popular.jpg"


def test_manual_book_candidates_fall_back_for_flowers_for_algernon(monkeypatch):
    monkeypatch.setattr(leisure_books.google_books, "find_volumes", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        leisure_books.open_library, "search_books",
        lambda title, **_kwargs: [{
            "title": "Цветы для Элджернона", "author": "Дэниел Киз",
            "authors": ["Дэниел Киз"], "year": "1959",
            "cover_url": "https://covers.openlibrary.org/b/id/123-L.jpg",
        }] if title == "Цветы для Элджернона" else [],
        raising=False,
    )

    choices = asyncio.run(leisure_books._find_manual_book_candidates({
        "title": "Цветы для Элджернона", "alternative_title": "Flowers for Algernon",
        "author": "", "year": "",
    }))

    assert choices[0]["title"] == "Цветы для Элджернона"
    assert choices[0]["author"] == "Дэниел Киз"


def test_manual_book_candidate_determines_missing_genre_with_ai(monkeypatch):
    monkeypatch.setattr(leisure_books.google_books, "find_volumes", lambda *_args, **_kwargs: [{
        "title": "Цветы для Элджернона", "author": "Дэниел Киз",
        "year": "1959", "description": "Научный эксперимент меняет интеллект героя.",
        "cover_url": "https://images.test/algernon.jpg",
    }])

    async def genre_ai(*_args, **kwargs):
        assert kwargs["tier"] == "leisure"
        assert kwargs["module"] == "leisure_collection_add"
        return {"items": [{"id": 0, "genre": "Фантастика"}]}

    monkeypatch.setattr(leisure_books.ai, "allm_json", genre_ai)

    choices = asyncio.run(leisure_books._find_manual_book_candidates({
        "title": "Цветы для Элджернона", "alternative_title": "Flowers for Algernon",
        "author": "", "year": "",
    }))

    assert choices[0]["genre_label"] == "Фантастика"
    assert leisure_books._favorite_book_genre(choices[0]) == "Фантастика"


def test_manual_book_candidate_translates_description_to_russian(monkeypatch):
    monkeypatch.setattr(leisure_books.google_books, "find_volumes", lambda *_args, **_kwargs: [{
        "title": "Flowers for Algernon", "author": "Daniel Keyes", "year": "1959",
        "categories": ["Science Fiction"],
        "description": "A scientific experiment dramatically changes Charlie's intelligence.",
        "cover_url": "https://images.test/algernon.jpg",
    }])

    async def metadata_ai(*_args, **_kwargs):
        return {"items": [{
            "id": 0, "genre": "Фантастика",
            "description_ru": "Научный эксперимент резко меняет интеллект Чарли.",
        }]}

    monkeypatch.setattr(leisure_books.ai, "allm_json", metadata_ai)

    choices = asyncio.run(leisure_books._find_manual_book_candidates({
        "title": "Цветы для Элджернона", "alternative_title": "Flowers for Algernon",
        "author": "", "year": "",
    }))

    assert choices[0]["description"] == "Научный эксперимент резко меняет интеллект Чарли."
    assert choices[0]["cover_url"] == "https://images.test/algernon.jpg"


def test_manual_book_candidate_without_cover_is_not_offered(monkeypatch):
    monkeypatch.setattr(leisure_books.google_books, "find_volumes", lambda *_args, **_kwargs: [{
        "title": "Книга без обложки", "author": "Автор", "year": "2020",
        "description": "Описание на русском.", "cover_url": "",
    }])
    monkeypatch.setattr(leisure_books.open_library, "search_books", lambda *_args, **_kwargs: [])

    choices = asyncio.run(leisure_books._find_manual_book_candidates({
        "title": "Книга без обложки", "alternative_title": "", "author": "", "year": "",
    }))

    assert choices == []


def test_manual_book_candidate_keeps_the_verified_catalog_title(monkeypatch):
    monkeypatch.setattr(leisure_books.google_books, "find_volumes", lambda *_args, **_kwargs: [{
        "title": "The Martian", "author": "Andy Weir", "authors": ["Andy Weir"],
        "year": "2011", "cover_url": "https://images.test/weir.jpg",
    }])

    choices = asyncio.run(leisure_books._find_manual_book_candidates({
        "title": "Марсианин", "alternative_title": "The Martian",
        "author": "", "year": "2011",
    }))

    assert choices[0]["title"] == "The Martian"
    assert choices[0]["value"] == "The Martian"


def test_manual_book_other_invalidates_old_buttons_when_media_edit_fails():
    token = "bookeditfail"
    leisure_books._manual_book_choices[token] = {
        "cid": "42", "created_at": leisure_books.time.time(), "current_index": 0,
        "choices": [
            {"title": "Марсианин", "author": "Энди Вейер", "year": "2011",
             "cover_url": "https://images.test/weir.jpg"},
            {"title": "Марсианин", "author": "Другой Автор", "year": "2020",
             "cover_url": "https://images.test/other.jpg"},
        ],
    }
    bot = _Bot()

    asyncio.run(leisure_books.handle_manual_book_add_callback(
        bot, "42", _FailingQuery(), f"book_add_next:{token}:0",
    ))

    assert token not in leisure_books._manual_book_choices
    assert bot.messages[0]["photo"] == "https://images.test/other.jpg"
    callback = bot.messages[0]["reply_markup"].inline_keyboard[0][0].callback_data
    new_token = callback.split(":", 2)[1]
    assert new_token != token
    assert leisure_books._manual_book_choices[new_token]["current_index"] == 1
    leisure_books._manual_book_choices.pop(new_token, None)


def test_manual_book_add_button_saves_only_shown_card(monkeypatch):
    token = "booksave"
    previous = {
        "title": "Марсианин", "value": "Марсианин", "author": "Другой Автор",
        "year": "2010", "cover_url": "https://images.test/previous.jpg",
    }
    chosen = {
        "title": "Марсианин", "value": "Марсианин", "author": "Энди Вейер",
        "year": "2011", "cover_url": "https://images.test/weir.jpg",
        "genre_label": "Фантастика",
    }
    leisure_books._manual_book_choices[token] = {
        "cid": "42", "created_at": leisure_books.time.time(),
        "current_index": 1, "choices": [previous, chosen],
    }
    added = []
    monkeypatch.setattr(leisure_books.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(
        leisure_books.store, "add_to_list",
        lambda _key, _cid, value: added.append(value),
    )
    query = _Query()

    asyncio.run(leisure_books.handle_manual_book_add_callback(
        # Даже устаревшая кнопка первой карточки сохраняет реально показанную вторую.
        _Bot(), "42", query, f"book_add_ok:{token}:0",
    ))

    assert added == [chosen]
    assert token not in leisure_books._manual_book_choices
    assert "✅ Добавлена в «🎚️ Мои книги»" in query.edits[0]["media"].caption


def test_manual_book_add_upgrades_legacy_title_to_structured_card(monkeypatch):
    token = "booklegacy"
    chosen = {
        "title": "Марсианин", "value": "Марсианин", "author": "Энди Вейер",
        "year": "2011", "cover_url": "https://images.test/weir.jpg",
    }
    leisure_books._manual_book_choices[token] = {
        "cid": "42", "created_at": leisure_books.time.time(),
        "current_index": 0, "choices": [chosen],
    }
    saved = []
    added = []
    monkeypatch.setattr(leisure_books.store, "get_list", lambda *_args: ["Марсианин"])
    monkeypatch.setattr(
        leisure_books.store, "set_list",
        lambda _key, _cid, values: saved.extend(values),
    )
    monkeypatch.setattr(
        leisure_books.store, "add_to_list", lambda *_args: added.append(_args),
    )
    query = _Query()

    asyncio.run(leisure_books.handle_manual_book_add_callback(
        _Bot(), "42", query, f"book_add_ok:{token}:0",
    ))

    assert added == []
    assert saved[0]["author"] == "Энди Вейер"
    assert saved[0]["cover_url"] == "https://images.test/weir.jpg"
    assert "✅ Добавлена в «🎚️ Мои книги»" in query.edits[0]["media"].caption


def test_manual_book_add_reports_an_existing_structured_book(monkeypatch):
    token = "bookduplicate"
    chosen = {
        "title": "Марсианин", "value": "Марсианин", "author": "Энди Вейер",
        "year": "2011", "cover_url": "https://images.test/weir.jpg",
    }
    leisure_books._manual_book_choices[token] = {
        "cid": "42", "created_at": leisure_books.time.time(),
        "current_index": 0, "choices": [chosen],
    }
    added = []
    monkeypatch.setattr(leisure_books.store, "get_list", lambda *_args: [chosen])
    monkeypatch.setattr(
        leisure_books.store, "add_to_list", lambda *_args: added.append(_args),
    )
    query = _Query()

    asyncio.run(leisure_books.handle_manual_book_add_callback(
        _Bot(), "42", query, f"book_add_ok:{token}:0",
    ))

    assert added == []
    assert "✅ Уже в «🎚️ Мои книги»" in query.edits[0]["media"].caption


def test_manual_book_other_reopens_input_when_variants_are_exhausted():
    token = "booklast"
    leisure_books._manual_book_choices[token] = {
        "cid": "42", "origin": "leisure", "created_at": leisure_books.time.time(),
        "choices": [{
            "title": "Марсианин", "author": "Энди Вейер", "year": "2011",
            "cover_url": "https://images.test/weir.jpg",
        }],
    }
    leisure_books.store.pending_input.pop("42", None)
    bot = _Bot()

    asyncio.run(leisure_books.handle_manual_book_add_callback(
        bot, "42", _Query(), f"book_add_next:{token}:0",
    ))

    assert leisure_books.store.pending_input["42"] == "loveaddls_books"
    assert bot.messages[0]["text"] == (
        "Других подтверждённых вариантов не нашлось. "
        "Можно уточнить автора или год либо написать другое название."
    )
    leisure_books.store.pending_input.pop("42", None)


def test_manual_book_add_saves_verified_metadata_and_shows_full_card(monkeypatch):
    added = []
    monkeypatch.setattr(personal_collections, "_love_items", lambda _cid, _key: [])
    monkeypatch.setattr(
        personal_collections.store, "add_to_list",
        lambda _key, _cid, value: added.append(value),
    )
    item = {
        "title": "Дюна", "author": "Фрэнк Герберт", "year": "1965",
        "categories": ["Science Fiction"], "description": "История борьбы за Арракис.",
        "cover_url": "https://images.test/dune.jpg",
        "info_link": "https://books.google.test/dune",
    }

    async def analyze(_text):
        return {"title": "Дюна", "alternative_title": "Dune",
                "author": "Фрэнк Герберт", "year": ""}

    async def find(_query):
        return [leisure_books._manual_book_candidate(item)]

    leisure_books._manual_book_choices.clear()
    monkeypatch.setattr(leisure_books, "_analyze_manual_book_query", analyze)
    monkeypatch.setattr(leisure_books, "_find_manual_book_candidates", find)
    monkeypatch.setattr(leisure_books.store, "get_list", lambda *_args: [])
    bot = _Bot()

    asyncio.run(personal_collections.love_add_done(
        bot, "42", "books", "Дюна — Фрэнк Герберт", confirmed=True,
    ))

    assert added == []
    callback = bot.messages[0]["reply_markup"].inline_keyboard[0][0].callback_data
    query = _Query()
    asyncio.run(leisure_books.handle_manual_book_add_callback(
        bot, "42", query, callback,
    ))

    assert added[0]["author"] == "Фрэнк Герберт"
    assert added[0]["year"] == "1965"
    assert added[0]["categories"] == ["Science Fiction"]
    assert bot.messages[0]["photo"] == "https://images.test/dune.jpg"
    message = query.edits[0]["media"]
    assert "✅ Добавлена в «🎚️ Мои книги»" in message.caption
    assert "📚 Дюна · 1965 · Фантастика" in message.caption
    assert "Автор: Фрэнк Герберт" in message.caption
    assert "История борьбы за Арракис." in message.caption


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


def test_manual_book_query_uses_premium_ai_and_keeps_explicit_year(monkeypatch):
    captured = {}

    async def analyze(_prompt, _tokens, **kwargs):
        captured.update(kwargs)
        return {
            "title": "Марсианин", "alternative_title": "The Martian",
            "author": "", "year": "2015",
        }

    monkeypatch.setattr(leisure_books.ai, "allm_json", analyze)

    query = asyncio.run(leisure_books._analyze_manual_book_query("Марсианин 2011"))

    assert query == {
        "title": "Марсианин", "alternative_title": "The Martian",
        "author": "", "year": "2011",
    }
    assert captured["tier"] == "leisure"
    assert captured["module"] == "leisure_collection_add"


def test_manual_book_query_drops_ai_author_and_year_missing_from_input(monkeypatch):
    async def analyze(_prompt, _tokens, **_kwargs):
        return {
            "title": "Марсианин", "alternative_title": "The Martian",
            "author": "Энди Вейер", "year": "2011",
        }

    monkeypatch.setattr(leisure_books.ai, "allm_json", analyze)

    query = asyncio.run(leisure_books._analyze_manual_book_query("Марсианин"))

    assert query["title"] == "Марсианин"
    assert query["alternative_title"] == "The Martian"
    assert query["author"] == ""
    assert query["year"] == ""


def test_manual_book_query_accepts_transliterated_author_present_in_input(monkeypatch):
    async def analyze(_prompt, _tokens, **_kwargs):
        return {
            "title": "Марсианин", "alternative_title": "The Martian",
            "author": "Andy Weir", "year": "",
        }

    monkeypatch.setattr(leisure_books.ai, "allm_json", analyze)

    query = asyncio.run(leisure_books._analyze_manual_book_query(
        "Марсианин Энди Вейер",
    ))

    assert query["author"] == "Andy Weir"


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
        _Bot(), "42", "games", "Unknown Adventure", confirmed=True,
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
