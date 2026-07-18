from datetime import date, timedelta

import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import tmdb


def _movie(movie_id, title, release_date, popularity=10, vote_count=100):
    return tmdb.CinemaMovie(
        id=movie_id,
        title=title,
        original_title=title,
        overview=None,
        poster_url=None,
        release_date=release_date,
        genres=["драма"],
        rating=7.5,
        popularity=popularity,
        country_code="NL",
        is_theatrical=False,
        vote_count=vote_count,
    )


def test_get_calls_tmdb_and_returns_json(monkeypatch):
    seen = {}

    class Response:
        status_code = 200
        headers = {}

        def json(self):
            return {"results": [{"id": 7}]}

    def fake_get(url, *, params, timeout):
        seen.update(url=url, params=params, timeout=timeout)
        return Response()

    monkeypatch.setattr(tmdb.config, "TMDB_API_KEY", "test-key")
    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    monkeypatch.setattr(tmdb.api_usage, "record_request", lambda *_args, **_kwargs: None)

    result = tmdb._get("/search/multi", {"query": "Arrival"}, timeout=3)

    assert result == {"results": [{"id": 7}]}
    assert seen["url"].endswith("/search/multi")
    assert seen["params"]["api_key"] == "test-key"
    assert seen["params"]["query"] == "Arrival"
    assert seen["timeout"] == 3


def test_now_playing_requires_current_nl_theatrical_release(monkeypatch):
    today = date.today()
    candidates = [
        _movie(1, "Official NL title", today - timedelta(days=5), 50),
        _movie(2, "Digital only", today - timedelta(days=2), 100),
        _movie(3, "Future theatrical", today + timedelta(days=2), 90),
        _movie(4, "Old theatrical", today - timedelta(days=100), 80),
    ]
    releases = {
        1: today - timedelta(days=5),
        2: None,
        3: today + timedelta(days=2),
        4: today - timedelta(days=100),
    }
    monkeypatch.setattr(tmdb.config, "TMDB_API_KEY", "test")
    monkeypatch.setattr(tmdb, "_regional_movies", lambda *a, **k: candidates)
    monkeypatch.setattr(tmdb, "_regional_theatrical_release_date", lambda mid, cc: releases[mid])

    result = tmdb.get_now_playing("NL", "nl-NL", max_results=8)

    assert [movie.id for movie in result] == [1]
    assert result[0].is_theatrical is True


def test_release_dates_accept_only_nl_theatrical_types(monkeypatch):
    today = date.today().isoformat() + "T00:00:00.000Z"
    monkeypatch.setattr(tmdb.util, "ttl_get", lambda *a, **k: None)
    monkeypatch.setattr(tmdb.util, "ttl_set", lambda *a, **k: None)
    monkeypatch.setattr(tmdb, "_get", lambda *a, **k: {
        "results": [
            {"iso_3166_1": "US", "release_dates": [{"type": 3, "release_date": today}]},
            {"iso_3166_1": "NL", "release_dates": [
                {"type": 4, "release_date": today},
                {"type": 3, "release_date": today},
            ]},
        ]
    })

    assert tmdb._regional_theatrical_release_date(10, "NL") == date.today()
