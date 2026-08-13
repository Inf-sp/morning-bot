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
import leisure_music
import movie_engine
import settings


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _bold_values(message):
    encoded = message.text.encode("utf-16-le")
    return {
        encoded[entity.offset * 2:(entity.offset + entity.length) * 2].decode("utf-16-le")
        for entity in message.entities if entity.type == MessageEntity.BOLD
    }


def test_category_homes_keep_personal_lists_in_their_own_sections():
    assert _labels(leisure_movies._movie_home_kb()) == [
        ["✨ Подобрать новое кино"],
        ["🎟️ Премьеры"],
        ["🎚️ Моё кино"],
        ["#️⃣ Главная"],
    ]
    assert _labels(leisure_books.books_home_keyboard()) == [
        ["✨ Подобрать новую книгу"],
        ["🆕 Премьеры"],
        ["🎚️ Мои книги"],
        ["#️⃣ Главная"],
    ]
    assert _labels(leisure_music.music_home_keyboard()) == [
        ["✨ Подобрать новую музыку"],
        ["🎫 Концерты"],
        ["🎚️ Мои артисты"],
        ["#️⃣ Главная"],
    ]


def test_recommendation_cards_use_content_specific_next_labels():
    assert _labels(leisure_movies._movie_kb(0))[0] == ["✨ Другое кино"]
    assert _labels(leisure_books._book_kb(0))[0] == ["✨ Другая книга"]
    assert _labels(leisure_music._listen_kb())[0] == ["✨ Другая музыка"]
    assert _labels(leisure_books._book_kb(0))[1] == ["🎭 По жанру"]
    assert _labels(leisure_movies._movie_kb(0))[1] == ["🎭 По жанру"]
    assert _labels(leisure_music._listen_kb())[1] == ["🎭 По жанру"]
    assert _labels(leisure_movies._movie_kb(0))[-1] == ["⬅️ Назад", "#️⃣ Главная"]


