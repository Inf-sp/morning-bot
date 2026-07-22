import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_books
import leisure_home
import leisure_music
import leisure_movies
from ui.menu import _SCREENS


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_leisure_home_contains_only_four_sections_and_home():
    rows = _SCREENS["m_leisure"][3]
    assert [label for row in rows for label, _ in row] == [
        "🎫 Концерты", "🎬 Кино", "🎧 Музыка", "📖 Книги", "#️⃣ Главная"
    ]


def test_movie_home_uses_clear_recommendation_labels():
    labels = _labels(leisure_movies._movie_home_kb())
    assert labels == [
        ["✨ Другое кино"],
        ["🎟️ Сейчас в кино"],
        ["🎭 По жанру", "🌙 По настроению"],
        ["❤️ Моё кино", "💾 Смотреть позже"],
        ["🎚️ Предпочтения"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_book_and_music_home_follow_same_model():
    assert _labels(leisure_books.books_home_keyboard())[:4] == [
        ["✨ Подобрать книгу"], ["❤️ Мои книги"],
        ["💾 Почитать позже"], ["🎚️ Предпочтения"],
    ]
    assert _labels(leisure_music.music_home_keyboard())[:4] == [
        ["✨ Подобрать музыку"], ["❤️ Мои артисты"],
        ["💾 Послушать позже"], ["🎚️ Предпочтения"],
    ]
    assert leisure_books.books_home_keyboard().inline_keyboard[0][0].callback_data == "book_reco"
    assert leisure_music.music_home_keyboard().inline_keyboard[0][0].callback_data == "music_reco"


def test_recommendation_cards_use_content_specific_next_labels():
    assert _labels(leisure_movies._movie_kb(0))[0] == ["✨ Другое кино"]
    assert _labels(leisure_books._book_kb(0))[0] == ["✨ Другая книга"]
    assert _labels(leisure_music._listen_kb())[0] == ["✨ Другой артист"]


def test_leisure_home_is_a_compact_cross_category_showcase(monkeypatch):
    class Bot:
        sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    async def movies(*_args, **_kwargs):
        return [
            {"title": "Одиссея", "rating": 7.9, "vote_count": 100, "genres": ["приключения"]},
            {"title": "Приглашение", "rating": 7.8, "vote_count": 100},
            {"title": "Des preuves d'amour", "rating": 7.7, "vote_count": 100},
        ]

    async def artist(*_args, **_kwargs):
        return {"artist": "Jungle", "tracks": ["Volcano"], "desc": "Соул и электро-фанк."}

    async def book(*_args, **_kwargs):
        return {"author": "Автор", "title": "Название", "year": "2026"}

    async def concerts(*_args, **_kwargs):
        return []

    monkeypatch.setattr(leisure_home.store, "get_settings", lambda *_args: {"city": "Алкмар"})
    monkeypatch.setattr(leisure_home.leisure_movies, "get_local_now_playing", movies)
    monkeypatch.setattr(leisure_home.leisure_music, "send_listen", artist)
    monkeypatch.setattr(leisure_home.leisure_books, "get_current_book", book)
    monkeypatch.setattr(leisure_home.leisure_concerts, "_fetch_favorite_events", concerts)
    bot = Bot()

    import asyncio
    asyncio.run(leisure_home.send_home(bot, "42"))

    text = bot.sent[0]["text"]
    assert "🎬 В кино сегодня" in text
    assert "Ещё в кино:" in text
    assert "🎧 Послушать" in text and "Jungle · Volcano" in text
    assert "📖 Почитать" in text and "Автор · «Название»" in text
    assert "🎫 В этом месяце" not in text
    assert _labels(bot.sent[0]["reply_markup"]) == [
        ["🎫 Концерты", "🎬 Кино"], ["🎧 Музыка", "📖 Книги"], ["#️⃣ Главная"],
    ]
