import asyncio
import os
from datetime import date, timedelta

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import apple_music
import leisure_games
import leisure_novelty
import recommendation_stoplist
from ui import leisure as leisure_ui


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def send_photo(self, **kwargs):
        self.sent.append({**kwargs, "text": kwargs["caption"]})


def _movies(n=3):
    today = date.today()
    return [{"id": i, "title": f"Фильм {i}", "date": (today - timedelta(days=i)).isoformat(),
             "genres": "драма", "overview": "Первое предложение. Второе."} for i in range(n)]


def _send(monkeypatch, kind, items, *, dislike=False):
    async def fetch(_cid, _kind):
        return [dict(item) for item in items]

    async def no_image(_kind, _item):
        return ""

    monkeypatch.setattr(leisure_novelty, "_items", fetch)
    monkeypatch.setattr(leisure_novelty, "_image", no_image)
    bot = Bot()
    action = leisure_novelty.dislike_novelty if dislike else leisure_novelty.send_novelty
    asyncio.run(action(bot, CID, kind))
    return bot.sent[-1]


CID = "novelty-test"


def test_movie_novelty_card_says_now_in_cinema_and_has_actions(monkeypatch):
    sent = _send(monkeypatch, "movie", _movies(1))

    assert sent["text"].startswith("🎬 Сейчас в кино\n\n«Фильм 0»\nдрама\n\nПервое предложение.")
    rows = sent["reply_markup"].inline_keyboard
    assert [(row[0].text, row[0].callback_data) for row in rows[:2]] == [
        ("✨ Другой фильм", "nov_pick_movie"), ("Не нравится", "nov_no_movie"),
    ]
    assert rows[1][0].api_kwargs == {"style": "danger"}
    picker = leisure_novelty.genre_picker(CID, "movie", back="nov_card_movie").inline_keyboard
    assert [row[0].text for row in picker[:2]] == ["Любой жанр", "🆕 Новинка"]
    assert picker[-1][0].callback_data == "nov_card_movie"


def test_upcoming_movie_and_game_show_date_like_concerts():
    later = date.today() + timedelta(days=10)
    label = leisure_ui._event_date_label(later.isoformat())

    assert leisure_ui._novelty_status("movie", {"date": later.isoformat()}) == f"🎬 Скоро в кино · {label}"
    assert leisure_ui._novelty_status("game", {"date": later.isoformat()}) == f"👾 Выходит {label}"
    assert leisure_ui._novelty_status("book", {"published_date": "2026-09-01"}).startswith("📚 Новая книга · 1 сентября")


def test_other_novelty_does_not_repeat_and_dislike_hides_it(monkeypatch):
    items = _movies(3)
    titles = [_send(monkeypatch, "movie", items)["text"].split("«")[1].split("»")[0] for _ in range(3)]
    assert len(set(titles)) == 3

    hidden = _send(monkeypatch, "movie", items, dislike=True)
    assert titles[-1] in recommendation_stoplist.values(CID, "movie")
    for _ in range(4):
        assert f"«{titles[-1]}»" not in _send(monkeypatch, "movie", items)["text"]
    assert hidden["text"]


def test_music_novelty_is_a_real_album_with_artist(monkeypatch):
    sent = _send(monkeypatch, "music", [{"title": "Brat", "artist": "Charli XCX",
                                         "date": "2026-10-02", "url": "https://music.apple.com/album/1"}])

    assert sent["text"].startswith("🎧 Новый альбом · 2 октября")
    assert "«Brat» — Charli XCX" in sent["text"]


def test_apple_music_keeps_only_fresh_albums_of_the_genre(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"feed": {"results": [
                {"name": "Brat", "artistName": "Charli XCX", "releaseDate": "2026-10-02",
                 "genres": [{"genreId": "14"}], "artworkUrl100": "x/100x100bb.jpg", "url": "u"},
                {"name": "Old", "artistName": "A", "releaseDate": "2025-01-01", "genres": [{"genreId": "14"}]},
                {"name": "Rock", "artistName": "B", "releaseDate": "2026-10-01", "genres": [{"genreId": "21"}]},
            ]}}

    monkeypatch.setattr(apple_music.util, "ttl_get", lambda *_a: None)
    monkeypatch.setattr(apple_music.util, "ttl_set", lambda *_a: None)
    monkeypatch.setattr(apple_music.requests, "get", lambda *_a, **_k: Response())
    assert apple_music.new_releases("pop", "nl", today=date(2026, 10, 8)) == [{
        "title": "Brat", "artist": "Charli XCX", "date": "2026-10-02",
        "cover": "x/600x600bb.jpg", "url": "u", "genre": "pop",
    }]

    def boom(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(apple_music.requests, "get", boom)
    assert apple_music.new_releases("rock", "nl") == []
    assert apple_music.new_releases("unknown") == []


def test_empty_novelty_offers_genres(monkeypatch):
    sent = _send(monkeypatch, "book", [])

    assert sent["text"] == "Свежих книжных премьер пока нет — загляни позже."
    # Новинок нет — под сообщением сразу выбор жанра.
    assert sent["reply_markup"].inline_keyboard[0][0].callback_data == "book_next"


def test_disliked_game_is_not_recommended_again(monkeypatch):
    cid = "game-dislike-test"
    name = leisure_games._GAME_CATALOG[0]["name"]
    monkeypatch.setattr(leisure_games, "_effective_platforms", lambda _cid: ["pc", "ps5", "xbox", "switch", "mobile"])
    assert any(item["name"] == name for item in leisure_games._eligible_games(cid))

    recommendation_stoplist.add(cid, "game", name, "hidden")

    assert all(item["name"] != name for item in leisure_games._eligible_games(cid))
