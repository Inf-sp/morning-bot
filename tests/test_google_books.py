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
                        "averageRating": 4.2,
                        "ratingsCount": 318,
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
        google_books.api_usage, "google_books_requests",
        lambda *, consume=False: usage.append(consume) or {
            "used": int(consume), "remaining": 1000 - int(consume), "allowed": True,
        },
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
    assert result["rating"] == 4.2
    assert result["ratings_count"] == 318
    assert result["cover_url"] == "https://books.google.com/cover.jpg"
    assert result["isbn"] == "9780000000001"
    assert captured["url"] == "https://www.googleapis.com/books/v1/volumes"
    assert captured["params"]["key"] == "books-secret"
    assert captured["params"]["printType"] == "books"
    assert captured["params"]["maxResults"] == 8
    assert captured["timeout"] == 10
    assert usage == [False, True]


def test_google_volume_description_is_plain_text():
    volume = google_books._volume({
        "id": "book",
        "volumeInfo": {
            "title": "Марсианин",
            "description": "<p>История <b>выживания</b><br>на Марсе &amp; Земле.</p>",
        },
    })

    assert volume["description"] == "История выживания на Марсе & Земле."


def test_find_volumes_ranks_exact_title_year_and_popularity(monkeypatch):
    monkeypatch.setattr(google_books.config, "GOOGLE_BOOKS_API_KEY", "books-secret")
    monkeypatch.setattr(google_books, "_search_items", lambda *_args, **_kwargs: [
        {"id": "subtitle", "volumeInfo": {
            "title": "Марсианин: роман", "authors": ["Энди Вейер"],
            "publishedDate": "2011", "ratingsCount": 999_999,
            "imageLinks": {"thumbnail": "https://example.com/subtitle.jpg"},
        }},
        {"id": "wrong-year", "volumeInfo": {
            "title": "Марсианин", "authors": ["Энди Вейер"],
            "publishedDate": "2014", "ratingsCount": 50_000,
            "imageLinks": {"thumbnail": "https://example.com/wrong-year.jpg"},
        }},
        {"id": "other-author", "volumeInfo": {
            "title": "Марсианин", "authors": ["Другой Автор"],
            "publishedDate": "2011", "ratingsCount": 120,
            "imageLinks": {"thumbnail": "https://example.com/other.jpg"},
        }},
        {"id": "popular", "volumeInfo": {
            "title": "Марсианин", "authors": ["Энди Вейер"],
            "publishedDate": "2011", "averageRating": 4.7,
            "ratingsCount": 800,
            "imageLinks": {"thumbnail": "https://example.com/popular.jpg"},
        }},
    ])

    result = google_books.find_volumes("Марсианин", year="2011")

    assert [book["google_books_id"] for book in result] == [
        "popular", "other-author", "wrong-year", "subtitle",
    ]
    assert all(book["google_books_verified"] is True for book in result)


def test_find_volumes_prefers_newest_only_after_popularity(monkeypatch):
    monkeypatch.setattr(google_books.config, "GOOGLE_BOOKS_API_KEY", "books-secret")
    monkeypatch.setattr(google_books, "_search_items", lambda *_args, **_kwargs: [
        {"id": "less-popular-new", "volumeInfo": {
            "title": "Остров", "authors": ["Автор Б"],
            "publishedDate": "2025", "averageRating": 4.8, "ratingsCount": 20,
            "imageLinks": {"thumbnail": "https://example.com/b.jpg"},
        }},
        {"id": "popular-old", "volumeInfo": {
            "title": "Остров", "authors": ["Автор А"],
            "publishedDate": "2011", "averageRating": 4.5, "ratingsCount": 500,
            "imageLinks": {"thumbnail": "https://example.com/a.jpg"},
        }},
        {"id": "popular-new", "volumeInfo": {
            "title": "Остров", "authors": ["Автор В"],
            "publishedDate": "2024", "averageRating": 4.5, "ratingsCount": 500,
            "imageLinks": {"thumbnail": "https://example.com/c.jpg"},
        }},
    ])

    result = google_books.find_volumes("Остров")

    assert [book["google_books_id"] for book in result] == [
        "popular-new", "popular-old", "less-popular-new",
    ]


def test_find_volumes_prioritizes_popular_adult_book_over_childrens_match(monkeypatch):
    monkeypatch.setattr(google_books.config, "GOOGLE_BOOKS_API_KEY", "books-secret")
    monkeypatch.setattr(google_books, "_search_items", lambda *_args, **_kwargs: [
        {"id": "childrens", "volumeInfo": {
            "title": "Остров", "authors": ["Малоизвестный автор"],
            "publishedDate": "2020", "ratingsCount": 50_000,
            "categories": ["Juvenile Fiction"],
            "imageLinks": {"thumbnail": "https://example.com/child.jpg"},
        }},
        {"id": "popular-adult", "volumeInfo": {
            "title": "Остров", "authors": ["Популярный автор"],
            "publishedDate": "2015", "ratingsCount": 5_000,
            "categories": ["Fiction"],
            "imageLinks": {"thumbnail": "https://example.com/adult.jpg"},
        }},
    ])

    result = google_books.find_volumes("Остров")

    assert [book["google_books_id"] for book in result] == ["popular-adult"]


