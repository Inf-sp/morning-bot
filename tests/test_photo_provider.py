from unittest.mock import patch

import pytest

import photo_provider as pp


def _recipe(**overrides):
    base = {
        "name": "Shakshuka",
        "recipe_slug": "shakshuka-test",
        "cuisine": "turkish",
        "photo_query": "shakshuka turkish breakfast",
        "main_ingredients_en": "eggs, tomatoes",
        "dish_type_en": "stew",
        "meal_type_en": "breakfast",
    }
    base.update(overrides)
    return base


def _pexels_photo(alt="", photo_url="https://img/large.jpg"):
    return {
        "id": 1,
        "alt": alt,
        "photographer": "John",
        "photographer_url": "https://pexels.com/@john",
        "src": {"large": photo_url, "small": "https://img/small.jpg"},
        "url": "https://pexels.com/photo/1",
    }


def _unsplash_photo(description="", photo_url="https://img/unsplash.jpg"):
    return {
        "id": "u1",
        "description": description,
        "alt_description": "",
        "user": {"name": "Anna", "links": {"html": "https://unsplash.com/@anna"}},
        "urls": {"regular": photo_url, "thumb": "https://img/unsplash_thumb.jpg"},
        "links": {"html": "https://unsplash.com/photos/u1"},
    }


# ---------- fallback-цепочка запросов ----------

@pytest.mark.unit
def test_fallback_queries_start_with_exact_photo_query():
    queries = pp._fallback_queries(_recipe())
    assert queries[0] == "shakshuka turkish breakfast"
    assert len(queries) >= 3


@pytest.mark.unit
def test_fallback_queries_deduplicate_and_skip_empty():
    queries = pp._fallback_queries(_recipe(photo_query="", main_ingredients_en="", dish_type_en="", meal_type_en=""))
    assert all(q for q in queries)
    assert len(queries) == len(set(queries))


@pytest.mark.unit
def test_fallback_queries_fall_back_to_name_and_cuisine_when_no_photo_query():
    queries = pp._fallback_queries(_recipe(photo_query=""))
    assert "shakshuka turkish food" in queries[0]


# ---------- scoring ----------

@pytest.mark.unit
def test_score_rewards_dish_and_ingredient_matches():
    recipe = _recipe()
    photo = _pexels_photo(alt="Delicious shakshuka dish served with eggs and tomatoes")
    score = pp._score_photo(photo, "pexels", recipe, "shakshuka turkish breakfast")
    assert score >= pp._SCORE_THRESHOLD


@pytest.mark.unit
def test_score_penalizes_raw_ingredients():
    recipe = _recipe()
    photo = _pexels_photo(alt="Raw ingredients on a market table: fresh tomatoes and eggs")
    score = pp._score_photo(photo, "pexels", recipe, "shakshuka turkish breakfast")
    assert score < pp._SCORE_THRESHOLD


@pytest.mark.unit
def test_score_penalizes_people_as_main_subject():
    recipe = _recipe()
    photo = _pexels_photo(alt="Woman hands cooking in kitchen, person preparing food")
    score = pp._score_photo(photo, "pexels", recipe, "shakshuka turkish breakfast")
    assert score < pp._SCORE_THRESHOLD


@pytest.mark.unit
def test_score_is_neutral_when_no_description_available():
    recipe = _recipe()
    photo = _pexels_photo(alt="")
    score = pp._score_photo(photo, "pexels", recipe, "shakshuka turkish breakfast")
    assert score == 0


@pytest.mark.unit
def test_best_photo_picks_highest_scoring_candidate_across_queries():
    recipe = _recipe()
    photos_by_query = [
        ("q1", [_pexels_photo(alt="raw ingredients on a table")]),
        ("q2", [_pexels_photo(alt="delicious shakshuka dish served hot", photo_url="https://img/best.jpg")]),
    ]
    best = pp._best_photo(photos_by_query, recipe, "pexels")
    assert best is not None
    photo, query, score = best
    assert photo["src"]["large"] == "https://img/best.jpg"
    assert query == "q2"


# ---------- оркестрация Pexels -> Unsplash ----------

