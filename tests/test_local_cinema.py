import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import local_cinema


def test_local_cinema_parser_keeps_only_movie_headings():
    page = """
    <h2>Bioscoopagenda</h2>
    <h3><a href=\"/film/the-odyssey.html\">The Odyssey</a></h3>
    <h3><a href=\"/film/the-invite.html\">The Invite</a></h3>
    <h3><a href=\"/film/the-odyssey.html\">The Odyssey</a></h3>
    """

    assert [movie.title for movie in local_cinema._parse_titles(page)] == ["The Odyssey", "The Invite"]


def test_local_cinema_never_falls_back_to_another_city(monkeypatch):
    class Response:
        status_code = 404
        text = ""

    monkeypatch.setattr(local_cinema.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(local_cinema, "_cache_get", lambda *args, **kwargs: None)

    assert local_cinema.get_city_movies("42", "Alkmaar", refresh=True) == []
