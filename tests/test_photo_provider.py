import time
from unittest.mock import patch

import pytest

import photo_provider as pp


def _recipe(**overrides):
    base = {
        "name": "Шакшука с фетой",
        "name_en": "Shakshuka with feta",
        "cuisine": "turkish",
        "photo_query_en": "shakshuka with feta food",
        "photo_fallback_queries": [
            "shakshuka food",
            "eggs tomato sauce skillet food",
            "middle eastern breakfast food",
        ],
        "main_ingredients_en": "eggs, tomato sauce, feta",
        "visual_tags": ["eggs", "tomato sauce", "feta", "skillet", "prepared dish"],
        "negative_visual_tags": ["raw ingredients", "grocery", "restaurant interior", "chef", "kitchen"],
    }
    base.update(overrides)
    return base


def _pexels_photo(photo_id=1, alt="", photo_url="https://img/large.jpg", width=1200, height=1200):
    return {
        "id": photo_id,
        "alt": alt,
        "width": width,
        "height": height,
        "photographer": "John",
        "photographer_url": "https://pexels.com/@john",
        "src": {"large": photo_url, "medium": photo_url, "small": "https://img/small.jpg", "tiny": "https://img/tiny.jpg"},
        "url": "https://pexels.com/photo/1",
    }


def _unsplash_photo(photo_id="u1", description="", photo_url="https://img/unsplash.jpg"):
    return {
        "id": photo_id,
        "description": description,
        "alt_description": "",
        "width": 1200,
        "height": 1200,
        "user": {"name": "Anna", "links": {"html": "https://unsplash.com/@anna"}},
        "urls": {"regular": photo_url, "small": photo_url, "thumb": "https://img/unsplash_thumb.jpg"},
        "links": {"html": "https://unsplash.com/photos/u1"},
    }


def _good_vision(match_score=90, contains_main_ingredients=True):
    return {
        "match_score": match_score,
        "is_prepared_dish": True,
        "contains_main_ingredients": contains_main_ingredients,
        "has_unrelated_subject": False,
        "reason": "matches",
    }


def _bad_vision(match_score=20, is_prepared_dish=False, has_unrelated_subject=True):
    return {
        "match_score": match_score,
        "is_prepared_dish": is_prepared_dish,
        "contains_main_ingredients": False,
        "has_unrelated_subject": has_unrelated_subject,
        "reason": "does not match",
    }


# ---------- цепочка запросов (без синтетического общего fallback) ----------

@pytest.mark.unit
def test_query_chain_starts_with_exact_query_then_fallbacks_in_order():
    queries = pp._query_chain(_recipe())
    assert queries[0] == "shakshuka with feta food"
    assert queries[1:] == [
        "shakshuka food",
        "eggs tomato sauce skillet food",
        "middle eastern breakfast food",
    ]


@pytest.mark.unit
def test_query_chain_never_appends_generic_food_fallback():
    queries = pp._query_chain(_recipe(photo_query_en="", photo_fallback_queries=[]))
    assert queries == []


@pytest.mark.unit
def test_query_chain_deduplicates():
    queries = pp._query_chain(_recipe(photo_fallback_queries=["shakshuka with feta food", "shakshuka food"]))
    assert queries.count("shakshuka with feta food") == 1


# ---------- сценарий 1: генерация очереди из 10 рецептов не трогает фото-API ----------

@pytest.mark.unit
def test_batch_queue_generation_never_calls_photo_apis(monkeypatch):
    import asyncio
    import balance

    cid = "photo-arch-queue-1"
    batch = [dict(_recipe(name=f"Рецепт {i}", name_en=f"Dish {i}")) for i in range(10)]
    monkeypatch.setattr(balance, "_gen_recipe_batch", lambda *a, **kw: batch)

    with patch("pexels.search_photos") as mock_pexels, \
         patch("unsplash.search_photos") as mock_unsplash, \
         patch("vision.validate_dish_photo") as mock_vision:
        asyncio.run(balance._generate_and_store_queue(cid, "breakfast"))

    mock_pexels.assert_not_called()
    mock_unsplash.assert_not_called()
    mock_vision.assert_not_called()

    stored = balance.get_recipe_queue(cid)
    assert stored["items"][0]["photo_query_en"] == "shakshuka with feta food"


# ---------- сценарий 2: показ первого рецепта запускает подбор фото только для него ----------

