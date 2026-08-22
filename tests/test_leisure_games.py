import asyncio
import os
from datetime import date, datetime, timedelta

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_games
import menu


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _profile_store(monkeypatch):
    profiles = {}
    monkeypatch.setattr(
        leisure_games.store, "get_profile",
        lambda cid: dict(profiles.get(str(cid), {})),
    )
    monkeypatch.setattr(
        leisure_games.store, "mutate_profile",
        lambda cid, change: profiles.__setitem__(
            str(cid), dict(change(dict(profiles.get(str(cid), {})))[0]),
        ),
    )
    return profiles


def test_main_menu_shows_all_four_entertainment_categories():
    labels = _labels(menu.main_menu_kb())

    assert labels[-3:] == [
        ["🎬 Кино", "🎧 Музыка"],
        ["📚 Книги", "👾 Игры"],
        ["🎚️ Настройки"],
    ]


def test_game_recommendation_respects_board_game_preference(monkeypatch):
    _profile_store(monkeypatch)
    monkeypatch.setattr(
        leisure_games.settings, "get",
        lambda _cid, key, default=None: ["board"] if key == "game_platforms" else default,
    )

    item = leisure_games.pick_game("42", refresh=True)

    assert item
    assert item["platform_labels"] == ["🎲 Настолки"]


def test_board_games_open_separately_from_digital_platform_preferences(monkeypatch):
    _profile_store(monkeypatch)
    monkeypatch.setattr(
        leisure_games.settings, "get",
        lambda _cid, key, default=None: ["ps5"] if key == "game_platforms" else default,
    )

    item = leisure_games.pick_game("42", genre="board", refresh=True)

    assert item
    assert item["platform_labels"] == ["🎲 Настолки"]


