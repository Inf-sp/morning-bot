import asyncio
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
        ["✨ Подобрать кино"],
        ["🎭 По жанру"],
        ["🎚️ Моё кино"],
        ["#️⃣ Главная"],
    ]
    assert _labels(leisure_books.books_home_keyboard()) == [
        ["✨ Подобрать книгу"],
        ["🎭 По жанру"],
        ["🎚️ Мои книги"],
        ["#️⃣ Главная"],
    ]
    assert _labels(leisure_music.music_home_keyboard()) == [
        ["✨ Подобрать новую музыку"],
        ["🎭 По жанру"],
        ["🎫 Концерты"],
        ["🎚️ Мои артисты"],
        ["#️⃣ Главная"],
    ]


def test_recommendation_cards_use_content_specific_next_labels():
    assert _labels(leisure_movies._movie_kb(0))[0] == ["✨ Подобрать другое кино"]
    assert _labels(leisure_books._book_kb(0))[0] == ["✨ Другая книга"]
    assert _labels(leisure_music._listen_kb())[0] == ["✨ Другой артист"]
    assert _labels(leisure_books._book_kb(0))[1] == ["🎭 По жанру"]
    assert _labels(leisure_movies._movie_kb(0))[1] == ["🎭 По жанру"]
    assert _labels(leisure_music._listen_kb())[2] == ["🎭 По жанру"]
    assert _labels(leisure_movies._movie_kb(0))[-1] == ["⬅️ Назад", "#️⃣ Главная"]


