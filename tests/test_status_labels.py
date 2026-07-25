import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot_callbacks


def test_inline_status_starts_with_action_specific_text():
    cases = {
        "m_food_next": "⏳ Ищу рецепт...",
        "w_look": "⏳ Ищу образ...",
        "movie_reco": "🎬 Ищу кино...",
        "book_reco": "📚 Ищу книгу...",
        "listen_no": "🎧 Ищу музыку...",
        "a_concerts_nl": "🎫 Ищу концерт...",
        "a_trav_go": "✈️ Ищу поездку...",
        "game_again": "🕵️ Ищу загадку...",
        "a_watch": "🎬 Ищу кино...",
        "a_read": "📚 Ищу книгу...",
        "a_listen": "🎧 Ищу музыку...",
        "m_food": "⏳ Ищу рецепт...",
        "m_wardrobe": "⏳ Ищу образ...",
        "m_leisure": "🍿 Ищу рекомендации...",
    }

    for data, expected in cases.items():
        assert bot_callbacks._status_stages(data)[0][1] == expected
