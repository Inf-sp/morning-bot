import os
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import igdb


def test_upcoming_games_returns_dated_catalog_releases(monkeypatch):
    today = date(2026, 8, 20)
    release = today + timedelta(days=20)

    class Response:
        status_code = 200
        headers = {}

        def json(self):
            return [{
                "name": "Catalog Game",
                "slug": "catalog-game",
                "first_release_date": int(datetime.combine(
                    release, datetime.min.time(), tzinfo=timezone.utc,
                ).timestamp()),
                "cover": {"image_id": "cover123"},
                "platforms": [{"id": 6}, {"id": 167}],
                "genres": [{"name": "Adventure"}],
                "videos": [{"name": "Official Trailer", "video_id": "trailer123"}],
            }]

    monkeypatch.setattr(igdb, "_access_token", lambda: "token")
    monkeypatch.setattr(igdb.requests, "post", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(igdb, "_record", lambda *_args, **_kwargs: None)

    result = igdb.get_upcoming_games({"pc"}, today=today)

    assert result == [{
        "title": "Catalog Game",
        "date": release.isoformat(),
        "platforms": ["pc", "ps5"],
        "platform_label": "💻 ПК · 🎮 PS5",
        "genre": "приключение",
        "summary": "",
        "url": "https://www.igdb.com/games/catalog-game",
        "poster": "https://images.igdb.com/igdb/image/upload/t_cover_big/cover123.jpg",
        "trailer_url": "https://www.youtube.com/watch?v=trailer123",
    }]


def test_enriches_digital_game_with_cover_and_verified_trailer(monkeypatch):
    igdb.util._TTL_CACHE.clear()
    monkeypatch.setattr(igdb.config, "IGDB_CLIENT_ID", "client-id")
    monkeypatch.setattr(igdb.config, "IGDB_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(igdb, "_access_token", lambda: "token")
    monkeypatch.setattr(igdb, "_multiquery", lambda titles, token: [{
        "name": "game_0",
        "result": [{
            "name": "Example Game",
            "cover": {"image_id": "co123"},
            "platforms": [{"id": 167}],
            "genres": [{"name": "Adventure"}],
            "first_release_date": 1704067200,
            "videos": [
                {"name": "Gameplay", "video_id": "gameplay-id"},
                {"name": "Official Trailer", "video_id": "trailer-id"},
            ],
        }],
    }])

    result = igdb.enrich_game_premieres([{
        "title": "Example Game",
        "platforms": ["pc", "ps5"],
        "url": "https://example.com/release",
    }])

    assert result[0]["poster"] == (
        "https://images.igdb.com/igdb/image/upload/t_cover_big/co123.jpg"
    )
    assert result[0]["trailer_url"] == "https://www.youtube.com/watch?v=trailer-id"
    assert result[0]["platforms"] == ["ps5"]
    assert result[0]["platform_label"] == "🎮 PS5"
    assert result[0]["genres"] == ["adventure"]
    assert result[0]["year"] == 2024


def test_does_not_query_igdb_for_board_games(monkeypatch):
    igdb.util._TTL_CACHE.clear()
    monkeypatch.setattr(igdb.config, "IGDB_CLIENT_ID", "client-id")
    monkeypatch.setattr(igdb.config, "IGDB_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(
        igdb, "_access_token", lambda: (_ for _ in ()).throw(AssertionError("unexpected request")),
    )

    item = {"title": "Настольная игра", "platforms": ["board"], "url": "https://example.com"}

    assert igdb.enrich_game_premieres([item]) == [item]


def test_rejects_uncertain_title_match(monkeypatch):
    igdb.util._TTL_CACHE.clear()
    monkeypatch.setattr(igdb.config, "IGDB_CLIENT_ID", "client-id")
    monkeypatch.setattr(igdb.config, "IGDB_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(igdb, "_access_token", lambda: "token")
    monkeypatch.setattr(igdb, "_multiquery", lambda *_args: [{
        "name": "game_0",
        "result": [{
            "name": "Completely Different Game",
            "cover": {"image_id": "wrong"},
            "videos": [{"name": "Trailer", "video_id": "wrong"}],
        }],
    }])
    item = {"title": "Example Game", "platforms": ["pc"], "url": "https://example.com"}

    assert igdb.enrich_game_premieres([item]) == [item]