def test_game_preferences_offer_popular_platforms_years_and_ratings(monkeypatch):
    monkeypatch.setattr(
        leisure_games.settings, "get",
        lambda _cid, key, default=None: ["pc", "ps5"] if key == "game_platforms" else default,
    )

    assert _labels(leisure_games._preferences_keyboard("42")) == [
        ["✅ 💻 ПК"],
        ["✅ 🎮 PS5"],
        ["□ 🟢 Xbox Series"],
        ["□ 🔴 Nintendo Switch"],
        ["□ 📱 Мобильные"],
        ["□ 🎲 Настолки"],
        ["🆕 Новинки"],
        ["📅 2020-е"],
        ["До 2020 года"],
        ["✅ Любые годы"],
        ["⭐ 7.5+"],
        ["⭐ 8.0+"],
        ["⭐ 8.5+"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_game_recommendation_prefers_selected_period_and_rating(monkeypatch):
    _profile_store(monkeypatch)
    values = {
        "game_platforms": ["pc"],
        "game_recency": "classic",
        "game_min_rating": "8.5",
    }
    monkeypatch.setattr(
        leisure_games.settings, "get",
        lambda _cid, key, default=None: values.get(key, default),
    )

    item = leisure_games.pick_game("42", refresh=True)

    assert item["year"] < 2020
    assert item["rating"] >= 8.5


def test_game_home_matches_movie_style_and_keeps_board_games_separate(monkeypatch):
    _profile_store(monkeypatch)
    monkeypatch.setattr(leisure_games.settings, "get", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(leisure_games.store, "get_settings", lambda _cid: {"city": "Alkmaar"})

    async def premieres(_cid, **_kwargs):
        return [{
            "title": "Example Game",
            "genre": "приключение",
            "platform_label": "💻 ПК",
            "url": "https://example.com/game",
            "trailer_url": "https://www.youtube.com/watch?v=game",
        }]

    monkeypatch.setattr(leisure_games, "get_game_premieres", premieres)

    class Status:
        call = None

        async def replace(self, text, **kwargs):
            self.call = (text, kwargs)

    status = Status()
    asyncio.run(leisure_games.send_games_home(object(), "42", status=status))

    assert "👾 Игры на сегодня · Новинки 2026" in status.call[0]
    assert "Новинки лета:" in status.call[0]
    assert "Ребус недели:" in status.call[0]
    assert "💡 Интересно:" in status.call[0]
    assert _labels(status.call[1]["reply_markup"]) == [
        ["✨ Подобрать новую игру"],
        ["🎲 Настолки"],
        ["🎚️ Мой набор игр"],
        ["#️⃣ Главная"],
    ]


def test_game_home_shows_three_linked_releases_with_genres_and_platforms():
    items = [{
        "title": f"Игра {index}",
        "genre": "RPG",
        "platform_label": "💻 ПК · 🎮 PS5",
        "url": f"https://example.com/{index}",
        "trailer_url": f"https://www.youtube.com/watch?v=game{index}",
    } for index in range(4)]

    message = leisure_games.leisure_ui.game_home_screen("Alkmaar", items, {
        "emoji": "🧙 🚪 3️⃣",
        "answer": "Baldur’s Gate 3",
        "fact": "Игровой факт.",
    }, year=2026)

    assert message.text.startswith("👾 Игры на сегодня · Новинки 2026\n\nНовинки лета:")
    assert message.text.count("• Игра ") == 3
    assert "• Игра 0 · RPG · 💻 ПК · 🎮 PS5" in message.text
    assert "Игра 3" not in message.text
    assert {
        entity.url for entity in message.entities if entity.type == "text_link"
    } == {f"https://www.youtube.com/watch?v=game{index}" for index in range(3)}
    assert any(entity.type == "spoiler" for entity in message.entities)


def test_game_home_youtube_fallback_searches_for_official_game_trailer():
    assert leisure_games._youtube_trailer_search_url("Example Game") == (
        "https://www.youtube.com/results?search_query="
        "Example+Game+game+official+trailer"
    )


def test_board_game_name_links_to_youtube_trailer_search():
    item = leisure_games._ensure_game_trailer_url({
        "name": "Каскадия",
        "platforms": ["board"],
        "platform_labels": ["🎲 Настолки"],
        "genre_label": "Настолки",
    })
    message = leisure_games.leisure_ui.game_card(item)

    links = [entity.url for entity in message.entities if entity.type == "text_link"]
    assert links == [
        "https://www.youtube.com/results?search_query="
        "%D0%9A%D0%B0%D1%81%D0%BA%D0%B0%D0%B4%D0%B8%D1%8F+game+official+trailer"
    ]


def test_daily_game_rebus_has_two_facts_without_revealing_answer():
    for rebus in leisure_games._GAME_DAILY_CONTENT:
        fact = rebus["fact"]
        assert fact.count(".") == 2
        assert rebus["answer"].casefold() not in fact.casefold()


def test_game_season_uses_three_calendar_months_and_handles_leap_winter():
    assert leisure_games._game_season(date(2026, 8, 21)) == (
        date(2026, 6, 1), date(2026, 8, 31), "лета",
    )
    assert leisure_games._game_season(date(2026, 9, 1)) == (
        date(2026, 9, 1), date(2026, 11, 30), "осени",
    )
    assert leisure_games._game_season(date(2028, 2, 1)) == (
        date(2027, 12, 1), date(2028, 2, 29), "зимы",
    )


def test_season_games_rotate_when_more_than_three_are_available():
    items = [{"title": str(index)} for index in range(5)]

    first = leisure_games._rotated_season_items(items, date(2026, 8, 21))
    second = leisure_games._rotated_season_items(items, date(2026, 8, 22))

    assert len(first) == len(second) == 3
    assert first != second


def test_game_home_attaches_nearest_release_poster(monkeypatch):
    sent = []
    items = [{
        "title": "Новая игра",
        "genre": "приключение",
        "platform_label": "💻 ПК",
        "url": "https://example.com/game",
        "poster": "https://images.igdb.com/new-game.jpg",
    }]

    class Bot:
        async def send_photo(self, **kwargs):
            sent.append(("photo", kwargs))

        async def send_message(self, **kwargs):
            sent.append(("message", kwargs))

    monkeypatch.setattr(
        leisure_games, "get_game_premieres",
        lambda _cid, **_kwargs: asyncio.sleep(0, result=items),
    )
    monkeypatch.setattr(leisure_games.store, "get_settings", lambda _cid: {"city": "Алкмар"})

    asyncio.run(leisure_games.send_games_home(Bot(), "42"))

    assert [kind for kind, _kwargs in sent] == ["photo"]
    assert sent[0][1]["photo"] == "https://images.igdb.com/new-game.jpg"


def test_game_recommendation_keeps_genres_inside_card(monkeypatch):
    _profile_store(monkeypatch)
    monkeypatch.setattr(leisure_games.settings, "get", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        leisure_games.igdb, "enrich_game_recommendation", lambda item: item,
    )

    class Status:
        call = None

        async def replace(self, text, **kwargs):
            self.call = (text, kwargs)

    status = Status()
    asyncio.run(leisure_games.send_game_recommendation(object(), "42", status=status))

    assert "👾 Игра для тебя" in status.call[0]
    assert _labels(status.call[1]["reply_markup"]) == [
        ["✨ Другая игра"],
        ["🎭 По жанру"],
        ["🎚️ Мой набор игр"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_board_recommendation_stays_in_board_games_without_genre_button(monkeypatch):
    _profile_store(monkeypatch)
    monkeypatch.setattr(leisure_games.settings, "get", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        leisure_games.igdb, "enrich_game_recommendation", lambda item: item,
    )

    class Status:
        call = None

        async def replace(self, text, **kwargs):
            self.call = (text, kwargs)

    status = Status()
    asyncio.run(leisure_games.send_game_recommendation(
        object(), "42", status=status, refresh=True, genre="board",
    ))

    assert "🎲 Настолки" in status.call[0]
    assert _labels(status.call[1]["reply_markup"]) == [
        ["✨ Другая игра"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]
    assert status.call[1]["reply_markup"].inline_keyboard[0][0].callback_data == "vg_next_board"


def test_favorite_games_influence_recommendation_and_are_not_repeated(monkeypatch):
    _profile_store(monkeypatch)
    monkeypatch.setattr(leisure_games.settings, "get", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        leisure_games.store, "get_list",
        lambda key, _cid: [leisure_games.normalize_favorite_game("Hades")]
        if key == leisure_games.config.FAVORITE_GAMES_KEY else [],
    )

    item = leisure_games.pick_game("42", refresh=True)

    assert item["name"] == "It Takes Two"
    assert "action" in item["genres"]


def test_manual_board_game_label_is_detected_without_ai():
    item = leisure_games.normalize_favorite_game("настольная игра Каркассон")

    assert item == {
        "name": "Каркассон",
        "genres": ["board"],
        "platforms": ["board"],
    }


def test_game_set_groups_games_like_my_cinema(monkeypatch):
    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    monkeypatch.setattr(leisure_games.store, "ensure_list_ids", lambda *_args: [
        {**leisure_games.normalize_favorite_game("Hades"), "id": "hades"},
        {**leisure_games.normalize_favorite_game("Baldur’s Gate 3"), "id": "bg3"},
    ])

    bot = Bot()
    asyncio.run(leisure_games.send_game_set(bot, "42"))

    assert bot.message["text"] == (
        "🎚️ Мой набор игр · 2 игры\n\n"
        "RPG:\nBaldur’s Gate 3\n\n"
        "Экшен:\nHades"
    )
    assert _labels(bot.message["reply_markup"])[-2:] == [
        ["🆕 Добавить игру"], ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_game_set_uses_one_primary_genre_and_keeps_board_games_separate(monkeypatch):
    monkeypatch.setattr(leisure_games.store, "ensure_list_ids", lambda *_args: [
        {
            "id": "multi", "name": "Multi Genre", "genres": ["adventure", "action"],
            "platforms": ["pc"],
        },
        {
            "id": "board", "name": "Board Strategy", "genres": ["strategy", "cozy"],
            "platforms": ["board"],
        },
    ])

    _token, view = leisure_games._new_game_set_view("42")

    assert [(genre, [item["name"] for item in items]) for genre, items in view["genres"]] == [
        ("Приключения", ["Multi Genre"]),
        ("Настолки", ["Board Strategy"]),
    ]


def test_game_set_genre_switches_posters_in_the_same_card(monkeypatch):
    token = "game-carousel"
    leisure_games._game_set_views[token] = {
        "cid": "42", "created_at": leisure_games.time.time(),
        "genres": [("Экшен", [{
            **leisure_games.normalize_favorite_game("Hades"), "id": "hades-one",
            "poster": "one.jpg",
        }, {
            **leisure_games.normalize_favorite_game("Hades"), "id": "hades-two",
            "name": "Hades II", "poster": "two.jpg",
        }])],
    }
    monkeypatch.setattr(leisure_games, "enrich_favorite_game", lambda item: dict(item))
    edited = []

    class Query:
        async def edit_message_media(self, **kwargs):
            edited.append(kwargs)

    asyncio.run(leisure_games.send_game_set_genre(
        object(), "42", token, 0, 1, q=Query(),
    ))

    assert edited[0]["media"].media == "two.jpg"
    assert "Hades II" in edited[0]["media"].caption
    assert _labels(edited[0]["reply_markup"])[0] == ["◀️", "2/2", "▶️"]
    assert _labels(edited[0]["reply_markup"])[1] == ["❌ Удалить"]


def test_game_recommendation_with_poster_is_sent_as_photo(monkeypatch):
    sent = []

    class Bot:
        async def send_photo(self, **kwargs):
            sent.append(("photo", kwargs))

        async def send_message(self, **kwargs):
            sent.append(("message", kwargs))

    monkeypatch.setattr(leisure_games, "pick_game", lambda *_args, **_kwargs: {
        "name": "Example Game",
        "platforms": ["pc"],
        "platform_labels": ["💻 ПК"],
        "genre_label": "приключение",
        "description": "Короткое описание.",
    })
    monkeypatch.setattr(
        leisure_games.igdb,
        "enrich_game_recommendation",
        lambda item: {**item, "poster": "https://images.igdb.com/example.jpg"},
    )

    asyncio.run(leisure_games.send_game_recommendation(Bot(), "42"))

    assert [kind for kind, _kwargs in sent] == ["photo"]
    assert sent[0][1]["photo"] == "https://images.igdb.com/example.jpg"


def test_game_premieres_use_verified_source_url_and_platforms(monkeypatch):
    today = datetime.now(leisure_games.config.TZ).date()
    release = today + timedelta(days=30)
    memory = {}
    source_url = "https://example.com/releases"

    monkeypatch.setattr(
        leisure_games.settings, "get",
        lambda _cid, key, default=None: ["pc", "ps5"] if key == "game_platforms" else default,
    )
    monkeypatch.setattr(leisure_games.store, "_load", lambda _key: memory)

    def mutate(_key, callback):
        data, result = callback(memory)
        memory.clear()
        memory.update(data)
        return result

    monkeypatch.setattr(leisure_games.store, "mutate_kv", mutate)
    monkeypatch.setattr(leisure_games.research, "web_search", lambda *_args, **_kwargs: [{
        "url": source_url,
        "title": "Release calendar",
        "content": f"Example Game releases on {release.isoformat()} for PC and PS5.",
    }])

    async def llm(*_args, **_kwargs):
        return {"items": [{
            "title": "Example Game",
            "date": release.isoformat(),
            "platforms": ["pc", "ps5"],
            "genre": "приключение",
            "summary": "Герой исследует неизвестную планету",
            "url": source_url,
        }]}

    monkeypatch.setattr(leisure_games.ai, "allm_json", llm)
    monkeypatch.setattr(leisure_games.igdb, "enrich_game_premieres", lambda items: items)

    items = asyncio.run(leisure_games.get_game_premieres("42", refresh=True))

    assert items == [{
        "title": "Example Game",
        "date": release.isoformat(),
        "date_label": leisure_games._premiere_date_label(release.isoformat()),
        "platforms": ["pc", "ps5"],
        "platform_label": "💻 ПК · 🎮 PS5",
        "genre": "приключение",
        "summary": "Герой исследует неизвестную планету.",
        "url": source_url,
    }]


def test_game_premieres_fall_back_to_igdb_when_web_search_is_empty(monkeypatch):
    today = datetime.now(leisure_games.config.TZ).date()
    release = today + timedelta(days=21)
    memory = {}
    expected = {
        "title": "Catalog Game",
        "date": release.isoformat(),
        "date_label": leisure_games._premiere_date_label(release.isoformat()),
        "platforms": ["pc"],
        "platform_label": "💻 ПК",
        "genre": "приключение",
        "summary": "Герой исследует новый мир.",
        "url": "https://www.igdb.com/games/catalog-game",
        "poster": "https://images.igdb.com/catalog-game.jpg",
    }

    monkeypatch.setattr(
        leisure_games.settings, "get",
        lambda _cid, key, default=None: ["pc"] if key == "game_platforms" else default,
    )
    monkeypatch.setattr(leisure_games.store, "_load", lambda _key: memory)
    monkeypatch.setattr(leisure_games.research, "web_search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        leisure_games.igdb,
        "get_upcoming_games",
        lambda platforms, **_kwargs: [expected] if platforms == {"pc"} else [],
        raising=False,
    )

    def mutate(_key, callback):
        data, result = callback(memory)
        memory.clear()
        memory.update(data)
        return result

    monkeypatch.setattr(leisure_games.store, "mutate_kv", mutate)

    items = asyncio.run(leisure_games.get_game_premieres("42", refresh=True))

    assert items == [expected]


def test_game_premiere_title_uses_youtube_trailer():
    message = leisure_games.leisure_ui.game_premieres_screen([{
        "title": "Example Game",
        "date_label": "15 сентября 2026",
        "platform_label": "💻 ПК",
        "genre": "приключение",
        "summary": "Герой исследует неизвестную планету.",
        "url": "https://example.com/releases",
        "trailer_url": "https://www.youtube.com/watch?v=official-trailer",
    }])

    links = [entity.url for entity in message.entities if entity.type == "text_link"]

    assert links == ["https://www.youtube.com/watch?v=official-trailer"]


def test_game_premieres_are_sent_as_native_poster_gallery(monkeypatch):
    sent = []

    class Bot:
        async def send_media_group(self, **kwargs):
            sent.append(("gallery", kwargs))

        async def send_photo(self, **kwargs):
            sent.append(("photo", kwargs))

        async def send_message(self, **kwargs):
            sent.append(("message", kwargs))

    items = [{
        "title": f"Игра {index}",
        "date_label": "15 сентября 2026",
        "platform_label": "💻 ПК",
        "genre": "RPG",
        "summary": f"Короткое описание {index}.",
        "url": f"https://example.com/game/{index}",
        "poster": f"https://images.igdb.com/cover{index}.jpg",
        "trailer_url": f"https://www.youtube.com/watch?v=trailer{index}",
    } for index in range(3)]

    monkeypatch.setattr(
        leisure_games, "get_game_premieres", lambda _cid, **_kwargs: asyncio.sleep(0, result=items),
    )

    asyncio.run(leisure_games.send_game_premieres(Bot(), "42"))

    assert [kind for kind, _kwargs in sent] == ["gallery"]
    gallery = sent[0][1]
    assert len(gallery["media"]) == 3
    assert gallery["caption"].startswith("🎮 Премьеры игр")
    assert {
        entity.url for entity in gallery["caption_entities"] if entity.type == "text_link"
    } == {item["trailer_url"] for item in items}
