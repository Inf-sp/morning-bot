import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import igdb


def test_enriches_digital_game_with_cover_and_verified_trailer(monkeypatch):
    monkeypatch.setattr(igdb.config, "IGDB_CLIENT_ID", "client-id")
    monkeypatch.setattr(igdb.config, "IGDB_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(igdb, "_access_token", lambda: "token")
    monkeypatch.setattr(igdb, "_multiquery", lambda titles, token: [{
        "name": "game_0",
        "result": [{
            "name": "Example Game",
            "cover": {"image_id": "co123"},
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


def test_does_not_query_igdb_for_board_games(monkeypatch):
    monkeypatch.setattr(igdb.config, "IGDB_CLIENT_ID", "client-id")
    monkeypatch.setattr(igdb.config, "IGDB_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(
        igdb, "_access_token", lambda: (_ for _ in ()).throw(AssertionError("unexpected request")),
    )

    item = {"title": "Настольная игра", "platforms": ["board"], "url": "https://example.com"}

    assert igdb.enrich_game_premieres([item]) == [item]


def test_rejects_uncertain_title_match(monkeypatch):
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