def test_preferences_are_available_from_personal_content_lists():
    assert _labels(leisure_movies._movie_prefs_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert cleanup.COLLECTIONS["cinema_favorites"]["menu_button"] == ("📌 Предпочтения", "movie_prefs")
    assert cleanup.COLLECTIONS["cinema_favorites"]["add_button_at_bottom"] is True
    assert cleanup.COLLECTIONS["cinema_favorites"]["allow_edit"] is False
    assert _labels(leisure_books._book_preferences_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert cleanup.COLLECTIONS["books_favorites"]["menu_button"] == ("📌 Предпочтения", "book_prefs")
    assert cleanup.COLLECTIONS["books_favorites"]["add_button_at_bottom"] is True
    assert cleanup.COLLECTIONS["books_favorites"]["allow_edit"] is False
    assert _labels(leisure_music._music_preferences_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert cleanup.COLLECTIONS["music_favorite_artists"]["menu_button"] == ("📌 Предпочтения", "music_prefs")
    assert cleanup.COLLECTIONS["music_favorite_artists"]["add_button_at_bottom"] is True
    assert cleanup.COLLECTIONS["music_favorite_artists"]["allow_edit"] is False


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


def test_only_the_movie_recommendation_card_offers_a_back_button():
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
        assert all("⬅️ Назад" not in sum(_labels(keyboard), []) for keyboard in keyboards)
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
        ["🌿 Инди", "🎸 Рок"], ["#️⃣ Главная"],
    ]


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


def test_book_card_has_complete_description_and_reader_rating():
    message = leisure_books._book_text({
        "title": "Night Night Fawn", "desc": "Город в вечной темноте, где мечты становятся оружием",
        "plot": "Герой пытается спасти сестру из подпольного рынка снов",
        "rating": 4.7, "ratings_count": 1234,
    })
    assert "Город в вечной темноте, где мечты становятся оружием." in message.text
    assert "Герой пытается спасти сестру из подпольного рынка снов." in message.text
    assert "⭐ Оценка читателей: 4.7/5 · 1 234 оценок" in message.text


def test_category_week_screens_are_compact_and_show_only_content():
    movie = leisure_movies.leisure_ui.movie_now_playing_screen("Алкмар", [{
        "title": "Фильм", "genres": ["drama", "thriller"],
    }], {
        "rebus": {
            "emoji": "🦈 🌊 👨‍🔬", "answer": "Челюсти",
            "fact": "«Челюсти» считают первым современным летним блокбастером.",
        },
        "birthday": {"name": "Грета Гервиг", "role": "режиссёр и актриса"},
        "mood": "Дождь за окном? «Глубокий сон» — тихий нуар для серого вечера.",
    })
    books = leisure_movies.leisure_ui.weekly_books_screen("Алкмар", {
        "rebus": {"emoji": "🧙‍♀️ ⚡ 🚂", "answer": "Гарри Поттер", "fact": "Факт."},
        "birthday": {"name": "Кнут Гамсун", "detail": "норвежский писатель"},
        "mood": "Дождливый вечер? Плотный детектив для серого вечера.",
    }, [{
        "title": "Onyx Storm", "author": "Ребекка Яррос",
        "vibe": "драконы, политика и тёмный фэнтези-мир",
    }])
    music = leisure_movies.leisure_ui.music_week_screen("Алкмар", {
        "vibe": {"track": "Introvert", "artist": "Little Simz", "tag": "Для собранного фокуса"},
        "rebus": {"emoji": "👑 🐝 🎤", "answer": "Beyoncé", "fact": "Факт."},
        "legend": {"name": "Луи Армстронг", "detail": "трубач и певец"},
    },
        [{"artist": "Romy", "date": "21 августа", "place": "Биддингхёйзен"}],
    )

    assert "🎬 Кино на сегодня · Алкмар" in movie.text
    assert "Ребус дня: 🦈 🌊 👨‍🔬 → Челюсти" in movie.text
    assert "Именинник дня: Грета Гервиг — режиссёр и актриса." in movie.text
    assert "Фильм под настроение: Дождь за окном?" in movie.text
    assert "Что в кино: Фильм (драма, триллер)" in movie.text
    assert "💡 Факт дня: «Челюсти» считают первым современным летним блокбастером." in movie.text
    assert movie.rich_message is None
    assert any(entity.type == MessageEntity.SPOILER for entity in movie.entities)
    assert "📚 Литературный вайб · Алкмар" in books.text
    assert "Цитата со страницы:" not in books.text
    assert "Литературный ребус: 🧙‍♀️ ⚡ 🚂 → Гарри Поттер" in books.text
    assert "Именинник дня: Кнут Гамсун — норвежский писатель." in books.text
    assert "Книга под настроение: Дождливый вечер?" in books.text
    assert "Главные премьеры: «Onyx Storm» · Ребекка Яррос (драконы, политика и тёмный фэнтези-мир)" in books.text
    assert any(entity.type == MessageEntity.SPOILER for entity in books.entities)
    assert "🎧 Музыка этой недели · Алкмар" in music.text
    assert "Вайб дня: Introvert — Little Simz" in music.text
    assert "Музыкальный ребус: 👑 🐝 🎤 → Beyoncé" in music.text
    assert "Легенда дня: Луи Армстронг — трубач и певец." in music.text
    assert "Концерты рядом: Romy · 21 августа · Биддингхёйзен" in music.text
    assert "Новые альбомы" not in music.text
    assert any(entity.type == MessageEntity.SPOILER for entity in music.entities)


def test_daily_category_block_titles_are_bold():
    movie = leisure_movies.leisure_ui.movie_now_playing_screen("Алкмар", [{
        "title": "Фильм", "genres": ["drama"],
    }], {
        "rebus": {"emoji": "🦈", "answer": "Челюсти", "fact": "Факт."},
        "birthday": {"name": "Имя", "role": "актёр"}, "mood": "Настроение.",
    })
    books = leisure_movies.leisure_ui.weekly_books_screen("Алкмар", {
        "rebus": {"emoji": "📚", "answer": "Ответ", "fact": "Факт."},
        "birthday": {"name": "Имя", "detail": "писатель"}, "mood": "Настроение.",
    }, [{"title": "Премьера", "author": "Автор", "vibe": "жанр"}])
    music = leisure_movies.leisure_ui.music_week_screen("Алкмар", {
        "vibe": {"track": "Трек", "artist": "Артист", "tag": "тег"},
        "rebus": {"emoji": "🎤", "answer": "Ответ", "fact": "Факт."},
        "legend": {"name": "Имя", "detail": "музыкант"},
    }, [{"artist": "Артист", "date": "Сегодня", "place": "Алкмар"}])

    assert {"Ребус дня:", "Именинник дня:", "Фильм под настроение:",
            "Что в кино:", "💡 Факт дня:"}.issubset(_bold_values(movie))
    assert {"Литературный ребус:", "Именинник дня:",
            "Книга под настроение:", "Главные премьеры:", "💡 Интересно:"}.issubset(_bold_values(books))
    assert {"Вайб дня:", "Музыкальный ребус:", "Легенда дня:",
            "Концерты рядом:", "💡 Факт дня:"}.issubset(_bold_values(music))


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


def test_movie_home_keeps_only_well_known_current_releases():
    featured = leisure_movies._featured_now_playing([
        {"title": "Хит", "rating": 7.3, "vote_count": 950},
        {"title": "Мало голосов", "rating": 9.0, "vote_count": 5},
        {"title": "Слабая оценка", "rating": 6.2, "vote_count": 900},
    ])
    assert [item["title"] for item in featured] == ["Хит"]


def test_cinema_rebus_changes_with_calendar_day():
    today = date(2026, 8, 4)

    assert leisure_movies._daily_rebus(today)["answer"] == "Челюсти"
    assert leisure_movies._daily_rebus(today + timedelta(days=1))["answer"] != "Челюсти"


def test_cinema_birthday_uses_one_shared_daily_cache(monkeypatch):
    today = date(2026, 8, 4)
    cache = {
        today.isoformat(): {"birthday": {"name": "Грета Гервиг", "role": "режиссёр"}},
    }
    monkeypatch.setattr(leisure_movies.store, "_load", lambda _key: cache)
    monkeypatch.setattr(leisure_movies.requests, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))

    assert leisure_movies._load_cinema_birthday(today) == {
        "name": "Грета Гервиг", "role": "режиссёр",
    }


def test_cinema_birthday_does_not_retry_an_empty_daily_cache(monkeypatch):
    today = date(2026, 8, 5)
    cache = {today.isoformat(): {"birthday": {}}}
    monkeypatch.setattr(leisure_movies.store, "_load", lambda _key: cache)
    monkeypatch.setattr(leisure_movies.requests, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))

    assert leisure_movies._load_cinema_birthday(today) == {}


def test_book_birthday_uses_the_shared_daily_cache_without_retry(monkeypatch):
    today = date(2026, 8, 5)
    cache = {today.isoformat(): {"birthday": {}}}
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
        "title": "Недавний бестселлер", "author": "Автор", "vibe": "триллер",
    }])

    assert "Главные премьеры: «Недавний бестселлер» · Автор (триллер)" in message.text
    assert "Популярное чтение" not in message.text
