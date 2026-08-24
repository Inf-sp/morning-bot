from datetime import date

import open_library


class _Response:
    status_code = 200
    headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return {"docs": [{
            "key": "/works/OL1W", "title": "Fresh Book", "author_name": ["Author"],
            "first_publish_year": 2026, "publish_date": ["July 15, 2026"],
            "isbn": ["9780000000001"], "cover_i": 123,
            "publisher": ["Publisher"], "ratings_average": 4.4, "ratings_count": 120,
        }]}


def test_recent_releases_require_exact_recent_first_publication(monkeypatch):
    monkeypatch.setattr(open_library.util, "ttl_get", lambda *_args: None)
    monkeypatch.setattr(open_library.util, "ttl_set", lambda *_args: None)
    monkeypatch.setattr(open_library.requests, "get", lambda *_args, **_kwargs: _Response())

    items = open_library.search_recent_releases(date(2026, 8, 23), 10)

    assert len(items) == 3  # одна подтверждённая запись из каждого тематического запроса
    assert items[0]["published_date"] == "2026-07-15"
    assert items[0]["cover_url"] == "https://covers.openlibrary.org/b/id/123-L.jpg"


def test_cover_lookup_rejects_placeholder(monkeypatch):
    response = _Response()
    response.status_code = 404
    monkeypatch.setattr(open_library.requests, "get", lambda *_args, **_kwargs: response)

    assert open_library.cover_for_isbn("978-0-00-000000-1") == ""


def test_search_books_finds_flowers_for_algernon_with_cover(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"docs": [{
                "key": "/works/OL24364W",
                "title": "Цветы для Элджернона",
                "author_name": ["Дэниел Киз"],
                "first_publish_year": 1959,
                "cover_i": 123,
                "ratings_count": 500,
                "subject": ["Science fiction"],
            }]}

    monkeypatch.setattr(open_library.util, "ttl_get", lambda *_args: None)
    monkeypatch.setattr(open_library.util, "ttl_set", lambda *_args: None)
    monkeypatch.setattr(open_library.requests, "get", lambda *_args, **_kwargs: Response())

    result = open_library.search_books(
        "Цветы для Элджернона", alternative_title="Flowers for Algernon",
    )

    assert result[0]["title"] == "Цветы для Элджернона"
    assert result[0]["author"] == "Дэниел Киз"
    assert result[0]["year"] == "1959"
    assert result[0]["cover_url"] == "https://covers.openlibrary.org/b/id/123-L.jpg"