@pytest.mark.unit
def test_showing_first_recipe_triggers_photo_lookup_only_for_that_recipe(monkeypatch):
    import asyncio
    import balance

    cid = "photo-arch-show-1"
    batch = [dict(_recipe(name=f"Рецепт {i}", name_en=f"Dish {i}")) for i in range(10)]
    monkeypatch.setattr(balance, "_gen_recipe_batch", lambda *a, **kw: batch)

    calls = []

    def fake_get_dish_photo(recipe, meal_type=""):
        calls.append(recipe.get("name_en"))
        return None

    monkeypatch.setattr(balance.photo_provider, "get_dish_photo", fake_get_dish_photo)

    class FakeBot:
        async def send_message(self, **kw):
            pass

        async def send_photo(self, **kw):
            pass

    asyncio.run(balance.enter_meal(FakeBot(), cid, "breakfast"))

    assert calls == ["Dish 0"]


# ---------- сценарий 3: не более 3 vision-вызовов на один показ ----------

@pytest.mark.unit
def test_vision_called_at_most_three_times_per_recipe():
    recipe = _recipe(name_en="Scenario-3 Dish")
    pexels_candidates = [_pexels_photo(photo_id=i, alt="raw ingredients grocery") for i in range(5)]
    unsplash_candidates = [_unsplash_photo(photo_id=f"u{i}", description="raw ingredients grocery") for i in range(5)]

    with patch("pexels.search_photos", return_value=pexels_candidates), \
         patch("unsplash.search_photos", return_value=unsplash_candidates), \
         patch("vision.validate_dish_photo", return_value=_bad_vision()) as mock_vision:
        result = pp.get_dish_photo(recipe)

    assert result is None
    assert mock_vision.call_count <= pp.MAX_VISION_CALLS_PER_RECIPE
    assert mock_vision.call_count == pp.MAX_PEXELS_VISION_CALLS + pp.MAX_UNSPLASH_VISION_CALLS


@pytest.mark.unit
def test_pexels_gets_at_most_two_vision_calls_unsplash_at_most_one():
    recipe = _recipe(name_en="Scenario-3b Dish")
    pexels_candidates = [_pexels_photo(photo_id=i, alt="raw ingredients grocery") for i in range(5)]
    unsplash_candidates = [_unsplash_photo(photo_id=f"u{i}", description="raw ingredients grocery") for i in range(5)]

    vision_sources_called = []

    def fake_vision(image_url, recipe, meal_type=""):
        vision_sources_called.append(image_url)
        return _bad_vision()

    with patch("pexels.search_photos", return_value=pexels_candidates), \
         patch("unsplash.search_photos", return_value=unsplash_candidates), \
         patch("vision.validate_dish_photo", side_effect=fake_vision):
        pp.get_dish_photo(recipe)

    pexels_calls = [u for u in vision_sources_called if "large.jpg" in u]
    unsplash_calls = [u for u in vision_sources_called if "unsplash.jpg" in u]
    assert len(pexels_calls) <= pp.MAX_PEXELS_VISION_CALLS
    assert len(unsplash_calls) <= pp.MAX_UNSPLASH_VISION_CALLS


# ---------- сценарий 4 и 5: TTL кэша ----------

@pytest.mark.unit
def test_positive_cache_survives_within_30_days():
    recipe = _recipe(name_en="Scenario-4 Cached Dish")
    good = _pexels_photo(alt="prepared dish served hot delicious food")

    with patch("pexels.search_photos", return_value=[good]), \
         patch("vision.validate_dish_photo", return_value=_good_vision()):
        first = pp.get_dish_photo(recipe)
    assert first is not None

    key = pp._cache_key(recipe)
    with patch("time.time", return_value=time.time() + pp._CACHE_TTL_POSITIVE_SECONDS - 60):
        cached = pp._cache_get(key)
    assert cached == first


@pytest.mark.unit
def test_positive_cache_expires_after_30_days():
    recipe = _recipe(name_en="Scenario-4b Expiring Dish")
    good = _pexels_photo(alt="prepared dish served hot delicious food")

    with patch("pexels.search_photos", return_value=[good]), \
         patch("vision.validate_dish_photo", return_value=_good_vision()):
        pp.get_dish_photo(recipe)

    key = pp._cache_key(recipe)
    with patch("time.time", return_value=time.time() + pp._CACHE_TTL_POSITIVE_SECONDS + 60):
        cached = pp._cache_get(key)
    assert cached is None


