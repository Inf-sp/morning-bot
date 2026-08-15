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


def test_missing_genre_match_offers_platform_settings(monkeypatch):
    _profile_store(monkeypatch)
    monkeypatch.setattr(
        leisure_games.settings, "get",
        lambda _cid, key, default=None: ["ps5"] if key == "game_platforms" else default,
    )

    item = leisure_games.pick_game("42", genre="board", refresh=True)
    labels = _labels(leisure_games._game_keyboard(no_match=not item))

    assert item == {}
    assert ["📝 Платформы"] in labels


def test_game_preferences_offer_pc_ps5_board_and_other(monkeypatch):
    monkeypatch.setattr(
        leisure_games.settings, "get",
        lambda _cid, key, default=None: ["pc", "ps5"] if key == "game_platforms" else default,
    )

    assert _labels(leisure_games._preferences_keyboard("42")) == [
        ["✅ 💻 ПК"],
        ["✅ 🎮 PS5"],
        ["□ 🎲 Настолки"],
        ["□ 🕹️ Прочее"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_game_home_has_other_game_premieres_and_genres(monkeypatch):
    _profile_store(monkeypatch)
    monkeypatch.setattr(leisure_games.settings, "get", lambda *_args, **_kwargs: [])

    class Status:
        call = None

        async def replace(self, text, **kwargs):
            self.call = (text, kwargs)

    status = Status()
    asyncio.run(leisure_games.send_games_home(object(), "42", status=status))

    assert "👾 Игра для тебя" in status.call[0]
    assert _labels(status.call[1]["reply_markup"]) == [
        ["✨ Другая игра"],
        ["🆕 Премьеры игр"],
        ["🎭 По жанру"],
        ["#️⃣ Главная"],
    ]


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
    assert gallery["caption"].startswith("🆕 Премьеры игр")
    assert {
        entity.url for entity in gallery["caption_entities"] if entity.type == "text_link"
    } == {item["trailer_url"] for item in items}
