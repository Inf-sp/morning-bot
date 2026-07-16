import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import google_books
import leisure_books
import secure


class FakeResponse:
    status_code = 200
    headers = {}

    def json(self):
        return {
            "items": [
                {
                    "id": "wrong",
                    "volumeInfo": {
                        "title": "Flowers for Someone Else",
                        "authors": ["Another Author"],
                    },
                },
                {
                    "id": "algernon",
                    "volumeInfo": {
                        "title": "Flowers for Algernon",
                        "authors": ["Daniel Keyes"],
                        "publishedDate": "1959-04",
                        "imageLinks": {
                            "thumbnail": "http://books.google.com/cover.jpg",
                        },
                        "previewLink": "https://books.google.com/preview",
                        "industryIdentifiers": [
                            {"type": "ISBN_13", "identifier": "9780000000001"},
                        ],
                    },
                },
            ],
        }


def test_find_volume_uses_key_and_picks_matching_book(monkeypatch):
    captured = {}
    usage = []
    monkeypatch.setattr(google_books.config, "GOOGLE_BOOKS_API_KEY", "books-secret")
    monkeypatch.setattr(google_books.util, "ttl_get", lambda *_args: None)
    monkeypatch.setattr(google_books.util, "ttl_set", lambda *_args: None)
    monkeypatch.setattr(
        google_books.api_usage, "record_request",
        lambda service, **kwargs: usage.append((service, kwargs)),
    )

    def fake_get(url, params, timeout):
        captured.update({"url": url, "params": params, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(google_books.requests, "get", fake_get)

    result = google_books.find_volume(
        "Цветы для Элджернона", "Flowers for Algernon", "Daniel Keyes",
    )

    assert result["google_books_id"] == "algernon"
    assert result["author"] == "Daniel Keyes"
    assert result["year"] == "1959"
    assert result["cover_url"] == "https://books.google.com/cover.jpg"
    assert result["isbn"] == "9780000000001"
    assert captured["url"] == "https://www.googleapis.com/books/v1/volumes"
    assert captured["params"]["key"] == "books-secret"
    assert captured["params"]["printType"] == "books"
    assert captured["params"]["maxResults"] == 8
    assert captured["timeout"] == 10
    assert usage[-1][0] == "google_books"
    assert usage[-1][1]["ok"] is True


def test_enrich_book_keeps_editorial_metadata_and_adds_google_fields(monkeypatch):
    monkeypatch.setattr(google_books, "find_volume", lambda *_args: {
        "google_books_id": "book-id",
        "title": "The Original Title",
        "author": "Verified Author",
        "year": "2001",
        "cover_url": "https://books.google.com/cover.jpg",
        "preview_link": "https://books.google.com/preview",
    })
    original = {
        "title": "Локальное название",
        "author": "Редакторский автор",
        "year": "1999",
        "desc": "Редакторское описание",
    }

    result = google_books.enrich_book(original)

    assert result["author"] == "Редакторский автор"
    assert result["year"] == "1999"
    assert result["desc"] == "Редакторское описание"
    assert result["cover_url"] == "https://books.google.com/cover.jpg"
    assert result["google_books_verified"] is True
    assert "cover_url" not in original


def test_no_google_books_key_skips_network(monkeypatch):
    monkeypatch.setattr(google_books.config, "GOOGLE_BOOKS_API_KEY", "")
    monkeypatch.setattr(
        google_books.requests, "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network called")),
    )

    assert google_books.find_volume("1984", author="George Orwell") is None


def test_enrichment_failure_returns_original_card(monkeypatch):
    monkeypatch.setattr(
        google_books, "find_volume",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("malformed response")),
    )
    original = {"title": "1984", "author": "George Orwell"}

    assert google_books.enrich_book(original) == original


def test_book_card_prefers_google_books_cover(monkeypatch):
    sent = []

    class FakeBot:
        async def send_photo(self, **kwargs):
            sent.append(("photo", kwargs))

        async def send_message(self, **kwargs):
            sent.append(("message", kwargs))

    monkeypatch.setattr(leisure_books.google_books, "enrich_book", lambda item: {
        **item,
        "cover_url": "https://books.google.com/verified-cover.jpg",
    })
    monkeypatch.setattr(
        leisure_books, "_book_cover",
        lambda *_args: (_ for _ in ()).throw(AssertionError("Open Library called")),
    )

    asyncio.run(leisure_books._send_book_card(
        FakeBot(), "42", {"title": "1984", "author": "George Orwell"}, 0,
    ))

    assert sent[0][0] == "photo"
    assert sent[0][1]["photo"] == "https://books.google.com/verified-cover.jpg"


def test_google_books_key_is_redacted(monkeypatch):
    monkeypatch.setattr(
        google_books.config, "GOOGLE_BOOKS_API_KEY", "google-books-secret-key-123",
    )

    assert "google-books-secret-key-123" not in secure.redact(
        "key=google-books-secret-key-123",
    )
