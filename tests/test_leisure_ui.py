import asyncio
import json
import os
from datetime import date, datetime, timedelta

from telegram import MessageEntity

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import cleanup
import config
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


def test_global_preferences_has_all_recommendation_sections():
    bot = RecordingBot()
    asyncio.run(settings.send_preferences(bot, "42"))

    assert _labels(bot.sent[0]["reply_markup"]) == [
        ["☀️ Мой день"], ["🧠 Обучение"], ["🥣 Кухни"], ["🧵 Стиль"], ["🎧 Музыка"],
        ["🎬 Кино"], ["⬅️ Назад", "#️⃣ Главная"],
    ]
    # Сводка всех предпочтений текстом: каждый раздел — своя строка.
    for label in ("Обучение:", "Кухни:", "Ограничения в еде:", "Стиль:", "Музыка:", "Кино:"):
        assert label in bot.sent[0]["text"]


def test_movie_preferences_keep_only_type_recency_and_rating(monkeypatch):
    monkeypatch.setattr(movie_recommendation.settings, "get", lambda *_args: "")

    rows = _labels(leisure_movies._movie_prefs_kb("42"))

    assert rows == [
        ["□ ⭐️ 6.5"],
        ["□ ⭐️ 7.0"],
        ["□ ⭐️ 7.5"],
        ["□ ⭐️ 8.0"],
        ["✅ Азия"], ["✅ Америка"], ["✅ Европа"], ["✅ Россия"], ["✅ Африка"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]
    assert leisure_movies._movie_prefs("42") == {
        "type_pref": None,
        "recency": None,
        "min_rating": None,
        "regions_off": [],
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

    asyncio.run(leisure_movies.send_recos(object(), "42", "tv"))  # «Сериалы» — строго сериалы

    assert requested == {"kind": "tv", "min_rating": 8.0, "year": 2000}
    assert delivered[0][0]["title"] == "Новый сериал"


def test_films_never_get_a_series_and_series_never_get_a_film(monkeypatch):
    asked, delivered = [], []

    async def deliver(_bot, _cid, item, _index, tm=None, **kwargs):
        delivered.append((tm["kind"], kwargs.get("kind")))

    def discover(kind, *_args, **_kwargs):
        asked.append(kind)
        return [{"id": 1 if kind == "movie" else 2, "name": f"Новый {kind}", "kind": kind, "rating": 8.0,
                 "vote_count": 500, "popularity": 20}]

    monkeypatch.setattr(leisure_movies.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_movies.movie_engine, "_excluded_norms", lambda _cid, **_kwargs: set())
    monkeypatch.setattr(leisure_movies.movie_engine, "mark_shown", lambda *_args: None)
    monkeypatch.setattr(movie_recommendation, "_movie_prefs", lambda _cid: {"min_rating": None})
    monkeypatch.setattr(leisure_movies.vacation, "trip_cc", lambda _cid: "")
    monkeypatch.setattr(leisure_movies.inclusive_recommendations, "is_due", lambda *_args: False)
    monkeypatch.setattr(leisure_movies, "_cache_movie", lambda *_args: None)
    monkeypatch.setattr(leisure_movies.tmdb, "discover", discover)
    monkeypatch.setattr(leisure_movies, "_send_movie_card", deliver)

    asyncio.run(leisure_movies.send_recos(object(), "42", "movie"))
    asyncio.run(leisure_movies.send_recos(object(), "42", "tv"))

    assert set(asked) == {"movie", "tv"} and asked[0] == "movie"
    assert delivered == [("movie", "movie"), ("tv", "tv")]
    assert leisure_movies._rec_key("42", "tv") != leisure_movies._rec_key("42", "movie")


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
    monkeypatch.setattr(leisure_movies, "_cached_movie", lambda *_args: None)
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

    # Без TMDb тип не проверить — карточка дня не кэшируется, открытие пойдёт живым подбором.
    assert asyncio.run(leisure_movies.get_current_movie("42")) == (None, None)


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
    assert bot.messages[0]["text"].startswith("🎚️ Моё кино · 2 фильма/сериала\n\n")
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
        ["❌ Удалить"], ["⬅️ Назад", "#️⃣ Главная"],
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


def test_favorite_movies_use_main_genres_with_animation():
    assert leisure_movies._FAVORITE_MOVIE_GENRES == (
        "Комедия", "Ужасы", "Фантастика", "Триллер", "Романтика", "Драма", "Анимация",
    )
    # Аниме и мультфильмы — в «Анимацию», даже если она не первый жанр.
    assert leisure_movies._favorite_movie_genre({"genres": "анимация, семейный"}) == "Анимация"
    assert leisure_movies._favorite_movie_genre({"genres": "комедия, анимация"}) == "Анимация"
    assert leisure_movies._favorite_movie_genre({"genres": "фантастика", "genre_ids": [10765, 16]}) == "Анимация"
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


def test_personal_lists_are_available_from_their_category_preferences():
    assert _labels(leisure_music._music_preferences_kb("42"))[0] == ["⬜ Инди"]


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


def test_favorite_artists_are_grouped_by_genre_without_no_genre_category(monkeypatch):
    monkeypatch.setattr(leisure_music, "_cached_artist", lambda _cid: {})
    items = [
        ("unknown", "Unknown Artist"),
        ("indie", "Big Thief"),
        ("pop", "Caroline Polachek"),
    ]

    grouped = leisure_music.group_favorite_artist_items("42", items)

    assert [leisure_music.favorite_artist_genre("42", label) for _id, label in grouped] == [
        "Другие артисты", "Инди", "Поп",
    ]
    assert "Без жанра" not in {
        leisure_music.favorite_artist_genre("42", label) for _id, label in grouped
    }


def test_music_genre_menu_shows_only_selected_styles(monkeypatch):
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie", "rock"])

    assert _labels(leisure_music._music_genre_menu_kb("42")) == [
        ["🆕 Новинка"], ["Инди"], ["Рок"], ["⬅️ Назад", "#️⃣ Главная"],
    ]


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


def test_weekly_concerts_card_lists_my_artists_then_popular():
    message = leisure_movies.leisure_ui.weekly_concerts_card(
        [{"artist": "Placebo", "title": "Placebo: Never Let Me Go Tour", "date": "2026-10-27",
          "url": "https://t.example/placebo", "venue": "Ziggo Dome", "city": "Amsterdam",
          "about": "Культовый альтернативный рок."}],
        [{"title": "Muse", "date": "2026-11-12", "url": "", "venue": "GelreDome", "city": "Arnhem"}],
    )

    assert message.text == (
        "🎫 Концерты недели\n\n"
        "Твои артисты:\n• Placebo — 27 октября, Ziggo Dome. Культовый альтернативный рок.\n\n"
        "Популярное рядом:\n• Muse — 12 ноября, GelreDome."
    )
    assert [entity.url for entity in message.entities if entity.type == MessageEntity.TEXT_LINK] == [
        "https://t.example/placebo",
    ]


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


def test_cinema_rebus_changes_with_calendar_day():
    today = date(2026, 8, 4)

    assert leisure_movies._daily_rebus(today)["answer"] == "Челюсти"
    assert leisure_movies._daily_rebus(today + timedelta(days=1))["answer"] != "Челюсти"


def test_tv_detail_line_uses_russian_plurals():
    from ui.leisure import _detail_line

    expected = {
        1: ("1 сезон", "1 серия"), 2: ("2 сезона", "2 серии"), 5: ("5 сезонов", "5 серий"),
        11: ("11 сезонов", "11 серий"), 21: ("21 сезон", "21 серия"), 22: ("22 сезона", "22 серии"),
    }
    for n, (seasons, episodes) in expected.items():
        line = _detail_line({"kind": "tv", "seasons": n, "episodes": n})
        assert line == f"{seasons} • {episodes}"


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
    assert click("movie_card_3")[0].inline_keyboard[1][0].callback_data == "movie_pick_3"
    # Прежнюю карточку удаляет роутер, когда пришла новая (_replaced); без новой — её не трогаем.
    assert click("movie_g_35") == [] and shown == ["35"]


class _NoStatus:
    async def replace(self, *_a, **_k):
        return True

    async def stop(self, delete=True):
        return None


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
    assert "Посмотреть трейлер" not in msg.text
    link = next(e for e in msg.entities if e.type == "text_link")
    assert link.url == "https://www.youtube.com/watch?v=abc"
    utf16 = msg.text.encode("utf-16-le")
    assert utf16[link.offset * 2:(link.offset + link.length) * 2].decode("utf-16-le") == "Дюна"


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


def test_preferences_summary_lists_every_section(monkeypatch):
    values = {"cuisines": ["italian"], "food_restrictions": ["no_pork"], "movie_type_pref": "tv",
              "movie_min_rating": "7.0", "book_recency": "new"}
    monkeypatch.setattr(settings, "get", lambda _cid, key, default=None: values.get(key, default))
    monkeypatch.setattr(settings.store, "learning_is_enabled", lambda _cid: False)
    monkeypatch.setattr(settings, "style_groups", lambda _cid: ["casual", "classic"])

    summary = dict(settings.preferences_summary("42"))

    assert summary["Обучение"] == "не изучаю"
    assert summary["Кухни"] == "Итальянская" and summary["Ограничения в еде"] == "Без свинины"
    assert summary["Стиль"] == "Повседневный · Классический" and summary["Музыка"] == "не выбрано"
    assert summary["Кино"] == "от ⭐ 7.0" and "Книги" not in summary




def test_recommendation_cards_have_no_refresh_label():
    assert "✨ Обновить" not in sum(_labels(leisure_movies._movie_kb(0)), [])
    assert "✨ Обновить" not in sum(_labels(leisure_music._listen_kb()), [])
    # «По жанру» открывает выбор жанра под карточкой; кнопки «Выбрать жанр» нет.
    for keyboard, other, callback in (
        (leisure_movies._movie_kb(0), "По жанру", "movie_pick_0"),
        (leisure_music._listen_kb(), "По жанру", "music_pick"),
    ):
        second = keyboard.inline_keyboard[1][0]
        assert (second.text, second.callback_data) == (other, callback)
        assert all("Выбрать жанр" not in b.text for row in keyboard.inline_keyboard for b in row)
        assert keyboard.inline_keyboard[-1][0].callback_data == "m_leisure"
    # «Не интересно» — красная, первой строкой, вместо «Добавить в Моё…».
    for keyboard, callback in (
        (leisure_movies._movie_kb(3), "movie_no_3"),
        (leisure_movies._movie_kb(3, kind="tv"), "tv_no_3"),
        (leisure_music._listen_kb(), "listen_no"),
    ):
        dislike = keyboard.inline_keyboard[0][0]
        assert (dislike.text, dislike.callback_data, dislike.api_kwargs) == (
            "Не интересно", callback, {"style": "danger"},
        )
        # «Настроить» на карточках нет — библиотека настраивается из хаба.
        assert all("Настроить" not in b.text for row in keyboard.inline_keyboard for b in row)
        assert all("Добавить в Мо" not in b.text for row in keyboard.inline_keyboard for b in row)
    assert _labels(leisure_movies._movie_kb(0))[-1] == ["⬅️ Назад", "#️⃣ Главная"]


def test_preferences_are_available_in_personal_content_lists():
    assert _labels(leisure_movies._movie_prefs_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert cleanup.COLLECTIONS["cinema_favorites"]["menu_button"] == ("📝 Выбрать предпочтения", "movie_prefs")
    assert cleanup.COLLECTIONS["cinema_favorites"]["add_button_at_bottom"] is False
    assert cleanup.COLLECTIONS["cinema_favorites"]["allow_edit"] is False
    assert _labels(leisure_music._music_preferences_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert cleanup.COLLECTIONS["music_favorite_artists"]["menu_button"] == ("📝 Выбрать предпочтения", "music_prefs")
    assert cleanup.COLLECTIONS["music_favorite_artists"]["add_button_at_bottom"] is False
    assert cleanup.COLLECTIONS["music_favorite_artists"]["allow_edit"] is False
    assert "books_favorites" not in cleanup.COLLECTIONS  # книг в библиотеке больше нет


def test_leisure_preference_choices_use_one_column():
    keyboards = (leisure_movies._movie_prefs_kb("42"), leisure_music._music_preferences_kb("42"))

    assert all(len(row) == 1 for keyboard in keyboards for row in _labels(keyboard)[:-1])


def test_favorite_movies_home_shows_title_and_one_hint():
    ui = leisure_movies.leisure_ui
    assert ui.favorite_movies_home(3).text == "🎚️ Моё кино · 3 фильма/сериала\n\nПо любимым фильмам и сериалам подбираю похожее."
    assert ui.favorite_movies_home(0).text.startswith("🎚️ Моё кино · 0 фильмов/сериалов\n\nДобавь")


def test_recommendation_subscreens_return_to_their_category_home():
    music_styles = [key for key, _label, _prompt_name in leisure_music._MUSIC_GENRES]
    original_music_styles = leisure_music._music_styles
    leisure_music._music_styles = lambda _cid: music_styles
    keyboards = [
        leisure_movies._movie_genre_menu_kb(),
        leisure_music._listen_kb(), leisure_music._music_genre_menu_kb("42"),
    ]
    try:
        assert all(_labels(keyboard)[-1] == ["⬅️ Назад", "#️⃣ Главная"] for keyboard in keyboards)
        assert _labels(leisure_movies._movie_kb(0))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
        assert _labels(leisure_movies._movie_prefs_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
        assert _labels(leisure_music._music_preferences_kb("42"))[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    finally:
        leisure_music._music_styles = original_music_styles


def test_movies_do_not_offer_generic_bookmarks():
    assert "💾 Сохранения" not in sum(_labels(leisure_movies._movie_kb(0)), [])
    assert "💾 Сохранить" not in sum(_labels(leisure_movies._movie_kb(0)), [])


def test_music_and_movie_genre_menus_have_one_column_without_emoji(monkeypatch):
    monkeypatch.setattr(
        leisure_music, "_music_styles",
        lambda _cid: [key for key, _label, _prompt_name in leisure_music._MUSIC_GENRES],
    )
    assert _labels(leisure_music._music_genre_menu_kb("42"))[:-1] == [
        ["🆕 Новинка"], ["Инди"], ["Поп"], ["Электроника"], ["R&B"], ["Рок"], ["Хип-хоп"],
    ]
    assert _labels(leisure_movies._movie_genre_menu_kb())[:-1] == [
        ["🆕 Новинка"], ["Комедия"], ["Ужасы"], ["Фантастика"],
        ["Триллер"], ["Романтика"], ["Драма"], ["Анимация"],
    ]
