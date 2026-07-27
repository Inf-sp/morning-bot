import os
from datetime import datetime
from telegram import MessageEntity

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_books
import leisure_home
import leisure_music
import leisure_movies
import config
from ui.menu import _SCREENS


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_leisure_home_contains_three_sections_in_one_column_and_home():
    rows = _SCREENS["m_leisure"][3]
    assert [label for row in rows for label, _ in row] == [
        "🎬 Кино", "🎧 Музыка", "📖 Книги", "💾 Сохранённое", "🎚️ Предпочтения", "#️⃣ Главная"
    ]
    assert [len(row) for row in rows] == [1, 1, 1, 2, 1]


def test_saved_leisure_categories_use_saved_marker():
    assert _labels(leisure_home._leisure_category_keyboard("leisure_saved", saved=True))[:3] == [
        ["💾 Кино"], ["💾 Музыка"], ["💾 Книги"],
    ]


def test_movie_home_uses_clear_recommendation_labels():
    labels = _labels(leisure_movies._movie_home_kb())
    assert labels == [
        ["✨ Другое кино"],
        ["🎭 По жанру"],
        ["❤️ Моё кино", "💾 Сохранить"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_book_and_music_home_follow_same_model():
    assert _labels(leisure_books.books_home_keyboard())[:4] == [
        ["✨ Подобрать книгу"], ["🎭 По жанру"], ["❤️ Мои книги", "💾 Сохранить"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]
    assert _labels(leisure_music.music_home_keyboard())[:5] == [
        ["✨ Подобрать музыку"], ["❤️ Мои артисты", "💾 Сохранить"],
        ["🎫 Концерты"], ["⬅️ Назад", "#️⃣ Главная"],
    ]
    assert leisure_books.books_home_keyboard().inline_keyboard[0][0].callback_data == "book_reco"
    assert leisure_music.music_home_keyboard().inline_keyboard[0][0].callback_data == "music_reco"


def test_recommendation_cards_use_content_specific_next_labels():
    assert _labels(leisure_movies._movie_kb(0))[0] == ["✨ Другое кино"]
    assert _labels(leisure_books._book_kb(0))[0] == ["✨ Другая книга"]
    assert _labels(leisure_music._listen_kb())[0] == ["✨ Другой артист"]
    assert _labels(leisure_music._listen_kb())[:3] == [
        ["✨ Другой артист"],
        ["🎫 Концерты"],
        ["❤️ Мои артисты", "💾 Сохранить"],
    ]
    assert _labels(leisure_books._book_kb(0, saved=True))[2] == ["❤️ Мои книги", "✅ Сохранено"]
    assert _labels(leisure_movies._movie_kb(0, saved=True))[2] == ["❤️ Моё кино", "✅ Сохранено"]
    assert _labels(leisure_music._listen_kb(saved=True))[2] == ["❤️ Мои артисты", "✅ Сохранено"]


def test_book_recommendation_skips_favorite_saved_as_structured_value(monkeypatch):
    def get_list(key, _cid):
        if key == config.BOOKS_KEY:
            return [{"value": "1984"}]
        return []

    monkeypatch.setattr(leisure_books.store, "get_list", get_list)
    monkeypatch.setattr(leisure_books.recommendation_stoplist, "values", lambda *_args: [])

    result = leisure_books._pick_good_book([
        {"title": "1984"},
        {"title": "Маленький принц"},
    ], "42")

    assert result["title"] == "Маленький принц"


def test_book_recommendation_prefers_reader_rating(monkeypatch):
    monkeypatch.setattr(leisure_books.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_books.recommendation_stoplist, "values", lambda *_args: [])

    result = leisure_books._pick_good_book([
        {"title": "Книга без оценки"},
        {"title": "Книга читателей", "rating": 4.6, "ratings_count": 240},
        {"title": "Книга с меньшей оценкой", "rating": 4.1, "ratings_count": 900},
    ], "42")

    assert result["title"] == "Книга читателей"


def test_book_genre_menu_has_only_genre_choices():
    labels = _labels(leisure_books._book_genre_menu_kb())

    assert labels[:-1] == [
        ["🧙 Фэнтези", "🚀 Фантастика"],
        ["🔍 Детектив", "😱 Триллер"],
        ["💕 Романтика", "🏛 История"],
        ["👤 Биографии", "🧠 Психология"],
    ]


def test_book_cache_drops_favorite_saved_as_structured_value(monkeypatch):
    today = datetime.now(config.TZ).date().isoformat()

    monkeypatch.setattr(leisure_books.store, "_load", lambda *_args: {
        "42": {"date": today, "item": {"title": "1984"}},
    })
    monkeypatch.setattr(
        leisure_books.store,
        "get_list",
        lambda key, _cid: [{"value": "1984"}] if key == config.BOOKS_KEY else [],
    )
    monkeypatch.setattr(leisure_books.recommendation_stoplist, "values", lambda *_args: [])

    assert leisure_books._cached_book("42") is None


def test_book_quote_matches_my_day_italic_format():
    message = leisure_books._book_text({
        "title": "1984",
        "author": "Джордж Оруэлл",
        "quote": "Война - это мир.",
    })

    assert "💭 «Война - это мир.»" in message.text
    assert "💬 Цитата" not in message.text
    assert any(entity.type == MessageEntity.ITALIC for entity in message.entities)


def test_book_card_shows_reader_rating():
    message = leisure_books._book_text({
        "title": "1984",
        "author": "Джордж Оруэлл",
        "rating": 4.7,
        "ratings_count": 1234,
    })

    assert "⭐ Оценка читателей: 4.7/5 · 1 234 оценок" in message.text


def test_leisure_home_shows_three_top_movies_in_cinemas(monkeypatch):
    class Bot:
        sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    async def movies(*_args, **_kwargs):
        return [
            {"title": "Одиссея", "rating": 7.9, "vote_count": 100, "genres": ["приключения"],
             "overview": "Одиссей возвращается домой после долгой войны и сталкивается с новыми испытаниями."},
            {"title": "Приглашение", "rating": 7.8, "vote_count": 100,
             "overview": "Таинственное приглашение на ужин превращает обычный вечер в тревожную загадку."},
            {"title": "Des preuves d'amour", "rating": 7.7, "vote_count": 100,
             "overview": "История любви, которой приходится пройти через неожиданные испытания."},
        ]

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("home must not build several recommendations")

    monkeypatch.setattr(leisure_home.store, "get_settings", lambda *_args: {"city": "Алкмар"})
    monkeypatch.setattr(leisure_home.leisure_movies, "get_local_now_playing", movies)
    monkeypatch.setattr(leisure_home.leisure_music, "_cached_artist", lambda *_args: None)
    monkeypatch.setattr(leisure_home.leisure_books, "_cached_book", lambda *_args: None)
    monkeypatch.setattr(leisure_home.leisure_concerts, "_concerts_cache_get", lambda *_args: None)
    monkeypatch.setattr(leisure_home.leisure_music, "send_listen", unexpected)
    monkeypatch.setattr(leisure_home.leisure_books, "get_current_book", unexpected)
    monkeypatch.setattr(leisure_home.leisure_concerts, "_fetch_favorite_events", unexpected)
    bot = Bot()

    import asyncio
    asyncio.run(leisure_home.send_home(bot, "42"))

    text = bot.sent[0]["text"]
    assert "Три фильма, которые сейчас идут в кино." in text
    assert "🍿 Развлечения на сегодня · Алкмар" in text
    assert "🎫 Событие" not in text
    assert "🎟️ Идёт в кино" in text
    assert "🎟️ Сейчас в кино" not in text
    assert "Одиссея" in text
    assert "Приглашение" in text
    assert "Des preuves d'amour" in text
    assert "Одиссея · ⭐ 7.9 · Приключения" in text
    assert "Таинственное приглашение" not in text
    assert "испытаниями.\n\n• Приглашение" not in text
    assert "💭" not in text
    assert "🎧 Послушать" not in text
    assert "📖 Почитать" not in text
    assert "🎫 В этом месяце" not in text


def test_leisure_home_uses_compact_verified_event_teaser(monkeypatch):
    class Bot:
        sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    event = {
        "name": "Lowlands 2026 - Festivalticket",
        "_artist": "Romy",
        "classifications": [{"genre": {"name": "Indie Pop"}}],
        "dates": {"start": {"localDate": "2099-08-21"}},
        "_embedded": {"venues": [{"city": {"name": "Biddinghuizen"}}]},
    }

    async def movies(*_args, **_kwargs):
        return [{
            "title": "Приглашение",
            "rating": 7.8,
            "vote_count": 100,
            "genres": ["драма"],
            "overview": "Это длинный синопсис, который не должен попадать в компактную витрину досуга.",
        }]

    monkeypatch.setattr(leisure_home.store, "get_settings", lambda *_args: {"city": "Алкмар", "cc": "NL"})
    monkeypatch.setattr(leisure_home.leisure_concerts, "_concerts_cache_get", lambda *_args: [event])
    monkeypatch.setattr(leisure_home.leisure_movies, "get_local_now_playing", movies)
    monkeypatch.setattr(leisure_home.leisure_music, "_cached_artist", lambda *_args: None)
    monkeypatch.setattr(leisure_home.leisure_books, "_cached_book", lambda *_args: None)
    bot = Bot()
    import asyncio
    asyncio.run(leisure_home.send_home(bot, "42"))
    text = bot.sent[0]["text"]

    assert "Romy · 21 августа · Biddinghuizen 🇳🇱" in text
    assert "Lowlands 2026 · Фестиваль" in text
    assert "Festivalticket" not in text
    assert "Indie Pop" not in text
    assert "Это длинный синопсис" not in text
    assert "Эта цитата не относится" not in text
    assert _labels(bot.sent[0]["reply_markup"]) == [
        ["🎬 Кино"], ["🎧 Музыка"], ["📖 Книги"],
        ["💾 Сохранённое", "🎚️ Предпочтения"], ["#️⃣ Главная"],
    ]
