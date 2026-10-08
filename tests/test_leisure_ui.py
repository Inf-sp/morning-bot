import asyncio
import json
import os
from datetime import date, datetime, timedelta

from telegram import MessageEntity

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import cleanup
import config
import leisure_books
import leisure_movies
import movie_discovery
import movie_recommendation
import leisure_music
import movie_engine
import settings
from fakes import RecordingBot


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _bold_values(message):
    encoded = message.text.encode("utf-16-le")
    return {
        encoded[entity.offset * 2:(entity.offset + entity.length) * 2].decode("utf-16-le")
        for entity in message.entities if entity.type == MessageEntity.BOLD
    }


def test_recommendation_cards_have_no_refresh_label():
    assert "✨ Обновить" not in sum(_labels(leisure_movies._movie_kb(0)), [])
    assert "✨ Обновить" not in sum(_labels(leisure_books._book_kb(0)), [])
    assert "✨ Обновить" not in sum(_labels(leisure_music._listen_kb()), [])
    # «✨ Другой …» открывает выбор жанра под карточкой; кнопки «Выбрать жанр» нет.
    for keyboard, other, callback in (
        (leisure_movies._movie_kb(0), "✨ Другой фильм", "movie_pick_0"),
        (leisure_books._book_kb(0), "✨ Другая книга", "book_pick_0"),
        (leisure_music._listen_kb(), "✨ Другой артист", "music_pick"),
    ):
        first = keyboard.inline_keyboard[0][0]
        assert (first.text, first.callback_data) == (other, callback)
        assert all("жанр" not in b.text for row in keyboard.inline_keyboard for b in row)
        assert keyboard.inline_keyboard[-1][0].callback_data == "m_leisure"
    # «Не нравится» — красная, последней перед навигацией, вместо «Добавить в Моё…».
    for keyboard, callback in (
        (leisure_movies._movie_kb(3), "movie_no_3"),
        (leisure_books._book_kb(3), "book_no_3"),
        (leisure_music._listen_kb(), "listen_no"),
    ):
        dislike = keyboard.inline_keyboard[-3][0]
        assert (dislike.text, dislike.callback_data, dislike.api_kwargs) == (
            "Не нравится", callback, {"style": "danger"},
        )
        # Под «Не нравится» — синяя «Настроить» своего раздела, затем «Назад | Главная».
        assert keyboard.inline_keyboard[-2][0].text == "🎚️ Настроить"
        assert keyboard.inline_keyboard[-2][0].callback_data.startswith("lz_cfg_")
        assert all("Добавить в Мо" not in b.text for row in keyboard.inline_keyboard for b in row)
    assert _labels(leisure_movies._movie_kb(0))[-1] == ["⬅️ Назад", "#️⃣ Главная"]


