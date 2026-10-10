import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import assistant


def test_answer_without_final_punctuation_gets_period():
    assert assistant._with_final_period("чтобы они не промокли") == "чтобы они не промокли."
    assert assistant._with_final_period("Смотри «Дюна»") == "Смотри «Дюна»."


def test_answer_with_punctuation_emoji_or_code_is_unchanged():
    for text in ("Готово.", "Правда?", "Ура!", "Итак…", "ок 👍", "x\n```", ""):
        assert assistant._with_final_period(text) == text
