import pytest

import pexels
import photo_provider


def _photo(photo_id=123):
    return {"id": photo_id, "src": {"large": "https://images.pexels.com/photos/123/large.jpg"}}


def test_first_pexels_result_wins_without_fallback(monkeypatch):
    calls = []

    def fake_search(**kwargs):
        calls.append(kwargs)
        return [_photo(456)]

    monkeypatch.setattr(photo_provider.pexels, "search_photos", fake_search)

    recipe = {
        "name": "Омлет",
        "photo_query_en": "spinach feta omelette",
        "photo_fallback_queries": ["spinach omelette", "vegetable omelette"],
    }

    result = photo_provider.get_dish_photo(recipe)

    assert result["photo_url"].endswith("/large.jpg")
    assert result["photo_id"] == 456
    assert result["photo_query_used"] == "spinach feta omelette"
    assert result["photo_fallback_index"] == 0
    assert recipe["photo_lookup_status"] == "found"
    assert len(calls) == 1
    assert calls[0] == {
        "query": "spinach feta omelette",
        "orientation": "square",
        "size": "large",
        "locale": "en-US",
        "per_page": 1,
        "timeout": 3,
    }


def test_fallbacks_are_sequential_and_stop_on_first_photo(monkeypatch):
    calls = []

    def fake_search(**kwargs):
        calls.append(kwargs["query"])
        if kwargs["query"] == "vegetable omelette":
            return [_photo(789)]
        return []

    monkeypatch.setattr(photo_provider.pexels, "search_photos", fake_search)

    recipe = {
        "photo_query_en": "spinach feta omelette",
        "photo_fallback_queries": ["spinach omelette", "vegetable omelette", "egg breakfast"],
    }

    result = photo_provider.get_dish_photo(recipe)

    assert result["photo_id"] == 789
    assert result["photo_query_used"] == "vegetable omelette"
    assert result["photo_fallback_index"] == 2
    assert calls == ["spinach feta omelette", "spinach omelette", "vegetable omelette"]


def test_not_found_is_saved_and_not_retried(monkeypatch):
    calls = []

    def fake_search(**kwargs):
        calls.append(kwargs["query"])
        return []

    monkeypatch.setattr(photo_provider.pexels, "search_photos", fake_search)

    recipe = {
        "photo_query_en": "tomato lentil soup",
        "photo_fallback_queries": ["lentil soup", "tomato soup"],
    }

    assert photo_provider.get_dish_photo(recipe) is None
    assert recipe["photo_lookup_status"] == "not_found"
    assert recipe["photo_query_used"] == "tomato soup"
    assert photo_provider.get_dish_photo(recipe) is None
    assert calls == ["tomato lentil soup", "lentil soup", "tomato soup"]


def test_saved_found_photo_is_not_retried(monkeypatch):
    def fail_search(**kwargs):
        pytest.fail("Pexels should not be called for a saved photo")

    monkeypatch.setattr(photo_provider.pexels, "search_photos", fail_search)

    recipe = {
        "photo_lookup_status": "found",
        "photo_source": "pexels",
        "photo_id": 111,
        "photo_url": "https://images.pexels.com/photos/111/large.jpg",
        "photo_query_used": "greek salad",
        "photo_fallback_index": 0,
    }

    result = photo_provider.get_dish_photo(recipe)

    assert result["photo_id"] == 111
    assert result["photo_url"] == recipe["photo_url"]


def test_error_stops_without_not_found(monkeypatch):
    calls = []

    def fake_search(**kwargs):
        calls.append(kwargs["query"])
        raise pexels.PexelsTimeoutError("timeout")

    monkeypatch.setattr(photo_provider.pexels, "search_photos", fake_search)

    recipe = {
        "photo_query_en": "creamy mushroom pasta",
        "photo_fallback_queries": ["mushroom pasta", "creamy pasta"],
    }

    assert photo_provider.get_dish_photo(recipe) is None
    assert recipe["photo_lookup_status"] == "error"
    assert recipe["photo_lookup_error"] == "timeout"
    assert calls == ["creamy mushroom pasta"]