def test_preferences_are_available_in_personal_content_lists():
    assert _labels(leisure_movies._movie_prefs_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert cleanup.COLLECTIONS["cinema_favorites"]["menu_button"] == ("📝 Выбрать предпочтения", "movie_prefs")
    assert cleanup.COLLECTIONS["cinema_favorites"]["add_button_at_bottom"] is False
    assert cleanup.COLLECTIONS["cinema_favorites"]["allow_edit"] is False
    assert _labels(leisure_books._book_preferences_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert cleanup.COLLECTIONS["books_favorites"]["menu_button"] == ("📝 Выбрать предпочтения", "book_prefs")
    assert cleanup.COLLECTIONS["books_favorites"]["add_button_at_bottom"] is False
    assert cleanup.COLLECTIONS["books_favorites"]["allow_edit"] is False
    assert _labels(leisure_music._music_preferences_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert cleanup.COLLECTIONS["music_favorite_artists"]["menu_button"] == ("📝 Выбрать предпочтения", "music_prefs")
    assert cleanup.COLLECTIONS["music_favorite_artists"]["add_button_at_bottom"] is False
    assert cleanup.COLLECTIONS["music_favorite_artists"]["allow_edit"] is False


def test_global_preferences_has_all_recommendation_sections():
    bot = RecordingBot()
    asyncio.run(settings.send_preferences(bot, "42"))

    assert _labels(bot.sent[0]["reply_markup"]) == [
        ["🧠 Обучение"], ["🥣 Кухни"], ["🧵 Стиль"], ["🎧 Музыка"],
        ["🎬 Кино"], ["📚 Книги"], ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_movie_preferences_keep_only_type_recency_and_rating(monkeypatch):
    monkeypatch.setattr(movie_recommendation.settings, "get", lambda *_args: "")

    rows = _labels(leisure_movies._movie_prefs_kb("42"))

    assert rows == [
        ["□ 🎬 Фильмы"],
        ["□ Сериалы"],
        ["□ Новинки"],
        ["✅ Любые годы"],
        ["□ ⭐️ 6.5"],
        ["□ ⭐️ 7.0"],
        ["□ ⭐️ 7.5"],
        ["□ ⭐️ 8.0"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]
    assert leisure_movies._movie_prefs("42") == {
        "type_pref": None,
        "recency": None,
        "min_rating": None,
    }


def test_movie_engine_ignores_retired_manual_country_and_genre_preferences():
    candidate = {
        "genre_ids": [1], "countries": ["NL"], "kind": "movie",
        "rating": 7.0, "vote_count": 100, "popularity": 10, "freq": 1,
    }
    taste = {"genres": {}, "countries": {}, "kind_pref": None}

    current = movie_engine._score(candidate, taste, {"type_pref": None})
    retired = movie_engine._score(
        candidate,
        taste,
        {"type_pref": None, "genres": [1], "countries": ["NL"]},
    )

    assert retired == current


def test_movie_recency_preference_prioritises_recent_releases():
    taste = {"genres": {}, "countries": {}, "kind_pref": None}
    common = {"genre_ids": [], "countries": [], "kind": "movie", "rating": 7.0,
              "vote_count": 100, "popularity": 10, "freq": 1}
    recent = {**common, "release_date": date.today().isoformat()}
    older = {**common, "release_date": (date.today() - timedelta(days=900)).isoformat()}

    assert movie_engine._score(recent, taste, {"recency": "new"}) > movie_engine._score(
        older, taste, {"recency": "new"}
    )


def test_movie_preferences_are_used_without_favourite_films(monkeypatch):
    requested = {}
    delivered = []

    async def deliver(_bot, _cid, item, _index, tm=None, **_kwargs):
        delivered.append((item, tm))

    def discover(kind, _genres, min_rating, year, page=1):
        requested.update(kind=kind, min_rating=min_rating, year=year)
        return [{
            "id": 7, "name": "Новый сериал", "kind": "tv", "rating": 8.2,
            "vote_count": 500, "popularity": 20, "release_date": date.today().isoformat(),
        }]

    monkeypatch.setattr(leisure_movies.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_movies.movie_engine, "_excluded_norms", lambda _cid, **_kwargs: set())
    monkeypatch.setattr(leisure_movies.movie_engine, "mark_shown", lambda *_args: None)
    monkeypatch.setattr(leisure_movies, "_movie_prefs", lambda _cid: {
        "type_pref": "tv", "recency": "new", "min_rating": 8.0,
    })
    monkeypatch.setattr(movie_recommendation, "_movie_prefs", lambda _cid: {
        "type_pref": "tv", "recency": "new", "min_rating": 8.0,
    })
    monkeypatch.setattr(leisure_movies.tmdb, "discover", discover)
    monkeypatch.setattr(leisure_movies, "_send_movie_card", deliver)

    asyncio.run(leisure_movies.send_recos(object(), "42", "movie"))

    assert requested == {"kind": "tv", "min_rating": 8.0, "year": 2000}
    assert delivered[0][0]["title"] == "Новый сериал"


def test_first_movie_recommendation_keeps_poster_with_inline_status():
    class Bot:
        def __init__(self):
            self.photos = []
            self.messages = []

        async def send_photo(self, **kwargs):
            self.photos.append(kwargs)

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

    class Status:
        def __init__(self):
            self.replacements = []

        async def replace(self, text, **kwargs):
            self.replacements.append((text, kwargs))

    bot = Bot()
    status = Status()
    tm = {
        "name": "Патерсон",
        "kind": "movie",
        "poster": "https://image.tmdb.org/paterson.jpg",
        "rating": 7.4,
        "vote_count": 500,
    }

    asyncio.run(leisure_movies._send_movie_card(
        bot, "42", {"title": "Патерсон"}, 0, tm=tm, status=status,
    ))

    assert bot.photos[0]["photo"] == tm["poster"]
    assert status.replacements == []
    assert bot.messages == []


def test_movie_recommendation_replaces_localized_poster_with_english_one(monkeypatch):
    class Bot:
        def __init__(self):
            self.photos = []

        async def send_photo(self, **kwargs):
            self.photos.append(kwargs)

    monkeypatch.setattr(
        leisure_movies.tmdb, "english_poster",
        lambda movie_id, kind: "https://image.tmdb.org/english.jpg",
    )
    bot = Bot()
    tm = {
        "id": 77,
        "name": "Arrival",
        "kind": "movie",
        "poster": "https://image.tmdb.org/localized.jpg",
        "rating": 7.9,
        "vote_count": 1_000,
    }

    asyncio.run(leisure_movies._send_movie_card(
        bot, "42", {"title": "Arrival"}, 0, tm=tm,
    ))

    assert bot.photos[0]["photo"] == "https://image.tmdb.org/english.jpg"


def test_movie_home_falls_back_when_tmdb_is_temporarily_unavailable(monkeypatch):
    monkeypatch.setattr(leisure_movies, "_cached_movie", lambda _cid: None)
    monkeypatch.setattr(leisure_movies.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_movies.movie_engine, "_excluded_norms", lambda _cid, **_kwargs: set())
    monkeypatch.setattr(leisure_movies, "_movie_prefs", lambda _cid: {})
    monkeypatch.setattr(movie_recommendation, "_movie_prefs", lambda _cid: {})
    monkeypatch.setattr(leisure_movies.config, "TMDB_API_KEY", "test-key")
    monkeypatch.setattr(
        leisure_movies.tmdb, "discover",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("tmdb unavailable")),
    )
    monkeypatch.setattr(leisure_movies, "_cache_movie", lambda *_args: None)

    item, tm = asyncio.run(leisure_movies.get_current_movie("42"))

    assert item["title"] == "Решение уйти"
    assert tm is None


def test_movie_cache_serializes_tmdb_anchors(monkeypatch):
    def persist(_key, mutate):
        data, _result = mutate({})
        json.dumps(data)

    monkeypatch.setattr(leisure_movies.store, "mutate_kv", persist)

    leisure_movies._cache_movie(
        "42",
        {"title": "Паразиты"},
        {"name": "Паразиты", "anchors": {"Олдбой"}},
    )


def test_leisure_preference_choices_use_one_column():
    keyboards = (
        leisure_movies._movie_prefs_kb("42"),
        leisure_books._book_preferences_kb("42"),
        leisure_music._music_preferences_kb("42"),
    )

    assert all(len(row) == 1 for keyboard in keyboards for row in _labels(keyboard)[:-1])


def test_book_preferences_filter_recommendations_by_recency_and_rating(monkeypatch):
    values = {"book_recency": "new", "book_min_rating": "4.5"}
    monkeypatch.setattr(leisure_books.settings, "get", lambda _cid, key, default=None: values.get(key, default))
    monkeypatch.setattr(leisure_books, "_book_used", lambda _cid: set())
    current_year = datetime.now(config.TZ).year
    items = [
        {"title": "Старая высокая", "year": str(current_year - 3), "rating": 4.9, "ratings_count": 100},
        {"title": "Новая ниже", "year": str(current_year), "rating": 4.4, "ratings_count": 100},
        {"title": "Новая подходящая", "year": str(current_year), "rating": 4.7, "ratings_count": 10},
    ]

    rows = _labels(leisure_books._book_preferences_kb("42"))

    assert rows == [
        ["✅ Новинки"],
        ["□ Любые годы"],
        ["□ ⭐️ 3.5"],
        ["□ ⭐️ 4.0"],
        ["✅ ⭐️ 4.5"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]
    assert leisure_books._pick_good_book(items, "42", fallback=False)["title"] == "Новая подходящая"


def test_genre_book_reserve_keeps_producing_distinct_cards(monkeypatch):
    monkeypatch.setattr(leisure_books, "_book_used", lambda _cid: set())
    shown = []
    for _index in range(5):
        item = leisure_books._genre_fallback_book("42", "fantasy", shown)
        shown.append(item["title"])

    assert len(set(shown)) == 5


def test_artist_list_keeps_add_above_navigation_without_edit_button(monkeypatch):
    view_id = "artists-layout"
    cleanup._views[view_id] = {
        "ctx": "music_favorite_artists",
        "revision": 0,
        "selected_ids": set(),
        "page": 0,
        "back": "m_music",
        "created_at": 0,
        "confirming": False,
        "editing": False,
    }
    monkeypatch.setattr(
        cleanup, "_view_items",
        lambda *_args: ("🎚️ Мои артисты", [("artist-1", "Артист")], "m_music"),
    )

    bot = RecordingBot()
    try:
        asyncio.run(cleanup._render_view(bot, "42", view_id))
    finally:
        cleanup._views.pop(view_id, None)

    rows = _labels(bot.message["reply_markup"])
    assert rows[0] == ["✅ Добавить артиста"]
    assert ["📝 Выбрать предпочтения"] in rows
    assert all("✏️ Изменить" not in row for row in rows)


def test_movie_list_keeps_add_above_navigation_without_edit_button(monkeypatch):
    view_id = "movies-layout"
    cleanup._views[view_id] = {
        "ctx": "cinema_favorites",
        "revision": 0,
        "selected_ids": set(),
        "page": 0,
        "back": "m_movie",
        "created_at": 0,
        "confirming": False,
        "editing": False,
    }
    monkeypatch.setattr(
        cleanup, "_view_items",
        lambda *_args: ("🎚️ Моё кино", [("movie-1", "Фильм")], "m_movie"),
    )

    bot = RecordingBot()
    try:
        asyncio.run(cleanup._render_view(bot, "42", view_id))
    finally:
        cleanup._views.pop(view_id, None)

    rows = _labels(bot.message["reply_markup"])
    assert rows[0] == ["✅ Добавить фильм"]
    assert ["📝 Выбрать предпочтения"] in rows
    assert all("✏️ Изменить" not in row for row in rows)


def test_favorite_movies_home_groups_russian_titles_by_genre():
    message = leisure_movies.leisure_ui.favorite_movies_home(3, [
        {"genre": "Драма", "titles": ["Патерсон", "Развод Надера и Симин"]},
        {"genre": "Комедия", "titles": ["Амели"]},
    ])

    assert message.text == (
        "🎚️ Моё кино · 3 фильма/сериала\n\n"
        "Драма:\nПатерсон, Развод Надера и Симин\n\n"
        "Комедия:\nАмели"
    )


def test_favorite_movies_open_genre_and_poster_card(monkeypatch):
    class Bot:
        messages = []
        photos = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

        async def send_photo(self, **kwargs):
            self.photos.append(kwargs)

    records = [
        {"id": "a123456789", "value": "Paterson (фильм, 2016)"},
        {"id": "b123456789", "value": "Amélie (фильм, 2001)"},
    ]
    metadata = {
        "Paterson": {"name": "Патерсон", "year": "2016", "kind": "movie", "genres": "драма, комедия",
                      "overview": "Водитель автобуса пишет стихи.", "poster": "https://img/paterson.jpg"},
        "Amélie": {"name": "Амели", "year": "2001", "kind": "movie", "genres": "комедия, мелодрама",
                   "overview": "Девушка меняет жизни соседей.", "poster": "https://img/amelie.jpg"},
    }
    monkeypatch.setattr(leisure_movies.config, "TMDB_API_KEY", "test-key")
    monkeypatch.setattr(leisure_movies.store, "ensure_list_ids", lambda *_args: records)
    monkeypatch.setattr(leisure_movies.tmdb, "lookup_title", lambda title: metadata[title])

    bot = Bot()
    asyncio.run(leisure_movies.send_favorite_movies(bot, "42"))

    labels = _labels(bot.messages[0]["reply_markup"])
    assert bot.messages[0]["text"] == (
        "🎚️ Моё кино · 2 фильма/сериала\n\n"
        "Комедия:\nАмели\n\n"
        "Драма:\nПатерсон"
    )
    assert labels[0] == ["✅ Добавить фильм"]
    assert ["📝 Выбрать предпочтения"] in labels
    genre_callback = next(
        row[0].callback_data
        for row in bot.messages[0]["reply_markup"].inline_keyboard
        if row[0].text.startswith("Драма")
    )
    _op, token, genre_index, page = genre_callback.split(":")

    asyncio.run(leisure_movies.send_favorite_movie_genre(
        bot, "42", token, int(genre_index), int(page),
    ))

    assert bot.photos[-1]["photo"] == "https://img/paterson.jpg"
    assert "Патерсон" in bot.photos[-1]["caption"]
    assert "Дата выхода" not in bot.photos[-1]["caption"]
    assert "Фильм · драма, комедия · 2016" in bot.photos[-1]["caption"]
    assert bot.photos[-1]["caption"].startswith("🎬 Патерсон")
    assert "Водитель автобуса пишет стихи." in bot.photos[-1]["caption"]
    assert _labels(bot.photos[-1]["reply_markup"]) == [
        ["❌ Удалить"], ["Показать списком"], ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_favorite_movie_genre_switches_posters_in_the_same_card():
    token = "carousel"
    leisure_movies._favorite_movie_views[token] = {
        "cid": "42",
        "created_at": leisure_movies.time.time(),
        "genres": [("Драма", [{
            "id": "first-id", "title": "Первый", "tm": {
                "name": "Первый", "kind": "movie", "poster": "first.jpg",
            },
        }, {
            "id": "second-id", "title": "Второй", "tm": {
                "name": "Второй", "kind": "movie", "poster": "second.jpg",
            },
        }])],
    }
    edited = []

    class Query:
        async def edit_message_media(self, **kwargs):
            edited.append(kwargs)

    asyncio.run(leisure_movies.send_favorite_movie_genre(
        object(), "42", token, 0, 1, q=Query(),
    ))

    assert edited[0]["media"].media == "second.jpg"
    assert "Второй" in edited[0]["media"].caption
    assert _labels(edited[0]["reply_markup"])[:2] == [["❌ Удалить"], ["◀️", "2/2", "▶️"]]


def test_favorite_movies_use_only_six_main_genres():
    assert leisure_movies._FAVORITE_MOVIE_GENRES == (
        "Комедия", "Ужасы", "Фантастика", "Триллер", "Романтика", "Драма",
    )
    assert leisure_movies._favorite_movie_genre({"genres": "анимация, семейный"}) == "Фантастика"
    assert leisure_movies._favorite_movie_genre({"genres": "криминал, боевик"}) == "Триллер"


def test_favorite_movie_delete_removes_only_selected_record(monkeypatch):
    token = "delete"
    leisure_movies._favorite_movie_views[token] = {
        "cid": "42",
        "created_at": leisure_movies.time.time(),
        "genres": [("Драма", [{
            "id": "movie-id-1", "value": "Патерсон", "title": "Патерсон",
            "genre": "Драма", "tm": {},
        }])],
    }
    removed = []
    reopened = []
    monkeypatch.setattr(
        leisure_movies.store, "remove_from_list_by_ids",
        lambda key, cid, ids: removed.append((key, cid, ids)) or 1,
    )

    async def reopen(_bot, cid, q=None):
        reopened.append((cid, q))

    monkeypatch.setattr(leisure_movies, "send_favorite_movies", reopen)

    asyncio.run(leisure_movies.delete_favorite_movie(object(), "42", token, "movie-id"))

    assert removed == [(config.FAVORITE_MOVIES_KEY, "42", ["movie-id-1"])]
    assert reopened == [("42", None)]


def test_favorite_books_are_grouped_by_genre_and_open_cover_card(monkeypatch):
    class Bot:
        def __init__(self):
            self.messages = []
            self.photos = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

        async def send_photo(self, **kwargs):
            self.photos.append(kwargs)

    records = [
        {"id": "book-a123", "value": "Дюна"},
        {"id": "book-b123", "value": "Гордость и предубеждение"},
    ]
    metadata = {
        "Дюна": {"title": "Дюна", "categories": ["Science Fiction"],
                 "cover_url": "https://images.test/dune.jpg", "description": "История Арракиса."},
        "Гордость и предубеждение": {"title": "Гордость и предубеждение",
                                     "categories": ["Romance"]},
    }
    monkeypatch.setattr(leisure_books.store, "ensure_list_ids", lambda *_args: records)
    monkeypatch.setattr(
        leisure_books.google_books, "enrich_book",
        lambda item: metadata[item["title"]],
    )

    bot = Bot()
    asyncio.run(leisure_books.send_favorite_books(bot, "42"))

    assert bot.messages[0]["text"] == (
        "🎚️ Мои книги · 2 книги\n\n"
        "Фантастика:\nДюна\n\n"
        "Романтика:\nГордость и предубеждение"
    )
    genre_callback = next(
        row[0].callback_data for row in bot.messages[0]["reply_markup"].inline_keyboard
        if row[0].text.startswith("Фантастика")
    )
    _op, token, genre_index, page = genre_callback.split(":")
    asyncio.run(leisure_books.send_favorite_book_genre(
        bot, "42", token, int(genre_index), int(page),
    ))

    assert bot.photos[-1]["photo"] == "https://images.test/dune.jpg"
    assert _labels(bot.photos[-1]["reply_markup"])[0] == ["❌ Удалить"]


def test_reopening_favorite_book_does_not_replace_it_with_same_title_match(monkeypatch):
    saved = {
        "id": "book-martian", "value": "Марсианин", "title": "Марсианин",
        "author": "Энди Вейер", "year": "2014", "genre_label": "Без жанра",
        "description": "Астронавт остаётся один на Марсе.",
        "cover_url": "https://images.test/martian.jpg",
        "google_books_id": "real-martian",
    }
    monkeypatch.setattr(leisure_books.store, "ensure_list_ids", lambda *_args: [saved])
    persisted = []
    monkeypatch.setattr(
        leisure_books.store, "set_list",
        lambda _key, _cid, values: persisted.extend(values),
    )
    async def genre_ai(*_args, **_kwargs):
        return {"items": [{"id": 0, "genre": "Фантастика"}]}
    monkeypatch.setattr(leisure_books.ai, "allm_json", genre_ai)
    monkeypatch.setattr(leisure_books.google_books, "find_volume", lambda *_args, **_kwargs: {
        "google_books_id": "ridley-biography", "title": "Марсианин",
        "author": "Иэн Нейтан", "year": "2014",
        "categories": ["Ридли Скотт. Гений визуальных миров. От «Чужого» до «Марсианина»"],
        "description": "Биография режиссёра Ридли Скотта.",
        "cover_url": "https://images.test/ridley.jpg",
    })

    records = asyncio.run(leisure_books._favorite_book_records("42"))
    book = records[0]["book"]

    assert book["google_books_id"] == "real-martian"
    assert book["author"] == "Энди Вейер"
    assert book["description"] == "Астронавт остаётся один на Марсе."
    assert book["cover_url"] == "https://images.test/martian.jpg"
    assert records[0]["genre"] == "Фантастика"
    assert persisted[0]["genre_label"] == "Фантастика"


def test_favorite_book_with_multiple_categories_uses_primary_genre_once():
    assert leisure_books._favorite_book_genre({
        "categories": ["Fantasy", "Romance"],
    }) == "Фэнтези"
    token, view = leisure_books._new_favorite_book_view("42", [{
        "id": "book-1", "title": "Книга", "genre": "Фэнтези", "book": {},
    }])

    assert token
    assert [(genre, [item["title"] for item in items]) for genre, items in view["genres"]] == [
        ("Фэнтези", ["Книга"]),
    ]


def test_favorite_book_genre_switches_covers_in_the_same_card():
    token = "book-carousel"
    leisure_books._favorite_book_views[token] = {
        "cid": "42", "created_at": leisure_books.time.time(),
        "genres": [("Фантастика", [{
            "id": "book-one", "title": "Первая", "book": {
                "title": "Первая", "cover_url": "one.jpg",
            },
        }, {
            "id": "book-two", "title": "Вторая", "book": {
                "title": "Вторая", "cover_url": "two.jpg",
            },
        }])],
    }
    edited = []

    class Query:
        async def edit_message_media(self, **kwargs):
            edited.append(kwargs)

    asyncio.run(leisure_books.send_favorite_book_genre(
        object(), "42", token, 0, 1, q=Query(),
    ))

    assert edited[0]["media"].media == "two.jpg"
    assert "Вторая" in edited[0]["media"].caption
    assert _labels(edited[0]["reply_markup"])[:2] == [["❌ Удалить"], ["◀️", "2/2", "▶️"]]


def test_book_list_keeps_add_above_navigation_without_edit_button(monkeypatch):
    view_id = "books-layout"
    cleanup._views[view_id] = {
        "ctx": "books_favorites",
        "revision": 0,
        "selected_ids": set(),
        "page": 0,
        "back": "m_books",
        "created_at": 0,
        "confirming": False,
        "editing": False,
    }
    monkeypatch.setattr(
        cleanup, "_view_items",
        lambda *_args: ("🎚️ Мои книги", [("book-1", "Книга")], "m_books"),
    )

    bot = RecordingBot()
    try:
        asyncio.run(cleanup._render_view(bot, "42", view_id))
    finally:
        cleanup._views.pop(view_id, None)

    rows = _labels(bot.message["reply_markup"])
    assert rows[0] == ["✅ Добавить книгу"]
    assert ["📝 Выбрать предпочтения"] in rows
    assert all("✏️ Изменить" not in row for row in rows)


def test_personal_lists_are_available_from_their_category_preferences():
    assert _labels(leisure_music._music_preferences_kb("42"))[0] == ["⬜ Инди"]


def test_recommendation_subscreens_return_to_their_category_home():
    music_styles = [key for key, _label, _prompt_name in leisure_music._MUSIC_GENRES]
    original_music_styles = leisure_music._music_styles
    leisure_music._music_styles = lambda _cid: music_styles
    keyboards = [
        leisure_movies._movie_genre_menu_kb(),
        leisure_books._book_kb(0),
        leisure_books._book_genre_menu_kb(),
        leisure_music._listen_kb(), leisure_music._music_genre_menu_kb("42"),
    ]
    try:
        assert all(_labels(keyboard)[-1] == ["⬅️ Назад", "#️⃣ Главная"] for keyboard in keyboards)
        assert _labels(leisure_movies._movie_kb(0))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
        assert _labels(leisure_movies._movie_prefs_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
        assert _labels(leisure_books._book_preferences_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
        assert _labels(leisure_music._music_preferences_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    finally:
        leisure_music._music_styles = original_music_styles


def test_music_does_not_offer_generic_bookmarks():
    assert "💾 Сохранения" not in sum(_labels(leisure_music._listen_kb()), [])


def test_music_styles_are_stored_and_invalidate_the_daily_recommendation(monkeypatch):
    saved = {}
    monkeypatch.setattr(leisure_music.settings, "get", lambda *_args: [])
    monkeypatch.setattr(leisure_music.settings, "set_", lambda _cid, key, value: saved.update({key: value}))
    monkeypatch.setattr(leisure_music, "_invalidate_artist", lambda _cid: None)

    class Message:
        async def edit_text(self, *_args, **_kwargs):
            return None

    class Query:
        message = Message()

    asyncio.run(leisure_music.toggle_music_style(None, "42", "indie", Query()))
    assert saved["music_styles"] == ["indie"]


def test_movies_and_books_do_not_offer_generic_bookmarks():
    for keyboard in (
        leisure_movies._movie_kb(0), leisure_books._book_kb(0),
    ):
        assert "💾 Сохранения" not in sum(_labels(keyboard), [])
        assert "💾 Сохранить" not in sum(_labels(keyboard), [])


def test_book_recommendation_skips_favorite_and_prefers_reader_rating(monkeypatch):
    def get_list(key, _cid):
        return [{"value": "1984"}] if key == config.FAVORITE_BOOKS_KEY else []

    monkeypatch.setattr(leisure_books.store, "get_list", get_list)
    monkeypatch.setattr(leisure_books.recommendation_stoplist, "values", lambda *_args: [])
    result = leisure_books._pick_good_book([
        {"title": "1984", "rating": 5},
        {"title": "Книга читателей", "rating": 4.6, "ratings_count": 240},
        {"title": "Книга с меньшей оценкой", "rating": 4.1, "ratings_count": 900},
    ], "42")
    assert result["title"] == "Книга читателей"


def test_favorite_artists_are_grouped_by_genre_without_no_genre_category(monkeypatch):
    monkeypatch.setattr(leisure_music, "_cached_artist", lambda _cid: {})
    items = [
        ("unknown", "Unknown Artist"),
        ("indie", "Big Thief"),
        ("pop", "Caroline Polachek"),
    ]

    grouped = leisure_music.group_favorite_artist_items("42", items)

    assert [leisure_music.favorite_artist_genre("42", label) for _id, label in grouped] == [
        "Инди", "Поп", "Другие артисты",
    ]
    assert "Без жанра" not in {
        leisure_music.favorite_artist_genre("42", label) for _id, label in grouped
    }


def test_book_and_music_genre_menus_have_one_column_without_emoji(monkeypatch):
    monkeypatch.setattr(
        leisure_music, "_music_styles",
        lambda _cid: [key for key, _label, _prompt_name in leisure_music._MUSIC_GENRES],
    )
    assert _labels(leisure_books._book_genre_menu_kb())[:-1] == [
        ["🆕 Новинка"], ["Фэнтези"], ["Фантастика"], ["Детектив"], ["Триллер"],
        ["Романтика"], ["История"], ["Биографии"], ["Психология"],
    ]
    assert _labels(leisure_music._music_genre_menu_kb("42"))[:-1] == [
        ["🆕 Новинка"], ["Инди"], ["Поп"], ["Электроника"], ["R&B"], ["Рок"], ["Хип-хоп"],
    ]
    assert _labels(leisure_movies._movie_genre_menu_kb())[:-1] == [
        ["🆕 Новинка"], ["Комедия"], ["Ужасы"], ["Фантастика"],
        ["Триллер"], ["Романтика"], ["Драма"],
    ]


def test_music_genre_menu_shows_only_selected_styles(monkeypatch):
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie", "rock"])

    assert _labels(leisure_music._music_genre_menu_kb("42")) == [
        ["🆕 Новинка"], ["Инди"], ["Рок"], ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_book_genre_menu_does_not_replace_the_recommendation_card():
    class BookMessage:
        def __init__(self):
            self.edits = []
            self.markup = None

        async def edit_text(self, *args, **kwargs):
            self.edits.append((args, kwargs))

        async def edit_reply_markup(self, *, reply_markup):
            self.markup = reply_markup

    message = BookMessage()
    query = type("Query", (), {"message": message})()
    bot = RecordingBot()

    asyncio.run(leisure_books.send_book_genre_menu(bot, "42", query))

    assert message.edits == []
    assert _labels(message.markup) == _labels(leisure_books._book_genre_menu_kb())
    assert bot.sent == []


def test_book_genre_uses_a_matching_fallback_when_catalogue_is_empty(monkeypatch):
    selected = []

    class Bot:
        sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    async def send_card(_bot, _cid, book, _index, *, enrich):
        selected.append(book)
        return book

    async def no_books(_cid, _category):
        return []

    monkeypatch.setattr(leisure_books, "_book_candidates", no_books)
    monkeypatch.setattr(leisure_books, "_send_book_card", send_card)
    monkeypatch.setattr(leisure_books, "_cache_book", lambda *_args: None)
    monkeypatch.setattr(leisure_books.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(leisure_books.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_books.store, "last_recos", {})

    bot = Bot()
    asyncio.run(leisure_books.send_book_by_genre(bot, "genre-empty", "fantasy"))

    assert selected[0]["title"] == "Заражённая чаша"
    assert bot.sent == []


def test_music_genre_selection_stays_in_the_selected_genre(monkeypatch):
    calls = []

    async def send_listen(bot, cid, **kwargs):
        calls.append((cid, kwargs))

    monkeypatch.setattr(leisure_music, "send_listen", send_listen)
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie"])
    asyncio.run(leisure_music.send_music_by_genre(object(), "42", "indie", status="status"))

    assert calls == [("42", {"category": {
        "kind": "genre", "value": "indie", "label": "Инди",
        "prompt_name": "инди-поп или инди-рок",
    }, "force": True, "status": "status"})]


def test_book_cache_drops_a_favorite(monkeypatch):
    today = datetime.now(config.TZ).date().isoformat()
    monkeypatch.setattr(leisure_books.store, "_load", lambda *_args: {
        "42": {"date": today, "item": {"title": "1984"}},
    })
    monkeypatch.setattr(leisure_books.store, "get_list", lambda key, _cid: [{"value": "1984"}] if key == config.FAVORITE_BOOKS_KEY else [])
    monkeypatch.setattr(leisure_books.recommendation_stoplist, "values", lambda *_args: [])
    assert leisure_books._cached_book("42") is None


def test_book_home_reads_its_fresh_daily_cache(monkeypatch):
    today = datetime.now(config.TZ).date().isoformat()
    entry = {
        "42": {
            "date": today,
            "item": {"title": "Дюна", "rating": 4.5, "ratings_count": 1_000},
            "preferences": {"recency": None, "min_rating": None},
        },
    }
    monkeypatch.setattr(leisure_books.store, "_load", lambda *_args: entry)
    monkeypatch.setattr(leisure_books.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_books.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(leisure_books.settings, "get", lambda *_args: "")

    assert leisure_books._cached_book("42")["title"] == "Дюна"


def test_book_card_has_modern_compact_hierarchy():
    message = leisure_books._book_text({
        "title": "Ночной город", "title_en": "Night Night Fawn", "author": "Автор",
        "year": "2026", "categories": ["Fantasy"],
        "plot": "Первое. Второе. Третье. Четвёртое. Пятое. Шестое предложение.",
        "rating": 4.7, "ratings_count": 1234,
        "why": ["Необычный мир"],
        "quote": "Выдуманная цитата",
    })
    assert message.text == (
        "Ночной город\n\n"
        "Автор · 2026 · Night Night Fawn\n"
        "Жанр: Фэнтези\n\n"
        "Сюжет\n"
        "Первое. Второе. Третье. Четвёртое. Пятое."
    )
    assert "⭐" not in message.text
    assert "Почему стоит читать" not in message.text
    assert "цитат" not in message.text.casefold()


def test_book_card_shows_author_section_from_details():
    message = leisure_books._book_text({
        "title": "Книга", "author": "Автор", "plot": "О чём книга.",
        "author_about": "Американская писательница", "author_books": ["A", "B", "C", "D"],
    })
    assert message.text.endswith(
        "Об авторе\nАмериканская писательница.\nДругие книги: «A», «B», «C»"
    )


def test_book_card_hides_missing_metadata_instead_of_inventing_it():
    message = leisure_books._book_text({
        "title": "Night Night Fawn",
        "url": "https://books.google.com/books?id=test",
    })

    assert message.text == "Night Night Fawn"


def test_book_card_from_inline_status_is_sent_with_cover(monkeypatch):
    sent = []

    class Bot:
        async def send_photo(self, **kwargs):
            sent.append(("photo", kwargs))

        async def send_message(self, **kwargs):
            sent.append(("message", kwargs))

    class Status:
        async def replace(self, *_args, **_kwargs):
            raise AssertionError("card with cover must be sent as a photo")

    item = {
        "title": "Новая книга",
        "url": "https://books.google.com/books?id=new",
        "cover_url": "https://images.test/new-book.jpg",
    }

    asyncio.run(leisure_books._send_book_card(
        Bot(), "42", item, 0, enrich=False, status=Status(),
    ))

    assert [kind for kind, _kwargs in sent] == ["photo"]
    assert sent[0][1]["photo"] == "https://images.test/new-book.jpg"


def test_book_card_text_fallback_disables_link_preview(monkeypatch):
    replaced = {}

    class Status:
        async def replace(self, text, **kwargs):
            replaced.update({"text": text, **kwargs})

    monkeypatch.setattr(leisure_books, "_book_cover", lambda *_args: None)

    asyncio.run(leisure_books._send_book_card(
        object(), "42", {
            "title": "Книга без обложки",
            "url": "https://books.google.com/books?id=no-cover",
        }, 0, enrich=False, status=Status(),
    ))

    assert replaced["disable_web_page_preview"] is True


def test_premiere_screens_are_compact_and_keep_book_links():
    movie = leisure_movies.leisure_ui.movie_premieres_screen("Нидерланды", "13–26 августа", [{
        "title": "Премьера", "date": "2026-08-15", "genres": "Драма, комедия",
        "overview": "Семья пытается сохранить дом после большого наводнения",
        "trailer_url": "https://www.youtube.com/watch?v=premiere",
    }])
    books = leisure_books.leisure_ui.book_premieres_screen("Августа 2026", [{
        "title": "Новая книга", "author": "Автор", "summary": "Героиня ищет сестру в незнакомом городе",
        "published_date": "2026-08-15", "categories": ["Fiction"],
        "url": "https://books.google.com/books?id=new",
    }])

    assert "Премьеры фильмов · Нидерланды" in movie.text
    assert "до 7 самых популярных" not in movie.text
    assert "«Премьера» · драма · комедия · 15 августа 2026" in movie.text
    assert "Семья пытается сохранить дом после большого наводнения." in movie.text
    assert any(
        entity.type == MessageEntity.TEXT_LINK
        and entity.url == "https://www.youtube.com/watch?v=premiere"
        for entity in movie.entities
    )
    assert books.text.startswith("🆕 Премьеры книг · Августа 2026")
    assert "«Новая книга»\nАвтор\nХудожественная проза\nПремьера: 15 августа 2026" in books.text
    assert "Героиня ищет сестру в незнакомом городе." in books.text
    assert any(entity.type == MessageEntity.TEXT_LINK and entity.url.endswith("id=new") for entity in books.entities)


def test_weekly_events_are_one_line_per_item_across_all_categories():
    items = range(4)
    concerts = range(7)
    message = leisure_movies.leisure_ui.weekly_events_card(
        [{
            "id": index, "title": f"Фильм {index}", "genres": "Драма",
            "rating": 7.5, "vote_count": 20,
            "trailer_url": f"https://example.com/movie/{index}",
            "overview": "Описание не должно попасть в рассылку.",
        } for index in items],
        [{
            "title": f"Концерт {index}", "genre": "Рок", "date": "2026-08-21",
            "url": f"https://example.com/concert/{index}",
        } for index in concerts],
        [{
            "title": f"Книга {index}", "categories": ["Fantasy"],
            "rating": 4.4, "ratings_count": 15,
            "url": f"https://example.com/book/{index}",
            "summary": "Описание не должно попасть в рассылку.",
        } for index in items],
    )

    assert message.text.startswith("🎲 Ближайшие события\n\n🎬 Кино")
    assert message.text.count("• «Фильм") == 3
    assert message.text.count("• Концерт") == 6
    assert message.text.count("• «Книга") == 3
    assert "Игр" not in message.text
    assert "• «Фильм 0» · драма\n" in message.text
    assert "⭐" not in message.text.split("🎫")[0]
    assert "«Книга 0» · Фэнтези · ⭐ 4.4/5" in message.text
    assert "Описание не должно" not in message.text
    assert len([entity for entity in message.entities if entity.type == MessageEntity.TEXT_LINK]) == 12


def test_movie_premieres_fit_one_message_without_cutting_descriptions():
    first_sentence = "Героиня возвращается домой и пытается раскрыть семейную тайну."
    items = [{
        "title": f"Премьера {index}",
        "date": "2026-08-15",
        "genres": "Драма, триллер",
        "overview": f"{first_sentence} Это второе подробное предложение в карточке.",
    } for index in range(30)]

    message = leisure_movies.leisure_ui.movie_premieres_screen(
        "Нидерланды", "13–26 августа", items,
    )

    assert len(message.text.encode("utf-16-le")) // 2 <= 1024
    assert message.text.count("«Премьера ") == 5
    assert first_sentence in message.text
    assert "Это второе подробное предложение" not in message.text
    assert not message.text.endswith("…")


def test_movie_premieres_are_sent_as_one_poster_carousel(monkeypatch):
    sent = []
    premiere_date = (datetime.now(config.TZ).date() + timedelta(days=3)).isoformat()

    class Bot:
        async def send_photo(self, **kwargs):
            sent.append(("photo", kwargs))

        async def send_message(self, **kwargs):
            sent.append(("message", kwargs))

    class Status:
        async def replace(self, *_args, **_kwargs):
            raise AssertionError("carousel must not fall back to a text message")

    items = [
        {
            "id": index,
            "title": f"Фильм {index}",
            "date": premiere_date,
            "genres": "драма",
            "overview": f"Короткая завязка {index}.",
            "poster": f"https://image.tmdb.org/poster{index}.jpg",
            "trailer_url": f"https://www.youtube.com/watch?v=trailer{index}",
        }
        for index in range(3)
    ]
    monkeypatch.setattr(leisure_movies.store, "get_settings", lambda _cid: {
        "country": "Нидерланды", "cc": "NL",
    })
    monkeypatch.setattr(leisure_movies, "get_movie_premieres", lambda _cid: asyncio.sleep(0, result=items))
    monkeypatch.setattr(movie_discovery, "get_movie_premieres", lambda _cid: asyncio.sleep(0, result=items))
    monkeypatch.setattr(
        leisure_movies.tmdb, "english_poster",
        lambda movie_id, _kind: f"https://image.tmdb.org/poster{movie_id}.jpg",
    )

    asyncio.run(leisure_movies.send_movie_premieres(Bot(), "42", status=Status()))

    assert [kind for kind, _kwargs in sent] == ["photo"]
    card = sent[0][1]
    assert card["photo"] == items[0]["poster"]
    assert card["caption"].startswith("🎟️ Премьеры фильмов · Нидерланды")
    assert "Фильм 0" in card["caption"]
    assert "Фильм 1" not in card["caption"]
    assert {
        entity.url for entity in card["caption_entities"]
        if entity.type == MessageEntity.TEXT_LINK
    } == {items[0]["trailer_url"]}
    assert _labels(card["reply_markup"]) == [
        ["◀️", "1/3", "▶️"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_movie_premiere_carousel_edits_the_same_message(monkeypatch):
    premiere_date = (datetime.now(config.TZ).date() + timedelta(days=3)).isoformat()
    items = [{
        "id": index,
        "title": f"Фильм {index}",
        "date": premiere_date,
        "genres": "драма",
        "overview": f"Короткая завязка {index}.",
        "poster": f"https://image.tmdb.org/poster{index}.jpg",
    } for index in range(3)]
    edited = []

    class Query:
        async def edit_message_media(self, **kwargs):
            edited.append(kwargs)

    monkeypatch.setattr(leisure_movies.store, "get_settings", lambda _cid: {
        "country": "Нидерланды", "cc": "NL",
    })
    monkeypatch.setattr(
        leisure_movies, "get_movie_premieres",
        lambda _cid: asyncio.sleep(0, result=items),
    )
    monkeypatch.setattr(
        movie_discovery, "get_movie_premieres",
        lambda _cid: asyncio.sleep(0, result=items),
    )
    monkeypatch.setattr(
        leisure_movies.tmdb, "english_poster",
        lambda movie_id, _kind: f"https://image.tmdb.org/poster{movie_id}.jpg",
    )

    asyncio.run(leisure_movies.show_movie_premiere_page("42", Query(), 1))

    assert len(edited) == 1
    assert edited[0]["media"].media == items[1]["poster"]
    assert "Фильм 1" in edited[0]["media"].caption
    assert _labels(edited[0]["reply_markup"]) == [
        ["◀️", "2/3", "▶️"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_book_premieres_are_sent_as_one_poster_carousel(monkeypatch):
    sent = []
    items = [{
        "title": f"Книга {index}", "author": f"Автор {index}",
        "published_date": "2026-08-15", "categories": ["Fiction"],
        "summary": f"Короткое описание {index}.",
        "cover_url": f"https://images.test/book{index}.jpg",
    } for index in range(3)]

    class Bot:
        async def send_photo(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(
        leisure_books, "_book_premieres_with_covers",
        lambda: asyncio.sleep(0, result=items),
    )

    asyncio.run(leisure_books.send_book_premieres(Bot(), "42"))

    assert sent[0]["photo"] == items[0]["cover_url"]
    assert "Книга 0" in sent[0]["caption"]
    assert "Книга 1" not in sent[0]["caption"]
    assert _labels(sent[0]["reply_markup"]) == [
        ["◀️", "1/3", "▶️"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_book_premiere_carousel_edits_the_same_message(monkeypatch):
    items = [{
        "title": f"Книга {index}", "author": f"Автор {index}",
        "published_date": "2026-08-15", "categories": ["Fiction"],
        "summary": f"Короткое описание {index}.",
        "cover_url": f"https://images.test/book{index}.jpg",
    } for index in range(3)]
    edited = []

    class Query:
        async def edit_message_media(self, **kwargs):
            edited.append(kwargs)

    monkeypatch.setattr(
        leisure_books, "_book_premieres_with_covers",
        lambda: asyncio.sleep(0, result=items),
    )

    asyncio.run(leisure_books.show_book_premiere_page(Query(), 1))

    assert edited[0]["media"].media == items[1]["cover_url"]
    assert "Книга 1" in edited[0]["media"].caption
    assert _labels(edited[0]["reply_markup"]) == [
        ["◀️", "2/3", "▶️"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_series_premiere_card_marks_favorite_season_without_rating():
    message = leisure_movies.leisure_ui.series_premiere_screen({
        "name": "Разделение",
        "season_number": 3,
        "favorite": True,
        "release_date": "2026-09-12",
        "rating": 8.4,
        "genres": "драма, фантастика",
        "overview": "Сотрудники снова пытаются раскрыть тайну компании.",
        "url": "https://www.themoviedb.org/tv/95396",
    })

    assert "📺 Премьеры сериалов" in message.text
    assert "3 сезон · из Моего кино · 12 сентября 2026 · драма" in message.text
    assert "⭐" not in message.text
    assert "драма · фантастика" in message.text
    assert any(
        entity.type == MessageEntity.TEXT_LINK
        and entity.url == "https://www.themoviedb.org/tv/95396"
        for entity in message.entities
    )


def test_book_card_does_not_show_quote():
    message = leisure_books._book_text({
        "title": "1984", "author": "Джордж Оруэлл", "quote": "Война - это мир.",
    })
    assert "Война - это мир" not in message.text


def test_cinema_rebus_changes_with_calendar_day():
    today = date(2026, 8, 4)

    assert leisure_movies._daily_rebus(today)["answer"] == "Челюсти"
    assert leisure_movies._daily_rebus(today + timedelta(days=1))["answer"] != "Челюсти"


def test_book_showcase_falls_back_to_google_books_search_link():
    item = leisure_books._with_book_url({
        "title": "Недавний бестселлер", "author": "Автор",
        "summary": "Короткое описание книги.",
    })

    assert item["url"] == "https://books.google.com/books?q=%D0%9D%D0%B5%D0%B4%D0%B0%D0%B2%D0%BD%D0%B8%D0%B9+%D0%B1%D0%B5%D1%81%D1%82%D1%81%D0%B5%D0%BB%D0%BB%D0%B5%D1%80+%D0%90%D0%B2%D1%82%D0%BE%D1%80"


def test_tv_detail_line_uses_russian_plurals():
    from ui.leisure import _detail_line

    expected = {
        1: ("1 сезон", "1 серия"), 2: ("2 сезона", "2 серии"), 5: ("5 сезонов", "5 серий"),
        11: ("11 сезонов", "11 серий"), 21: ("21 сезон", "21 серия"), 22: ("22 сезона", "22 серии"),
    }
    for n, (seasons, episodes) in expected.items():
        line = _detail_line({"kind": "tv", "seasons": n, "episodes": n})
        assert line == f"{seasons} • {episodes}"


def test_book_premiere_summary_is_separated_from_date_by_blank_line():
    from ui import leisure as leisure_ui

    text = leisure_ui.book_premieres_screen("сентябрь", [{
        "title": "Intermezzo", "author": "Sally Rooney",
        "published_date": "2026-09-01", "summary": "История двух братьев после смерти отца.",
    }]).text

    assert "Премьера: 1 сентября 2026\n\nИстория двух братьев" in text




def test_other_opens_plain_genre_picker_and_back_returns_card_buttons():
    picker = leisure_movies._movie_genre_menu_kb(back="movie_card_3")
    rows = picker.inline_keyboard
    assert all(row[0].text != "Любой жанр" for row in rows)
    genres = [row[0] for row in rows[:-1] if row[0].callback_data.startswith("movie_g_")]
    assert genres and all(not button.api_kwargs for button in genres)
    assert rows[0][0].callback_data == "nov_movie"  # «Новинка» — первой
    assert rows[-1][0].callback_data == "movie_card_3"


def test_picker_swaps_card_buttons_and_choice_clears_old_card(monkeypatch):
    import bot_callbacks
    from types import SimpleNamespace

    class Message:
        chat_id = "42"
        message_id = 9
        reply_markup = None

        def __init__(self):
            self.markups = []

        async def edit_reply_markup(self, reply_markup=None):
            self.markups.append(reply_markup)

    async def answer(*_a, **_k):
        return None

    shown = []

    async def by_genre(_bot, _cid, genre):
        shown.append(genre)

    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.leisure_movies, "send_movie_by_genre", by_genre)
    async def start_inline(*_a, **_k):
        return _NoStatus()

    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)

    def click(data):
        message = Message()
        query = SimpleNamespace(data=data, message=message, answer=answer)
        asyncio.run(bot_callbacks.handle(SimpleNamespace(callback_query=query), SimpleNamespace(bot=object()), None))
        return message.markups

    picker = click("movie_pick_3")[0]
    assert picker.inline_keyboard[0][0].text == "🆕 Новинка"
    assert picker.inline_keyboard[1][0].text == "Комедия"
    assert click("movie_card_3")[0].inline_keyboard[0][0].callback_data == "movie_pick_3"
    assert click("movie_g_35") == [None] and shown == ["35"]


class _NoStatus:
    async def replace(self, *_a, **_k):
        return True

    async def stop(self, delete=True):
        return None


def test_book_genre_pick_uses_library_taste_first(monkeypatch):
    asked = []

    def recommend(kind, cid, genre=None):
        asked.append(genre)
        return {"items": [{"title": "Dune"}]}

    async def enrich(items):
        return items

    monkeypatch.setattr(leisure_books, "content_recommend", recommend)
    monkeypatch.setattr(leisure_books, "_enrich_book_candidates", enrich)

    items = asyncio.run(leisure_books._book_candidates("42", {"value": "scifi"}))

    assert asked == [("Фантастика", "Science fiction")] and items == [{"title": "Dune"}]


def test_movie_card_puts_full_release_date_into_rating_line():
    _title, msg = leisure_movies.leisure_ui.movie_card({"title": "Дюна"}, {
        "name": "Дюна", "year": "2021", "release_date": "2021-09-15", "kind": "movie",
        "rating": 7.8, "vote_count": 900, "genres": "фантастика, драма",
    })

    assert "⭐ 7.8 · Фильм · фантастика, драма · 15 сентября 2021" in msg.text
    assert "Дата выхода" not in msg.text


def test_movie_card_hides_genre_match_and_links_trailer_in_text():
    _title, msg = leisure_movies.leisure_ui.movie_card({"title": "Дюна"}, {
        "name": "Дюна", "year": "2021", "kind": "movie", "genres": "фантастика, драма",
        "because": "Интерстеллар", "via": "similar", "shared_genres": ["драма", "фантастика"],
        "trailer_url": "https://www.youtube.com/watch?v=abc",
    })

    assert "Подходит по жанрам" not in msg.text
    assert msg.text.endswith("Посмотреть трейлер")
    link = next(e for e in msg.entities if e.type == "text_link")
    assert link.url == "https://www.youtube.com/watch?v=abc"


def test_configure_under_card_opens_section_settings_as_new_message(monkeypatch):
    import bot_callbacks
    from types import SimpleNamespace

    opened = []

    async def favorites(_bot, _cid, q=None):
        opened.append(("movie", q))

    async def answer(*_a, **_k):
        return None

    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.leisure_movies, "send_favorite_movies", favorites)
    query = SimpleNamespace(data="lz_cfg_movie", message=SimpleNamespace(chat_id="42", message_id=1), answer=answer)
    asyncio.run(bot_callbacks.handle(SimpleNamespace(callback_query=query), SimpleNamespace(bot=object()), None))

    assert opened == [("movie", None)]
    assert leisure_novelty_card_cfg() == "lz_cfg_book"


def leisure_novelty_card_cfg():
    import leisure_novelty
    return leisure_novelty.card_keyboard("book").inline_keyboard[-2][0].callback_data


def test_book_card_shows_author_country_year_and_genre():
    text = leisure_movies.leisure_ui.book_text({
        "title": "Проект «Аве Мария»", "author": "Энди Вейер", "country": "США",
        "year": "2021", "genre": "Фантастика", "plot": "Учёный просыпается на корабле.",
    }).text

    assert text.startswith("Проект «Аве Мария»\n\nЭнди Вейер · США · 2021\nЖанр: Фантастика")




