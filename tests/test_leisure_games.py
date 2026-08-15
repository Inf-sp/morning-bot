import asyncio
import os
from datetime import datetime, timedelta

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
        leisure_games.store, "set_profile",
        lambda cid, value: profiles.__setitem__(str(cid), dict(value)),
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

    assert "🎬 Игры на сегодня · Alkmaar" in status.call[0]
    assert "Какие новинки:" in status.call[0]
    assert "Ребус дня:" in status.call[0]
    assert "💡 Интересно:" in status.call[0]
    assert _labels(status.call[1]["reply_markup"]) == [
        ["✨ Подобрать новую игру"],
        ["🎮 Премьеры игр"],
        ["🎲 Настолки"],
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
    })

    assert message.text.count("• Игра ") == 3
    assert "• Игра 0 · RPG · 💻 ПК · 🎮 PS5" in message.text
    assert "Игра 3" not in message.text
    assert {
        entity.url for entity in message.entities if entity.type == "text_link"
    } == {f"https://www.youtube.com/watch?v=game{index}" for index in range(3)}
    assert any(entity.type == "spoiler" for entity in message.entities)


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
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


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