def test_preferences_are_kept_out_of_personal_content_lists():
    assert _labels(leisure_movies._movie_prefs_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert cleanup.COLLECTIONS["cinema_favorites"]["menu_button"] is None
    assert cleanup.COLLECTIONS["cinema_favorites"]["add_button_at_bottom"] is True
    assert cleanup.COLLECTIONS["cinema_favorites"]["allow_edit"] is False
    assert _labels(leisure_books._book_preferences_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert cleanup.COLLECTIONS["books_favorites"]["menu_button"] is None
    assert cleanup.COLLECTIONS["books_favorites"]["add_button_at_bottom"] is True
    assert cleanup.COLLECTIONS["books_favorites"]["allow_edit"] is False
    assert _labels(leisure_music._music_preferences_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert cleanup.COLLECTIONS["music_favorite_artists"]["menu_button"] is None
    assert cleanup.COLLECTIONS["music_favorite_artists"]["add_button_at_bottom"] is True
    assert cleanup.COLLECTIONS["music_favorite_artists"]["allow_edit"] is False


def test_global_preferences_has_all_recommendation_sections():
    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    bot = Bot()
    asyncio.run(settings.send_preferences(bot, "42"))

    assert _labels(bot.sent[0]["reply_markup"]) == [
        ["🧠 Обучение"], ["🥣 Кухни"], ["🧵 Стиль"], ["🎧 Музыка"],
        ["🎬 Кино"], ["📚 Книги"], ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_movie_preferences_keep_only_type_recency_and_rating(monkeypatch):
    monkeypatch.setattr(leisure_movies.settings, "get", lambda *_args: "")

    rows = _labels(leisure_movies._movie_prefs_kb("42"))

    assert rows == [
        ["🎬 Фильмы"],
        ["Сериалы"],
        ["Новинки"],
        ["✅ Любые годы"],
        ["⭐️ 6.5"],
        ["⭐️ 7.0"],
        ["⭐️ 7.5"],
        ["⭐️ 8.0"],
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

    def discover(kind, _genres, min_rating, year):
        requested.update(kind=kind, min_rating=min_rating, year=year)
        return [{
            "id": 7, "name": "Новый сериал", "kind": "tv", "rating": 8.2,
            "vote_count": 500, "popularity": 20, "release_date": date.today().isoformat(),
        }]

    monkeypatch.setattr(leisure_movies.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_movies.movie_engine, "_excluded_norms", lambda _cid: set())
    monkeypatch.setattr(leisure_movies.movie_engine, "mark_shown", lambda *_args: None)
    monkeypatch.setattr(leisure_movies, "_movie_prefs", lambda _cid: {
        "type_pref": "tv", "recency": "new", "min_rating": 8.0,
    })
    monkeypatch.setattr(leisure_movies.tmdb, "discover", discover)
    monkeypatch.setattr(leisure_movies, "_send_movie_card", deliver)

    asyncio.run(leisure_movies.send_recos(object(), "42", "movie"))

    assert requested == {"kind": "tv", "min_rating": 8.0, "year": 2000}
    assert delivered[0][0]["title"] == "Новый сериал"


def test_movie_home_falls_back_when_tmdb_is_temporarily_unavailable(monkeypatch):
    monkeypatch.setattr(leisure_movies, "_cached_movie", lambda _cid: None)
    monkeypatch.setattr(leisure_movies.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_movies.movie_engine, "_excluded_norms", lambda _cid: set())
    monkeypatch.setattr(leisure_movies, "_movie_prefs", lambda _cid: {})
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
        ["Любые годы"],
        ["⭐️ 3.5"],
        ["⭐️ 4.0"],
        ["✅ ⭐️ 4.5"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]
    assert leisure_books._pick_good_book(items, "42", fallback=False)["title"] == "Новая подходящая"


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

    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    bot = Bot()
    try:
        asyncio.run(cleanup._render_view(bot, "42", view_id))
    finally:
        cleanup._views.pop(view_id, None)

    rows = _labels(bot.message["reply_markup"])
    assert rows[-2:] == [["🆕 Добавить артиста"], ["⬅️ Назад", "#️⃣ Главная"]]
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

    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    bot = Bot()
    try:
        asyncio.run(cleanup._render_view(bot, "42", view_id))
    finally:
        cleanup._views.pop(view_id, None)

    rows = _labels(bot.message["reply_markup"])
    assert rows[-2:] == [["🆕 Добавить фильм"], ["⬅️ Назад", "#️⃣ Главная"]]
    assert all("✏️ Изменить" not in row for row in rows)


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

    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    bot = Bot()
    try:
        asyncio.run(cleanup._render_view(bot, "42", view_id))
    finally:
        cleanup._views.pop(view_id, None)

    rows = _labels(bot.message["reply_markup"])
    assert rows[-2:] == [["🆕 Добавить книгу"], ["⬅️ Назад", "#️⃣ Главная"]]
    assert all("✏️ Изменить" not in row for row in rows)


def test_personal_lists_are_available_from_their_category_preferences():
    assert _labels(leisure_music._music_preferences_kb("42"))[0] == ["⬜ 🌿 Инди"]


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
    assert "💾 Сохранения" not in sum(_labels(leisure_music.music_home_keyboard()), [])
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
        leisure_movies._movie_home_kb(), leisure_movies._movie_kb(0),
        leisure_books.books_home_keyboard(), leisure_books._book_kb(0),
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


def test_book_and_music_genre_menus_have_two_columns(monkeypatch):
    monkeypatch.setattr(
        leisure_music, "_music_styles",
        lambda _cid: [key for key, _label, _prompt_name in leisure_music._MUSIC_GENRES],
    )
    assert _labels(leisure_books._book_genre_menu_kb())[:-1] == [
        ["🧙 Фэнтези", "🚀 Фантастика"], ["🔍 Детектив", "😱 Триллер"],
        ["💕 Романтика", "🏛 История"], ["👤 Биографии", "🧠 Психология"],
    ]
    assert _labels(leisure_music._music_genre_menu_kb("42"))[:-1] == [
        ["🌿 Инди", "✨ Поп"], ["⚡ Электроника", "🪩 R&B"],
        ["🎸 Рок", "🎤 Хип-хоп"],
    ]


def test_music_genre_menu_shows_only_selected_styles(monkeypatch):
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie", "rock"])

    assert _labels(leisure_music._music_genre_menu_kb("42")) == [
        ["🌿 Инди", "🎸 Рок"], ["⬅️ Назад", "#️⃣ Главная"],
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

    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    message = BookMessage()
    query = type("Query", (), {"message": message})()
    bot = Bot()

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

    assert selected[0]["title"] in {"Хоббит", "Волшебник Земноморья"}
    assert bot.sent == []


def test_music_genre_selection_stays_in_the_selected_genre(monkeypatch):
    calls = []

    async def send_listen(bot, cid, **kwargs):
        calls.append((cid, kwargs))

    monkeypatch.setattr(leisure_music, "send_listen", send_listen)
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie"])
    asyncio.run(leisure_music.send_music_by_genre(object(), "42", "indie", status="status"))

    assert calls == [("42", {"category": {
        "kind": "genre", "value": "indie", "label": "🌿 Инди",
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


def test_book_card_has_complete_description_and_reader_rating():
    message = leisure_books._book_text({
        "title": "Night Night Fawn", "desc": "Город в вечной темноте, где мечты становятся оружием",
        "plot": "Герой пытается спасти сестру из подпольного рынка снов",
        "rating": 4.7, "ratings_count": 1234,
        "why": ["Необычный мир"],
    })
    assert "Город в вечной темноте, где мечты становятся оружием." in message.text
    assert "Герой пытается спасти сестру из подпольного рынка снов." in message.text
    assert "Коротко о сюжете: Герой пытается спасти сестру из подпольного рынка снов." in message.text
    assert "⭐ Оценка читателей: 4.7/5 · 1 234 оценок" in message.text
    assert "Почему стоит читать:" in message.text


def test_book_card_title_links_to_google_books():
    message = leisure_books._book_text({
        "title": "Night Night Fawn",
        "url": "https://books.google.com/books?id=test",
    })

    assert any(
        entity.type == MessageEntity.TEXT_LINK
        and entity.url == "https://books.google.com/books?id=test"
        for entity in message.entities
    )


def test_category_week_screens_are_compact_and_show_only_content():
    movie = leisure_movies.leisure_ui.movie_now_playing_screen("Алкмар", [{
        "title": "Фильм", "genres": ["drama", "thriller"],
        "trailer_url": "https://www.youtube.com/watch?v=trailer123",
        "overview": "Героиня возвращается домой и находит старую тайну",
    }], {
        "rebus": {
            "emoji": "🦈 🌊 👨‍🔬", "answer": "Челюсти",
            "fact": "«Челюсти» считают первым современным летним блокбастером.",
        },
        "birthday": {
            "name": "Грета Гервиг", "birth": "+1983-08-04T00:00:00Z", "role": "режиссёр и актриса",
            "fact": "«Леди Бёрд» принесла ей две номинации на «Оскар».",
        },
    })
    books = leisure_movies.leisure_ui.weekly_books_screen("Алкмар", {
        "rebus": {"emoji": "🧙‍♀️ ⚡ 🚂", "answer": "Гарри Поттер", "fact": "Факт."},
        "birthday": {"name": "Кнут Гамсун", "birth": "1859-08-04", "detail": "норвежский писатель"},
    }, [{
        "title": "Onyx Storm", "author": "Ребекка Яррос",
        "summary": "Вайолет ищет союзников, пока война всё ближе к её дому.",
    }])
    music = leisure_movies.leisure_ui.music_week_screen("Алкмар", {
        "rebus": {"emoji": "👑 🐝 🎤", "answer": "Beyoncé", "fact": "Факт."},
        "legend": {"name": "Луи Армстронг", "birth": "1901-08-04", "detail": "трубач и певец"},
    },
        [{"artist": "Romy", "date": "21 августа", "place": "Биддингхёйзен"}],
    )

    assert "🎬 Кино на сегодня · Алкмар" in movie.text
    assert "Ребус дня: 🦈 🌊 👨‍🔬 → Челюсти" in movie.text
    assert "Именинник дня: Грета Гервиг · 4 августа 1983 — режиссёр и актриса. «Леди Бёрд» принесла ей две номинации на «Оскар»." in movie.text
    assert "Фильм под настроение:" not in movie.text
    assert "Что в кино:\n• «Фильм» (драма, триллер) · Героиня возвращается домой и находит старую тайну." in movie.text
    movie_link = next(entity for entity in movie.entities if entity.type == MessageEntity.TEXT_LINK)
    assert movie_link.url == "https://www.youtube.com/watch?v=trailer123"
    assert "💡 Интересно: «Челюсти» считают первым современным летним блокбастером." in movie.text
    assert movie.text.index("Что в кино:") < movie.text.index("Именинник дня:") < movie.text.index("💡 Интересно:")
    assert movie.rich_message is None
    assert any(entity.type == MessageEntity.SPOILER for entity in movie.entities)
    assert "📚 Литературный вайб · Алкмар" in books.text
    assert "Цитата со страницы:" not in books.text
    assert "Литературный ребус: 🧙‍♀️ ⚡ 🚂 → Гарри Поттер" in books.text
    assert "Именинник дня: Кнут Гамсун · 4 августа 1859 — норвежский писатель." in books.text
    assert "Книга под настроение:" not in books.text
    assert (
        "Главные премьеры:\n• «Onyx Storm» (Ребекка Яррос) · "
        "Вайолет ищет союзников, пока война всё ближе к её дому."
    ) in books.text
    assert books.text.index("Главные премьеры:") < books.text.index("Именинник дня:") < books.text.index("💡 Интересно:")
    assert any(entity.type == MessageEntity.SPOILER for entity in books.entities)
    assert "🎧 Музыка этой недели · Алкмар" in music.text
    assert "Вайб дня" not in music.text
    assert "Музыкальный ребус: 👑 🐝 🎤 → Beyoncé" in music.text
    assert "Именинник дня: Луи Армстронг · 4 августа 1901 — трубач и певец." in music.text
    assert "Концерты рядом:\n• Romy - 21 августа · Биддингхёйзен" in music.text
    assert music.text.index("Именинник дня:") < music.text.index("💡 Интересно:")
    assert "Новые альбомы" not in music.text
    assert any(entity.type == MessageEntity.SPOILER for entity in music.entities)


def test_books_home_opens_daily_literary_screen_not_a_recommendation(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def daily():
        return {"rebus": {"emoji": "🧙 ⚡", "answer": "Гарри Поттер", "fact": "Факт."}}

    async def premieres():
        return [{"title": "Премьера", "author": "Автор", "vibe": "фэнтези"}]

    monkeypatch.setattr(leisure_books, "_daily_book_content", daily)
    monkeypatch.setattr(leisure_books, "get_weekly_new_books", premieres)
    monkeypatch.setattr(leisure_books, "_book_city", lambda _cid: "Алкмар")

    asyncio.run(leisure_books.send_books_home(Bot(), "42"))

    assert "📚 Литературный вайб · Алкмар" in sent[0]["text"]
    assert "Литературный ребус: 🧙 ⚡ → Гарри Поттер" in sent[0]["text"]
    assert _labels(sent[0]["reply_markup"])[0] == ["✨ Подобрать новую книгу"]


def test_premiere_screens_are_compact_and_keep_book_links():
    movie = leisure_movies.leisure_ui.movie_premieres_screen("Нидерланды", "13–26 августа", [{
        "title": "Премьера", "date": "2026-08-15", "genres": "Драма, комедия",
        "overview": "Семья пытается сохранить дом после большого наводнения",
    }])
    books = leisure_books.leisure_ui.book_premieres_screen("Август 2026", [{
        "title": "Новая книга", "author": "Автор", "summary": "Героиня ищет сестру в незнакомом городе",
        "published_date": "2026-08-15", "url": "https://books.google.com/books?id=new",
    }])

    assert "Премьеры в кино · Нидерланды" in movie.text
    assert "«Премьера»\nдрама · комедия\nПремьера: 15 августа 2026" in movie.text
    assert "Семья пытается сохранить дом после большого наводнения." in movie.text
    assert "Премьеры книг · Август 2026" in books.text
    assert "«Новая книга»\nАвтор\nПремьера: 15 августа 2026" in books.text
    assert "Героиня ищет сестру в незнакомом городе." in books.text
    assert any(entity.type == MessageEntity.TEXT_LINK and entity.url.endswith("id=new") for entity in books.entities)


def test_movie_home_opens_daily_cinema_screen(monkeypatch):
    calls = []

    async def cinema(bot, cid, *, q=None, status=None):
        calls.append((bot, cid, q, status))

    monkeypatch.setattr(leisure_movies, "send_movie_now_playing", cinema)

    asyncio.run(leisure_movies.send_movie_home("bot", "42", q="query", status="status"))

    assert calls == [("bot", "42", "query", "status")]


def test_daily_category_block_titles_are_bold():
    movie = leisure_movies.leisure_ui.movie_now_playing_screen("Алкмар", [{
        "title": "Фильм", "genres": ["drama"],
    }], {
        "rebus": {"emoji": "🦈", "answer": "Челюсти", "fact": "Факт."},
        "birthday": {"name": "Имя", "role": "актёр", "fact": "Интересный факт."},
    })
    books = leisure_movies.leisure_ui.weekly_books_screen("Алкмар", {
        "rebus": {"emoji": "📚", "answer": "Ответ", "fact": "Факт."},
        "birthday": {"name": "Имя", "detail": "писатель"},
    }, [{"title": "Премьера", "author": "Автор", "vibe": "жанр"}])
    music = leisure_movies.leisure_ui.music_week_screen("Алкмар", {
        "rebus": {"emoji": "🎤", "answer": "Ответ", "fact": "Факт."},
        "legend": {"name": "Имя", "detail": "музыкант"},
    }, [{"artist": "Артист", "date": "Сегодня", "place": "Алкмар"}])

    assert {"Ребус дня:", "Именинник дня:",
            "Что в кино:", "💡 Интересно:"}.issubset(_bold_values(movie))
    assert {"Литературный ребус:", "Именинник дня:",
            "Главные премьеры:", "💡 Интересно:"}.issubset(_bold_values(books))
    assert {"Музыкальный ребус:", "Именинник дня:",
            "Концерты рядом:", "💡 Интересно:"}.issubset(_bold_values(music))
    assert "Вайб дня:" not in _bold_values(music)


def test_book_quote_uses_the_my_day_italic_format():
    message = leisure_books._book_text({"title": "1984", "quote": "Война - это мир."})
    assert "💭 «Война - это мир.»" in message.text
    assert any(entity.type == MessageEntity.ITALIC for entity in message.entities)


def test_local_cinema_catalogue_is_reused_for_a_week(monkeypatch):
    cache, calls = {}, {"cinema": 0, "tmdb": 0}

    def mutate(key, change):
        value, result = change(cache.get(key) or {})
        cache[key] = value
        return result

    def local_movies(*_args, **_kwargs):
        calls["cinema"] += 1
        return [leisure_movies.local_cinema.LocalCinemaMovie("Film")]

    def movie_meta(*_args, **_kwargs):
        calls["tmdb"] += 1
        return {"name": "Фильм", "year": 2026, "rating": 7.7, "vote_count": 100, "genre_ids": [18]}

    monkeypatch.setattr(leisure_movies.store, "_load", lambda key: cache.get(key))
    monkeypatch.setattr(leisure_movies.store, "mutate_kv", mutate)
    monkeypatch.setattr(leisure_movies, "_movie_city", lambda _cid: "Алкмар")
    monkeypatch.setattr(leisure_movies, "_movie_prefs", lambda _cid: {})
    monkeypatch.setattr(leisure_movies.local_cinema, "get_city_movies", local_movies)
    monkeypatch.setattr(leisure_movies.tmdb, "search_id", movie_meta)
    monkeypatch.setattr(leisure_movies.config, "TMDB_API_KEY", "test-key")
    monkeypatch.setattr(leisure_movies, "_now_playing_week_key", lambda: "2026-W30", raising=False)

    asyncio.run(leisure_movies.get_local_now_playing("42", limit=3))
    asyncio.run(leisure_movies.get_local_now_playing("42", limit=3))
    assert calls == {"cinema": 1, "tmdb": 1}


def test_lille_cinema_uses_current_french_theatrical_releases_when_local_listing_is_empty(monkeypatch):
    requested = {}
    regional_movies = [
        leisure_movies.tmdb.CinemaMovie(
            id=1, title="Премьера", original_title="Premiere", overview=None,
            poster_url=None, release_date=date(2026, 8, 1), genres=["драма"],
            rating=7.2, popularity=80, country_code="FR", is_theatrical=True, vote_count=200,
        ),
    ]
    monkeypatch.setattr(leisure_movies.store, "get_settings", lambda _cid: {
        "city": "Лилль", "country": "Франция", "cc": "FR",
    })
    monkeypatch.setattr(leisure_movies, "_movie_prefs", lambda _cid: {})
    monkeypatch.setattr(leisure_movies, "_now_playing_catalog_get", lambda *_args: None)
    monkeypatch.setattr(leisure_movies, "_now_playing_catalog_set", lambda *_args: None)
    monkeypatch.setattr(leisure_movies.local_cinema, "get_city_movies", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(leisure_movies.config, "TMDB_API_KEY", "test-key")

    def now_playing(country, language, *, max_results):
        requested.update(country=country, language=language, max_results=max_results)
        return regional_movies

    monkeypatch.setattr(leisure_movies.tmdb, "get_now_playing", now_playing)

    result = asyncio.run(leisure_movies.get_local_now_playing("42", limit=3))

    assert requested == {"country": "FR", "language": "fr-FR", "max_results": 20}
    assert [item["title"] for item in result] == ["Премьера"]


def test_movie_home_keeps_only_well_known_current_releases():
    featured = leisure_movies._featured_now_playing([
        {"title": "Менее популярный", "rating": 7.3, "vote_count": 950, "popularity": 20},
        {"title": "Хит", "rating": 7.3, "vote_count": 950, "popularity": 80},
        {"title": "Мало голосов", "rating": 9.0, "vote_count": 5},
        {"title": "Слабая оценка", "rating": 6.2, "vote_count": 900},
    ])
    assert [item["title"] for item in featured] == ["Хит", "Менее популярный"]


def test_movie_home_shows_three_popular_local_premieres_with_trailer_links(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def local_movies(_cid, *, limit):
        assert limit == 20
        return [
            {"id": 1, "title": "Первый", "genres": ["drama"], "rating": 7.0,
             "vote_count": 100, "popularity": 90},
            {"id": 2, "title": "Второй", "genres": ["comedy"], "rating": 7.0,
             "vote_count": 100, "popularity": 80},
            {"id": 3, "title": "Третий", "genres": ["thriller"], "rating": 7.0,
             "vote_count": 100, "popularity": 70},
            {"id": 4, "title": "Четвёртый", "genres": ["action"], "rating": 7.0,
             "vote_count": 100, "popularity": 60},
        ]

    async def cinema_day():
        return {"rebus": {"emoji": "🎬", "answer": "Ответ"}}

    monkeypatch.setattr(leisure_movies, "get_local_now_playing", local_movies)
    monkeypatch.setattr(leisure_movies, "_daily_cinema_content", cinema_day)
    monkeypatch.setattr(leisure_movies, "_movie_city", lambda _cid: "Алкмар")
    monkeypatch.setattr(
        leisure_movies.tmdb, "trailer_url",
        lambda movie_id, _kind: f"https://www.youtube.com/watch?v=trailer{movie_id}",
    )

    asyncio.run(leisure_movies.send_movie_now_playing(Bot(), "42"))

    assert "• «Первый» (драма)" in sent[0]["text"]
    assert "• «Второй» (комедия)" in sent[0]["text"]
    assert "• «Третий» (триллер)" in sent[0]["text"]
    assert "Четвёртый" not in sent[0]["text"]
    links = [entity.url for entity in sent[0]["entities"] if entity.type == MessageEntity.TEXT_LINK]
    assert links == [
        "https://www.youtube.com/watch?v=trailer1",
        "https://www.youtube.com/watch?v=trailer2",
        "https://www.youtube.com/watch?v=trailer3",
    ]
    assert sent[0]["disable_web_page_preview"] is True


def test_cinema_rebus_changes_with_calendar_day():
    today = date(2026, 8, 4)

    assert leisure_movies._daily_rebus(today)["answer"] == "Челюсти"
    assert leisure_movies._daily_rebus(today + timedelta(days=1))["answer"] != "Челюсти"


def test_cinema_birthday_uses_one_shared_daily_cache(monkeypatch):
    today = date(2026, 8, 4)
    cache = {
        today.isoformat(): {
            "version": leisure_movies._CINEMA_BIRTHDAY_CACHE_VERSION,
            "birthday": {"name": "Грета Гервиг", "birth": "1983-08-04", "role": "режиссёр"},
        },
    }
    monkeypatch.setattr(leisure_movies.store, "_load", lambda _key: cache)
    monkeypatch.setattr(leisure_movies.requests, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))

    assert leisure_movies._load_cinema_birthday(today) == {
        "name": "Грета Гервиг", "birth": "1983-08-04", "role": "режиссёр",
    }


def test_cinema_birthday_does_not_retry_an_empty_daily_cache(monkeypatch):
    today = date(2026, 8, 5)
    cache = {today.isoformat(): {
        "version": leisure_movies._CINEMA_BIRTHDAY_CACHE_VERSION,
        "birthday": {},
    }}
    monkeypatch.setattr(leisure_movies.store, "_load", lambda _key: cache)
    monkeypatch.setattr(leisure_movies.requests, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))

    assert leisure_movies._load_cinema_birthday(today) == {}


def test_book_birthday_uses_the_shared_daily_cache_without_retry(monkeypatch):
    today = date(2026, 8, 5)
    cache = {today.isoformat(): {
        "version": leisure_books._BOOK_BIRTHDAY_CACHE_VERSION,
        "birthday": {},
    }}
    monkeypatch.setattr(leisure_books.store, "_load", lambda _key: cache)
    monkeypatch.setattr(leisure_books.requests, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))

    assert leisure_books._load_book_birthday(today) == {}


def test_weekly_books_keep_only_current_popular_releases(monkeypatch):
    today = datetime.now(config.TZ).date().isoformat()
    stored = {}

    monkeypatch.setattr(leisure_books.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_books.store, "_save", lambda _key, value: stored.update(value))
    monkeypatch.setattr(leisure_books.google_books, "search_new_releases", lambda *_args: [
        {"title": "Заметная", "published_date": today, "rating": 4.5, "ratings_count": 140},
        {"title": "Без отзывов", "published_date": today, "rating": 4.8, "ratings_count": 2},
        {"title": "Старая", "published_date": "2025-01-01", "rating": 4.9, "ratings_count": 900},
    ])

    items = asyncio.run(leisure_books.get_weekly_new_books())

    assert [item["title"] for item in items] == ["Заметная"]
    assert stored["items"] == items


def test_weekly_books_fall_back_to_this_month_when_week_has_no_hits(monkeypatch):
    today = datetime.now(config.TZ).date()
    stored = {}

    monkeypatch.setattr(leisure_books.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_books.store, "_save", lambda _key, value: stored.update(value))
    monkeypatch.setattr(leisure_books.google_books, "search_new_releases", lambda *_args: [
        {"title": "Премьера месяца", "published_date": f"{today.year}-{today.month:02d}-01",
         "rating": 4.2, "ratings_count": 2},
    ])

    items = asyncio.run(leisure_books.get_weekly_new_books())

    assert [item["title"] for item in items] == ["Премьера месяца"]
    assert items[0]["_showcase"] == "month"
    assert stored["items"] == items


def test_weekly_books_rebuilds_an_empty_daily_cache(monkeypatch):
    today = datetime.now(config.TZ).date()
    stored = {}
    empty_cache = {
        "week": leisure_books._book_week_key(),
        "date": today.isoformat(),
        "items": [],
    }

    monkeypatch.setattr(leisure_books.store, "_load", lambda *_args: empty_cache)
    monkeypatch.setattr(leisure_books.store, "_save", lambda _key, value: stored.update(value))
    monkeypatch.setattr(leisure_books.google_books, "search_new_releases", lambda *_args: [
        {"title": "Новая витрина", "published_date": today.isoformat(),
         "rating": 4.4, "ratings_count": 120},
    ])

    items = asyncio.run(leisure_books.get_weekly_new_books())

    assert [item["title"] for item in items] == ["Новая витрина"]
    assert stored["items"] == items


def test_weekly_books_fall_back_to_recent_popular_releases(monkeypatch):
    today = datetime.now(config.TZ).date()
    stored = {}

    monkeypatch.setattr(leisure_books.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_books.store, "_save", lambda _key, value: stored.update(value))
    monkeypatch.setattr(leisure_books.google_books, "search_new_releases", lambda *_args: [
        {"title": "Популярная премьера", "published_date": str(today.replace(day=1)),
         "rating": 4.6, "ratings_count": 240},
        {"title": "Неудачная", "published_date": "2020-01-01",
         "rating": 4.9, "ratings_count": 5000},
    ])
    monkeypatch.setattr(leisure_books, "_released_this_month", lambda _value: False)

    items = asyncio.run(leisure_books.get_weekly_new_books())

    assert [item["title"] for item in items] == ["Популярная премьера"]
    assert items[0]["_showcase"] == "popular"


def test_weekly_books_never_show_classics_when_catalogue_has_no_fresh_hits(monkeypatch):
    stored = {}

    monkeypatch.setattr(leisure_books.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_books.store, "_save", lambda _key, value: stored.update(value))
    monkeypatch.setattr(leisure_books.google_books, "search_new_releases", lambda *_args: [
        {"title": "Мастер и Маргарита", "published_date": "1967-01-01",
         "rating": 4.9, "ratings_count": 5000},
    ])

    items = asyncio.run(leisure_books.get_weekly_new_books())

    assert items[0]["_showcase"] == "popular"
    assert "Мастер и Маргарита" not in [item["title"] for item in items]


def test_weekly_books_screen_uses_premieres_without_the_old_popular_heading():
    message = leisure_books.leisure_ui.weekly_books_screen("Алкмар", {}, [{
        "title": "Недавний бестселлер", "author": "Автор",
        "summary": "Напряжённый триллер о тайне, которую нельзя оставить в прошлом.",
        "url": "https://books.google.com/books?id=test",
    }])

    assert (
        "Главные премьеры:\n• «Недавний бестселлер» (Автор) · "
        "Напряжённый триллер о тайне, которую нельзя оставить в прошлом."
    ) in message.text
    assert "Популярное чтение" not in message.text
    assert any(
        entity.type == MessageEntity.TEXT_LINK
        and entity.url == "https://books.google.com/books?id=test"
        for entity in message.entities
    )


def test_book_showcase_falls_back_to_google_books_search_link():
    item = leisure_books._books_with_premiere_summaries([{
        "title": "Недавний бестселлер", "author": "Автор",
    }])[0]

    assert item["url"] == "https://books.google.com/books?q=%D0%9D%D0%B5%D0%B4%D0%B0%D0%B2%D0%BD%D0%B8%D0%B9+%D0%B1%D0%B5%D1%81%D1%82%D1%81%D0%B5%D0%BB%D0%BB%D0%B5%D1%80+%D0%90%D0%B2%D1%82%D0%BE%D1%80"