@pytest.mark.unit
def test_get_dish_photo_prefers_pexels_when_good_enough():
    recipe = _recipe(recipe_slug="orchestration-pexels-good")
    good = _pexels_photo(alt="Delicious shakshuka dish served with eggs and tomatoes")
    with patch("pexels.search_photos", return_value=[good]), \
         patch("unsplash.search_photos") as mock_unsplash:
        result = pp.get_dish_photo(recipe)
    assert result["source"] == "pexels"
    assert result["photo_url"] == "https://img/large.jpg"
    mock_unsplash.assert_not_called()


@pytest.mark.unit
def test_get_dish_photo_falls_back_to_unsplash_when_pexels_weak():
    recipe = _recipe(recipe_slug="orchestration-fallback")
    weak = _pexels_photo(alt="raw ingredients on a market table")
    good = _unsplash_photo(description="ramen bowl food served delicious dish")
    with patch("pexels.search_photos", return_value=[weak]), \
         patch("unsplash.search_photos", return_value=[good]):
        result = pp.get_dish_photo(recipe)
    assert result["source"] == "unsplash"
    assert result["photo_url"] == "https://img/unsplash.jpg"


@pytest.mark.unit
def test_get_dish_photo_returns_none_when_both_sources_empty():
    recipe = _recipe(recipe_slug="orchestration-empty")
    with patch("pexels.search_photos", return_value=[]), \
         patch("unsplash.search_photos", return_value=[]):
        result = pp.get_dish_photo(recipe)
    assert result is None


@pytest.mark.unit
def test_get_dish_photo_returns_none_when_both_below_threshold():
    recipe = _recipe(recipe_slug="orchestration-below-threshold")
    weak_pexels = _pexels_photo(alt="raw ingredients on a market table")
    weak_unsplash = _unsplash_photo(description="person hands cooking in kitchen")
    with patch("pexels.search_photos", return_value=[weak_pexels]), \
         patch("unsplash.search_photos", return_value=[weak_unsplash]):
        result = pp.get_dish_photo(recipe)
    assert result is None


@pytest.mark.unit
def test_get_dish_photo_returns_none_for_recipe_without_queries():
    result = pp.get_dish_photo({"recipe_slug": "empty-recipe"})
    assert result is None


# ---------- кэш ----------

@pytest.mark.unit
def test_get_dish_photo_caches_positive_result_and_skips_api_on_second_call():
    recipe = _recipe(recipe_slug="cache-positive")
    good = _pexels_photo(alt="delicious shakshuka dish served with eggs")
    with patch("pexels.search_photos", return_value=[good]) as mock_pexels:
        first = pp.get_dish_photo(recipe)
    assert first is not None

    with patch("pexels.search_photos") as mock_pexels_2, \
         patch("unsplash.search_photos") as mock_unsplash_2:
        second = pp.get_dish_photo(recipe)
    assert second == first
    mock_pexels_2.assert_not_called()
    mock_unsplash_2.assert_not_called()


@pytest.mark.unit
def test_get_dish_photo_caches_negative_result_and_skips_api_on_second_call():
    recipe = _recipe(recipe_slug="cache-negative")
    with patch("pexels.search_photos", return_value=[]), patch("unsplash.search_photos", return_value=[]):
        first = pp.get_dish_photo(recipe)
    assert first is None

    with patch("pexels.search_photos") as mock_pexels_2, \
         patch("unsplash.search_photos") as mock_unsplash_2:
        second = pp.get_dish_photo(recipe)
    assert second is None
    mock_pexels_2.assert_not_called()
    mock_unsplash_2.assert_not_called()


@pytest.mark.unit
def test_get_dish_photo_cache_key_uses_slug_when_present():
    recipe_a = _recipe(recipe_slug="same-slug", name="Shakshuka A")
    recipe_b = _recipe(recipe_slug="same-slug", name="Shakshuka B", photo_query="different query entirely")
    good = _pexels_photo(alt="delicious shakshuka dish served with eggs")
    with patch("pexels.search_photos", return_value=[good]):
        pp.get_dish_photo(recipe_a)

    with patch("pexels.search_photos") as mock_pexels, patch("unsplash.search_photos") as mock_unsplash:
        result = pp.get_dish_photo(recipe_b)
    assert result is not None
    mock_pexels.assert_not_called()
    mock_unsplash.assert_not_called()
