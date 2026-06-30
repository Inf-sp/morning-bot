import pytest

import leisure


@pytest.mark.unit
def test_normalize_movie_items_tolerates_llm_shape_drift():
    items = leisure._normalize_movie_items([
        "Олдбой (2003)",
        {"name": "Пылающий", "desc": "медленный триллер"},
        {"title": ""},
        None,
    ])

    assert items == [
        {"title": "Олдбой (2003)", "title_en": "", "hook": ""},
        {"title": "Пылающий", "title_en": "", "hook": "медленный триллер"},
    ]


@pytest.mark.unit
def test_pick_good_movie_skips_non_dict_items():
    it, tm = leisure._pick_good_movie(["битый item", {"title": "Решение уйти"}], set())

    assert tm is None
    assert it == {"title": "Решение уйти"}


def test_book_text_uses_editorial_structure():
    text = leisure._book_text({
        "author": "Олдос Хаксли",
        "title": "Дивный новый мир",
        "year": "1932",
        "desc": "Генетический рай без свободы.",
        "why": ["-Анти-Оруэлл: общество ломают развлечениями.", "Главный конфликт: чужак внутри системы."],
        "plot": "Бернард привозит Дикаря из резервации. Тот ломает фасад счастливого концлагеря.",
        "quote": "Лучше быть несчастным в свободе.",
        "hook": "лишний итог",
    })

    assert text.startswith("📚 <b>Олдос Хаксли • «Дивный новый мир» <i>(1932)</i></b>")
    assert "🎯 <b>Почему стоит читать</b>" in text
    assert "✍🏻 <b>Коротко о сюжете</b>\nБернард" in text
    assert "💬 <b>Цитата</b>\n«Лучше быть несчастным в свободе.»" in text
    assert "-Анти" not in text
    assert "лишний итог" not in text