@pytest.mark.unit
def test_negative_cache_survives_within_24_hours():
    recipe = _recipe(name_en="Scenario-5 Negative Dish")

    with patch("pexels.search_photos", return_value=[]), patch("unsplash.search_photos", return_value=[]):
        result = pp.get_dish_photo(recipe)
    assert result is None

    key = pp._cache_key(recipe)
    with patch("time.time", return_value=time.time() + pp._CACHE_TTL_NEGATIVE_SECONDS - 60):
        cached = pp._cache_get(key)
    assert cached is False


@pytest.mark.unit
def test_negative_cache_expires_after_24_hours_unlike_positive():
    recipe = _recipe(name_en="Scenario-5b Negative Expiring Dish")

    with patch("pexels.search_photos", return_value=[]), patch("unsplash.search_photos", return_value=[]):
        pp.get_dish_photo(recipe)

    key = pp._cache_key(recipe)
    with patch("time.time", return_value=time.time() + pp._CACHE_TTL_NEGATIVE_SECONDS + 60):
        cached = pp._cache_get(key)
    assert cached is None


# ---------- сценарий 6: смена photo_query_en создаёт новый ключ кэша ----------

@pytest.mark.unit
def test_changing_photo_query_en_creates_different_cache_key():
    recipe_a = _recipe(name_en="Same Dish Name", photo_query_en="query one")
    recipe_b = _recipe(name_en="Same Dish Name", photo_query_en="query two")

    key_a = pp._cache_key(recipe_a)
    key_b = pp._cache_key(recipe_b)

    assert key_a != key_b
    assert key_a.startswith("recipe_photo:v2:same dish name:")
    assert key_b.startswith("recipe_photo:v2:same dish name:")


@pytest.mark.unit
def test_new_query_does_not_reuse_old_negative_cache():
    recipe_v1 = _recipe(name_en="Evolving Dish", photo_query_en="bad query")
    with patch("pexels.search_photos", return_value=[]), patch("unsplash.search_photos", return_value=[]):
        result_v1 = pp.get_dish_photo(recipe_v1)
    assert result_v1 is None

    recipe_v2 = _recipe(name_en="Evolving Dish", photo_query_en="better query")
    good = _pexels_photo(alt="prepared dish served hot delicious food")
    with patch("pexels.search_photos", return_value=[good]), \
         patch("vision.validate_dish_photo", return_value=_good_vision()):
        result_v2 = pp.get_dish_photo(recipe_v2)

    assert result_v2 is not None


# ---------- сценарий 7: contains_main_ingredients не блокирует прохождение ----------

@pytest.mark.unit
def test_high_score_photo_passes_even_without_main_ingredients_visible():
    vision_result = _good_vision(match_score=85, contains_main_ingredients=False)
    assert pp._vision_passes(vision_result) is True


@pytest.mark.unit
def test_get_dish_photo_accepts_candidate_missing_main_ingredients_signal():
    recipe = _recipe(name_en="Scenario-7 Dish")
    good = _pexels_photo(alt="prepared dish served hot delicious food")

    with patch("pexels.search_photos", return_value=[good]), \
         patch("vision.validate_dish_photo", return_value=_good_vision(contains_main_ingredients=False)):
        result = pp.get_dish_photo(recipe)

    assert result is not None


@pytest.mark.unit
def test_vision_passes_still_requires_prepared_dish_and_no_unrelated_subject():
    assert pp._vision_passes(_good_vision(match_score=78)) is True
    assert pp._vision_passes(_good_vision(match_score=77)) is False
    assert pp._vision_passes(_bad_vision(match_score=90, is_prepared_dish=False)) is False
    assert pp._vision_passes(_bad_vision(match_score=90, is_prepared_dish=True, has_unrelated_subject=True)) is False
    assert pp._vision_passes(None) is False


# ---------- сценарий 8: общий таймаут 6 секунд ----------

@pytest.mark.unit
def test_search_stops_and_returns_none_when_timeout_exceeded():
    recipe = _recipe(name_en="Scenario-8 Slow Dish")
    good = _pexels_photo(alt="prepared dish served hot delicious food")

    start = time.monotonic()
    # Первый вызов time.monotonic() внутри get_dish_photo считает deadline;
    # подделываем "прошедшее" время так, что deadline уже истёк к первой проверке.
    fake_times = iter([start, start + pp.PHOTO_LOOKUP_TIMEOUT_SECONDS + 1] + [start + 999] * 20)

    with patch("pexels.search_photos", return_value=[good]) as mock_pexels, \
         patch("unsplash.search_photos", return_value=[good]) as mock_unsplash, \
         patch("vision.validate_dish_photo", return_value=_good_vision()) as mock_vision, \
         patch("time.monotonic", side_effect=lambda: next(fake_times)):
        result = pp.get_dish_photo(recipe)

    assert result is None
    mock_vision.assert_not_called()


