import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import leisure_collection
import tmdb
from ui import leisure as leisure_ui


def _search(monkeypatch, results_by_query):
    monkeypatch.setattr(tmdb.config, "TMDB_API_KEY", "key")
    monkeypatch.setattr(tmdb.util, "ttl_get", lambda *_a: None)
    monkeypatch.setattr(tmdb.util, "ttl_set", lambda _ns, _key, value: value)
    monkeypatch.setattr(tmdb, "english_poster", lambda *_a: None)
    monkeypatch.setattr(tmdb, "_get", lambda path, params=None, **_k: (
        {"results": results_by_query.get((params or {}).get("query"), [])} if path == "/search/multi" else {}))


SCHITTS = {"id": 61662, "media_type": "tv", "name": "Шиттс Крик", "original_name": "Schitt's Creek",
           "first_air_date": "2015-01-13", "overview": "Семья теряет всё.", "popularity": 40,
           "poster_path": "/schitt.jpg"}
OTHER = {"id": 1, "media_type": "movie", "title": "Крик", "original_title": "Scream",
         "release_date": "1996-12-20", "overview": "Хоррор.", "popularity": 300}


def test_best_title_match_wins_over_popularity_and_poster_falls_back(monkeypatch):
    _search(monkeypatch, {"Шиттс крик": [OTHER, SCHITTS]})

    found = tmdb.lookup_title("Шиттс крик", strict=True)

    assert found["name"] == "Шиттс Крик" and found["kind"] == "tv"
    assert found["poster"].endswith("/schitt.jpg")


def test_strict_lookup_refuses_a_different_title(monkeypatch):
    _search(monkeypatch, {"Шитс крик": [OTHER]})

    assert tmdb.lookup_title("Шитс крик", strict=True) is None


def test_adding_uses_ai_original_title_when_written_title_does_not_match(monkeypatch):
    _search(monkeypatch, {"Шыттс крык": [OTHER], "Schitt's Creek": [SCHITTS]})
    monkeypatch.setattr(leisure_collection.ai, "llm_json",
                        lambda *_a, **_k: {"title_en": "Schitt's Creek", "year": "2015"})

    found = leisure_collection._resolve_movie_label("Шыттс крык", allow_ai=True)

    assert found["name"] == "Шиттс Крик"
    assert leisure_collection._resolve_movie_label("Шыттс крык") is None


def test_favorite_movie_list_shows_every_title_numbered():
    text = leisure_ui.favorite_movie_list("Драма", ["Патерсон (фильм, 2016)", "Медведь (сериал, 2022)"]).text

    assert text == "🎬 Драма · 2\n\n1. Патерсон (фильм, 2016)\n2. Медведь (сериал, 2022)"


def test_movie_add_query_keeps_kind_year_and_both_titles():
    import personal_collections

    assert personal_collections._movie_query_candidates("Взрослые Adults сериал 2025") == [
        {"value": "Adults (сериал, 2025)", "label": "Adults"},
        {"value": "Взрослые (сериал, 2025)", "label": "Взрослые"},
    ]
    assert personal_collections._movie_query_candidates("Adults (сериал, 2025)") == [
        {"value": "Adults (сериал, 2025)", "label": "Adults"},
    ]


def test_lookup_title_filters_kind_and_prefers_requested_year(monkeypatch):
    import tmdb

    results = [
        {"id": 1, "media_type": "movie", "title": "Adults", "release_date": "2025-01-01", "popularity": 90},
        {"id": 2, "media_type": "tv", "name": "Adults", "first_air_date": "2008-01-01", "popularity": 50},
        {"id": 3, "media_type": "tv", "name": "Adults", "first_air_date": "2025-05-28", "popularity": 10},
    ]
    monkeypatch.setattr(tmdb.config, "TMDB_API_KEY", "key")
    monkeypatch.setattr(tmdb, "_get", lambda path, *_a, **_k: {"results": results} if "search" in path else {"overview": "x"})
    monkeypatch.setattr(tmdb, "english_poster", lambda *_a, **_k: None)
    monkeypatch.setattr(tmdb.util, "ttl_get", lambda *_a, **_k: None)
    monkeypatch.setattr(tmdb.util, "ttl_set", lambda _ns, _key, value: value)

    found = tmdb.lookup_title("Adults", strict=True, kind="tv", year="2025")

    assert found["id"] == 3