def test_find_volumes_for_manual_addition_keeps_only_english_editions(monkeypatch):
    monkeypatch.setattr(google_books.config, "GOOGLE_BOOKS_API_KEY", "books-secret")
    monkeypatch.setattr(google_books, "_search_items", lambda *_args, **_kwargs: [
        {"id": "russian", "volumeInfo": {
            "title": "Марсианин", "authors": ["Энди Вейер"], "language": "ru",
            "publishedDate": "2014", "ratingsCount": 50_000,
            "imageLinks": {"thumbnail": "https://example.com/russian-cover.jpg"},
        }},
        {"id": "english", "volumeInfo": {
            "title": "The Martian", "authors": ["Andy Weir"], "language": "en",
            "publishedDate": "2014", "ratingsCount": 5_000,
            "imageLinks": {"thumbnail": "https://example.com/english-cover.jpg"},
        }},
    ])

    result = google_books.find_volumes(
        "Марсианин", alternative_title="The Martian", english_only=True,
    )

    assert [book["google_books_id"] for book in result] == ["english"]
    assert result[0]["cover_url"] == "https://example.com/english-cover.jpg"


def test_find_volumes_uses_alternative_title_and_filters_wrong_author(monkeypatch):
    calls = []
    monkeypatch.setattr(google_books.config, "GOOGLE_BOOKS_API_KEY", "books-secret")

    def search(query, max_results):
        calls.append((query, max_results))
        if query.startswith("Марсианин"):
            return []
        return [
            {"id": "correct", "volumeInfo": {
                "title": "The Martian", "authors": ["Andy Weir"],
                "publishedDate": "2011",
                "imageLinks": {"thumbnail": "https://example.com/correct.jpg"},
            }},
            {"id": "wrong-author", "volumeInfo": {
                "title": "The Martian", "authors": ["Someone Else"],
                "publishedDate": "2011",
                "imageLinks": {"thumbnail": "https://example.com/wrong.jpg"},
            }},
        ]

    monkeypatch.setattr(google_books, "_search_items", search)

    result = google_books.find_volumes(
        "Марсианин", alternative_title="The Martian",
        author="Andy Weir", year="2011", max_results=5,
    )

    assert calls == [
        ("Марсианин Andy Weir 2011", 5),
        ("The Martian Andy Weir 2011", 5),
    ]
    assert [book["google_books_id"] for book in result] == ["correct"]


def test_find_volumes_does_not_return_a_different_explicit_author(monkeypatch):
    monkeypatch.setattr(google_books.config, "GOOGLE_BOOKS_API_KEY", "books-secret")
    monkeypatch.setattr(google_books, "_search_items", lambda *_args, **_kwargs: [
        {"id": "wrong-author", "volumeInfo": {
            "title": "Марсианин", "authors": ["Другой Автор"],
            "publishedDate": "2011",
            "imageLinks": {"thumbnail": "https://example.com/wrong.jpg"},
        }},
    ])

    assert google_books.find_volumes("Марсианин", author="Энди Вейер") == []


def test_find_volumes_deduplicates_same_edition_and_prefers_cover(monkeypatch):
    monkeypatch.setattr(google_books.config, "GOOGLE_BOOKS_API_KEY", "books-secret")
    monkeypatch.setattr(google_books, "_search_items", lambda *_args, **_kwargs: [
        {"id": "without-cover", "volumeInfo": {
            "title": "Солярис", "authors": ["Станислав Лем"],
            "publishedDate": "1961", "ratingsCount": 10_000,
        }},
        {"id": "with-cover", "volumeInfo": {
            "title": "Солярис", "authors": ["Станислав Лем"],
            "publishedDate": "1961", "ratingsCount": 10,
            "imageLinks": {"thumbnail": "http://example.com/solaris.jpg"},
        }},
    ])

    result = google_books.find_volumes("Солярис")

    assert [book["google_books_id"] for book in result] == ["with-cover"]
    assert result[0]["cover_url"] == "https://example.com/solaris.jpg"


def test_enrich_book_keeps_editorial_metadata_and_adds_google_fields(monkeypatch):
    monkeypatch.setattr(google_books, "find_volume", lambda *_args, **_kwargs: {
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
    assert google_books.find_volumes("1984", author="George Orwell") == []


def test_new_releases_request_uses_newest_order_and_deduplicates(monkeypatch):
    calls = []

    def search(query, max_results, *, order_by):
        calls.append((query, max_results, order_by))
        return [{"id": query, "volumeInfo": {
            "title": "One Book", "authors": ["Author"], "language": "en",
        }}]

    monkeypatch.setattr(google_books, "_search_items", search)

    result = google_books.search_new_releases(12)

    assert calls == [
        ("subject:Fiction", 12, "newest"),
        ("subject:Biography", 12, "newest"),
        ("subject:History", 12, "newest"),
    ]
    assert len(result) == 1


def test_enrichment_failure_returns_original_card(monkeypatch):
    monkeypatch.setattr(
        google_books, "find_volume",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("malformed response")),
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


def test_youtube_key_is_redacted(monkeypatch):
    monkeypatch.setattr(google_books.config, "YOUTUBE_API_KEY", "youtube-secret-key-123")

    assert "youtube-secret-key-123" not in secure.redact(
        "key=youtube-secret-key-123",
    )