@pytest.mark.unit
def test_timeout_constant_is_six_seconds():
    assert pp.PHOTO_LOOKUP_TIMEOUT_SECONDS == 6


# ---------- сценарий: Pexels недоступен -> фолбэк на Unsplash, без исключений ----------

@pytest.mark.unit
def test_pexels_error_degrades_to_unsplash_without_raising():
    recipe = _recipe(name_en="Scenario-Error Dish")
    good_unsplash = _unsplash_photo(description="prepared dish served hot delicious food")

    with patch("pexels.search_photos", side_effect=Exception("pexels down")), \
         patch("unsplash.search_photos", return_value=[good_unsplash]), \
         patch("vision.validate_dish_photo", return_value=_good_vision()):
        result = pp.get_dish_photo(recipe)  # не должно поднять исключение наружу

    assert result is not None
    assert result["source"] == "unsplash"


# ---------- scoring: только сортировка, не финальное решение ----------

@pytest.mark.unit
def test_score_does_not_penalize_missing_dish_name_in_metadata():
    recipe = _recipe()
    no_name_but_food = _pexels_photo(alt="delicious prepared meal served hot")
    score = pp._score_candidate(no_name_but_food, "pexels", recipe)
    assert score > 0  # не наказываем за отсутствие точного названия блюда в metadata


@pytest.mark.unit
def test_score_penalizes_raw_ingredients_strongly():
    recipe = _recipe()
    photo = _pexels_photo(alt="raw ingredients and grocery items on a table")
    score = pp._score_candidate(photo, "pexels", recipe)
    assert score <= -5


@pytest.mark.unit
def test_score_penalizes_kitchen_and_chef():
    recipe = _recipe()
    photo = _pexels_photo(alt="chef cooking in a restaurant interior kitchen")
    score = pp._score_candidate(photo, "pexels", recipe)
    assert score < 0


@pytest.mark.unit
def test_score_is_used_for_sorting_not_as_final_gate():
    """Даже кандидат с низким локальным score должен доходить до vision, если
    он попадает в top-N по лимиту — локальный scoring не отбраковывает сам."""
    recipe = _recipe(name_en="Scenario-Score-Gate Dish")
    low_score_but_valid = _pexels_photo(photo_id=1, alt="", width=100, height=90)  # score == 0, не мусор

    with patch("pexels.search_photos", return_value=[low_score_but_valid]), \
         patch("vision.validate_dish_photo", return_value=_good_vision()) as mock_vision:
        result = pp.get_dish_photo(recipe)

    mock_vision.assert_called_once()
    assert result is not None


# ---------- прочее: без имени/запросов, одинаковые правила по meal_type ----------

@pytest.mark.unit
def test_get_dish_photo_returns_none_for_recipe_without_name_en_or_queries():
    result = pp.get_dish_photo({"name": "Без английского имени"})
    assert result is None


@pytest.mark.unit
@pytest.mark.parametrize("meal_type", ["breakfast", "lunch", "dinner", "fridge"])
def test_same_rules_applied_across_all_meal_types(meal_type):
    recipe = _recipe(name_en=f"Scenario-Meal Dish {meal_type}")
    good = _pexels_photo(alt="prepared dish served hot delicious food")

    with patch("pexels.search_photos", return_value=[good]), \
         patch("vision.validate_dish_photo", return_value=_good_vision()) as mock_vision:
        result = pp.get_dish_photo(recipe, meal_type=meal_type)

    assert result is not None
    call_args = mock_vision.call_args
    assert meal_type in call_args.args or call_args.kwargs.get("meal_type") == meal_type


@pytest.mark.unit
def test_pexels_called_with_square_large_locale_and_per_page_15():
    recipe = _recipe(name_en="Scenario-Params Dish")
    good = _pexels_photo(alt="prepared dish served hot delicious food")

    with patch("requests.get") as mock_get, \
         patch("config.PEXELS_API_KEY", "test-key"), \
         patch("vision.validate_dish_photo", return_value=_good_vision()):
        response = mock_get.return_value
        response.status_code = 200
        response.json.return_value = {"photos": [good]}
        pp.get_dish_photo(recipe)

    assert mock_get.called
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["orientation"] == "square"
    assert kwargs["params"]["size"] == "large"
    assert kwargs["params"]["locale"] == "en-US"
    assert kwargs["params"]["per_page"] == 15
