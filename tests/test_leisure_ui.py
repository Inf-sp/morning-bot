import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_books
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
        ["✨ Подобрать кино"],
        ["🎟️ Весь прокат"],
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
