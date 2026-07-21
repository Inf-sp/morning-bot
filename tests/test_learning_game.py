from learning_game import _get_english_query


def test_get_english_query_english():
    st = {
        "answer": "elephant",
        "aliases": ["слон", "elephant", "olifant"]
    }
    assert _get_english_query(st, "английский") == "elephant"


def test_get_english_query_dutch():
    st = {
        "answer": "olifant",
        "aliases": ["слон", "elephant", "olifant"]
    }
    assert _get_english_query(st, "нидерландский") == "elephant"


def test_get_english_query_fallback():
    st = {
        "answer": "яблоко",
        "aliases": ["яблоко"]
    }
    assert _get_english_query(st, "русский") == "яблоко"
