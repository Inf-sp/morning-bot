"""TheMealDB v1: базовые рецепты для последующей адаптации через AI-router."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import date

import requests

import api_usage
import config
import util


_FILTER_TTL = 24 * 60 * 60
_RECIPE_TTL = 7 * 24 * 60 * 60

_INGREDIENT_ALIASES = (
    (r"кур|chicken|kip", "chicken"),
    (r"говяд|beef|rund", "beef"),
    (r"свин|pork|varken", "pork"),
    (r"баран|ягн|lamb", "lamb"),
    (r"лосос|salmon", "salmon"),
    (r"кревет|shrimp|prawn|garnaal", "prawns"),
    (r"тун[еe]ц|tuna", "tuna"),
    (r"яйц|egg|eieren", "egg"),
    (r"рис|rice|rijst", "rice"),
    (r"картоф|potato|aardappel", "potatoes"),
    (r"помид|томат|tomato", "tomatoes"),
    (r"гриб|mushroom|champignon", "mushrooms"),
    (r"сыр|cheese|kaas", "cheese"),
    (r"нут|chickpea", "chickpeas"),
    (r"чечев|lentil", "lentils"),
    (r"фасол|bean|bonen", "beans"),
    (r"шпинат|spinach", "spinach"),
    (r"баклаж|aubergine|eggplant", "aubergine"),
)


def _api_key() -> str:
    key = str(config.THEMEALDB_API_KEY or "1").strip()
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else "1"


def _url(endpoint: str) -> str:
    endpoint = re.sub(r"[^a-z.]", "", str(endpoint or "").lower())
    return f"https://www.themealdb.com/api/json/v1/{_api_key()}/{endpoint}"


def _request(endpoint: str, params=None, ttl=_FILTER_TTL) -> list[dict]:
    params = dict(params or {})
    cache_key = f"{endpoint}:{sorted(params.items())}"
    cached = util.ttl_get("themealdb", cache_key, ttl)
    if isinstance(cached, list):
        api_usage.record_cache_hit("themealdb")
        return cached
    started = time.time()
    try:
        response = requests.get(_url(endpoint), params=params, timeout=8)
    except requests.exceptions.Timeout:
        api_usage.record_request("themealdb", ok=False, error="timeout")
        return []
    except requests.exceptions.RequestException:
        api_usage.record_request("themealdb", ok=False, error="network_error")
        return []
    latency_ms = int((time.time() - started) * 1000)
    if response.status_code != 200:
        api_usage.record_request(
            "themealdb", ok=False, status_code=response.status_code,
            error=f"HTTP {response.status_code}", latency_ms=latency_ms,
            headers=response.headers,
        )
        return []
    try:
        meals = response.json().get("meals") or []
    except (AttributeError, TypeError, ValueError):
        api_usage.record_request(
            "themealdb", ok=False, error="invalid_json", latency_ms=latency_ms,
            headers=response.headers,
        )
        return []
    meals = [meal for meal in meals if isinstance(meal, dict)]
    api_usage.record_request(
        "themealdb", ok=True, latency_ms=latency_ms, headers=response.headers,
    )
    util.ttl_set("themealdb", cache_key, meals)
    return meals


def _ingredients(meal: dict) -> list[dict]:
    result = []
    for index in range(1, 21):
        name = " ".join(str(meal.get(f"strIngredient{index}") or "").split())
        measure = " ".join(str(meal.get(f"strMeasure{index}") or "").split())
        if name:
            result.append({"name": name, "measure": measure})
    return result


def normalize_meal(meal: dict) -> dict:
    return {
        "id": str(meal.get("idMeal") or "").strip(),
        "name": str(meal.get("strMeal") or "").strip(),
        "category": str(meal.get("strCategory") or "").strip(),
        "area": str(meal.get("strArea") or "").strip(),
        "instructions": " ".join(str(meal.get("strInstructions") or "").split()),
        "ingredients": _ingredients(meal),
        "thumbnail": str(meal.get("strMealThumb") or "").strip(),
        "youtube": str(meal.get("strYoutube") or "").strip(),
        "source_url": str(meal.get("strSource") or "").strip(),
    }


def lookup(meal_id: str) -> dict | None:
    if not str(meal_id or "").isdigit():
        return None
    meals = _request("lookup.php", {"i": str(meal_id)}, ttl=_RECIPE_TTL)
    return normalize_meal(meals[0]) if meals else None


def _filter(field: str, value: str) -> list[dict]:
    if field not in {"c", "a", "i"} or not str(value or "").strip():
        return []
    return _request("filter.php", {field: str(value).strip()})


def _ingredient_keys(values: str) -> list[str]:
    text = str(values or "").lower().replace("ё", "е")
    result = []
    for pattern, key in _INGREDIENT_ALIASES:
        if re.search(pattern, text, re.I) and key not in result:
            result.append(key)
    return result[:3]


def _categories(meal_type: str) -> list[str]:
    text = str(meal_type or "").lower()
    if "завтрак" in text or "breakfast" in text:
        return ["Breakfast"]
    if "обед" in text or "lunch" in text:
        return ["Chicken", "Vegetarian", "Pasta", "Seafood"]
    if "ужин" in text or "dinner" in text:
        return ["Seafood", "Chicken", "Beef", "Vegetarian", "Pasta"]
    return ["Vegetarian", "Chicken", "Seafood", "Pasta", "Beef"]


def source_recipes(meal_type: str, *, ingredients: str = "", limit: int = 10,
                   avoid=()) -> list[dict]:
    """Получает реальные рецепты-источники; Gemini лишь выбирает и адаптирует их."""
    limit = max(1, min(int(limit or 1), 12))
    stubs = []
    ingredient_keys = _ingredient_keys(ingredients)
    if ingredient_keys:
        for ingredient in ingredient_keys:
            stubs.extend(_filter("i", ingredient))
    if not stubs:
        for category in _categories(meal_type):
            stubs.extend(_filter("c", category))
            if len(stubs) >= limit * 3:
                break
    unique = {}
    for stub in stubs:
        meal_id = str(stub.get("idMeal") or "").strip()
        if meal_id:
            unique.setdefault(meal_id, stub)
    avoided = {str(name or "").strip().casefold() for name in (avoid or []) if str(name or "").strip()}
    candidates = [
        stub for stub in unique.values()
        if str(stub.get("strMeal") or "").strip().casefold() not in avoided
    ]
    salt = f"{date.today().isoformat()}:{'|'.join(sorted(avoided))}:{meal_type}:{ingredients}"
    candidates.sort(key=lambda stub: hashlib.sha256(
        f"{salt}:{stub.get('idMeal', '')}".encode("utf-8")
    ).hexdigest())
    result = []
    for stub in candidates[:limit]:
        meal = lookup(str(stub.get("idMeal") or ""))
        if meal and meal.get("name") and meal.get("ingredients") and meal.get("instructions"):
            result.append(meal)
    return result
