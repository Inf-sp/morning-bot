
import open_library


class _Response:
    status_code = 200
    headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return {"docs": [{
            "key": "/works/OL1W", "title": "Fresh Book", "author_name": ["Author"],
            "first_publish_year": 2026, "publish_date": ["July 15, 2026"],
            "isbn": ["9780000000001"], "cover_i": 123,
            "language": ["eng"],
            "publisher": ["Publisher"], "ratings_average": 4.4, "ratings_count": 120,
        }]}


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
