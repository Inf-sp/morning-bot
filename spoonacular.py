"""Spoonacular: primary recipe source for the Cooking section."""

from __future__ import annotations

import hashlib
import json
import re
import time

import requests

import api_usage
import config
import util


_BASE_URL = "https://api.spoonacular.com"
_SEARCH_TTL = 6 * 60 * 60
_RECIPE_TTL = 7 * 24 * 60 * 60

_INGREDIENT_ALIASES = (
    (r"кур|chicken|kip", "chicken"),
    (r"говяд|beef|rund", "beef"),
    (r"свин|pork|varken", "pork"),
    (r"баран|ягн|lamb", "lamb"),
    (r"лосос|salmon", "salmon"),
    (r"треск|cod", "cod"),
    (r"тун[еe]ц|tuna", "tuna"),
    (r"кревет|shrimp|prawn|garnaal", "shrimp"),
    (r"яйц|egg|eieren", "egg"),
    (r"рис|rice|rijst", "rice"),
    (r"макарон|паст[аы]|pasta", "pasta"),
    (r"картоф|potato|aardappel", "potato"),
    (r"помид|томат|tomato", "tomato"),
    (r"огур|cucumber|komkommer", "cucumber"),
    (r"гриб|mushroom|champignon", "mushroom"),
    (r"сыр|cheese|kaas", "cheese"),
    (r"молок|milk|melk", "milk"),
    (r"сливк|cream|room", "cream"),
    (r"нут|chickpea", "chickpea"),
    (r"чечев|lentil", "lentil"),
    (r"фасол|bean|bonen", "beans"),
    (r"шпинат|spinach", "spinach"),
    (r"баклаж|aubergine|eggplant", "eggplant"),
    (r"кабач|цук+ини|zucchini|courgette", "zucchini"),
    (r"болгарск.*пер|bell pepper|paprika", "bell pepper"),
    (r"лук|onion|ui", "onion"),
    (r"чеснок|garlic|knoflook", "garlic"),
    (r"морков|carrot|wortel", "carrot"),
    (r"брокколи|broccoli", "broccoli"),
    (r"цветн.*капуст|cauliflower", "cauliflower"),
    (r"авокадо|avocado", "avocado"),
    (r"яблок|apple|appel", "apple"),
    (r"банан|banana", "banana"),
    (r"овсян|oat", "oats"),
    (r"хлеб|bread|brood", "bread"),
    (r"масл.*слив|butter|boter", "butter"),
    (r"соев.*соус|soy sauce|sojasaus", "soy sauce"),
)


def _cache_key(path: str, params: dict) -> str:
    payload = json.dumps([path, sorted(params.items())], ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _request(path: str, params=None, *, ttl=_SEARCH_TTL):
    key = str(config.SPOONACULAR_API_KEY or "").strip()
    if not key:
        return None
    safe_path = "/" + str(path or "").strip("/")
    if not re.fullmatch(r"/[A-Za-z0-9/_-]+", safe_path):
        return None
    query = {**(params or {}), "apiKey": key}
    cache_params = {name: value for name, value in query.items() if name != "apiKey"}
    cache_key = _cache_key(safe_path, cache_params)
    cached = util.ttl_get("spoonacular", cache_key, ttl)
    if cached is not None:
        api_usage.record_cache_hit("spoonacular")
        return cached

    started = time.time()
    try:
        response = requests.get(f"{_BASE_URL}{safe_path}", params=query, timeout=10)
    except requests.exceptions.Timeout:
        api_usage.record_request("spoonacular", ok=False, error="timeout")
        return None
    except requests.exceptions.RequestException:
        api_usage.record_request("spoonacular", ok=False, error="network_error")
        return None
    latency_ms = int((time.time() - started) * 1000)
    if response.status_code != 200:
        api_usage.record_request(
            "spoonacular", ok=False, status_code=response.status_code,
            error=f"HTTP {response.status_code}", latency_ms=latency_ms,
            headers=response.headers,
        )
        return None
    try:
        payload = response.json()
    except (TypeError, ValueError):
        api_usage.record_request(
            "spoonacular", ok=False, error="invalid_json", latency_ms=latency_ms,
            headers=response.headers,
        )
        return None
    api_usage.record_request(
        "spoonacular", ok=True, latency_ms=latency_ms, headers=response.headers,
    )
    util.ttl_set("spoonacular", cache_key, payload)
    return payload


def _ingredient_name(value: str) -> str:
    clean = " ".join(str(value or "").lower().split()).strip()
    for pattern, english in _INGREDIENT_ALIASES:
        if re.search(pattern, clean, re.IGNORECASE):
            return english
    return clean


def ingredient_query(values) -> str:
    if isinstance(values, str):
        values = re.split(r"[,;\n]+", values)
    result = []
    for value in values or []:
        name = _ingredient_name(value)
        if name and name not in result:
            result.append(name)
    return ",".join(result[:15])


def _steps(recipe: dict) -> list[str]:
    result = []
    for instruction in recipe.get("analyzedInstructions") or []:
        if not isinstance(instruction, dict):
            continue
        for step in instruction.get("steps") or []:
            text = " ".join(str((step or {}).get("step") or "").split())
            if text:
                result.append(text)
    if result:
        return result
    raw = re.sub(r"<[^>]+>", " ", str(recipe.get("instructions") or ""))
    raw = " ".join(raw.split())
    return [raw] if raw else []


def normalize_recipe(recipe: dict, search_result=None) -> dict:
    search_result = search_result if isinstance(search_result, dict) else {}
    extended = recipe.get("extendedIngredients") or search_result.get("usedIngredients") or []
    ingredients = []
    for item in extended:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("nameClean") or item.get("name") or "").split())
        if not name:
            continue
        ingredients.append({
            "name": name,
            "amount": item.get("amount"),
            "unit": " ".join(str(item.get("unit") or "").split()),
            "original": " ".join(str(item.get("original") or "").split()),
        })
    wine = recipe.get("winePairing") if isinstance(recipe.get("winePairing"), dict) else {}
    return {
        "id": str(recipe.get("id") or search_result.get("id") or "").strip(),
        "name": str(recipe.get("title") or search_result.get("title") or "").strip(),
        "category": ", ".join(recipe.get("dishTypes") or []),
        "area": ", ".join(recipe.get("cuisines") or []),
        "instructions": _steps(recipe),
        "ingredients": ingredients,
        "ready_minutes": recipe.get("readyInMinutes"),
        "servings": recipe.get("servings"),
        "thumbnail": str(recipe.get("image") or search_result.get("image") or "").strip(),
        "used_ingredient_count": search_result.get("usedIngredientCount"),
        "missed_ingredient_count": search_result.get("missedIngredientCount"),
        "missed_ingredients": [
            str(item.get("name") or "").strip()
            for item in search_result.get("missedIngredients") or []
            if isinstance(item, dict) and item.get("name")
        ],
        "pairing_wines": [str(item).strip() for item in wine.get("pairedWines") or [] if str(item).strip()],
        "pairing_text": " ".join(str(wine.get("pairingText") or "").split()),
        "source_provider": "spoonacular",
    }


def _meal_type(value: str) -> str:
    value = str(value or "").lower()
    if "завтрак" in value or "breakfast" in value:
        return "breakfast"
    if "обед" in value or "lunch" in value:
        return "main course"
    if "ужин" in value or "dinner" in value:
        return "main course"
    return "main course"


def source_recipes(meal_type, *, ingredients="", limit=10, avoid=()):
    """Return up to 10 source recipes, ranked by the user's available products."""
    limit = max(1, min(int(limit or 10), 10))
    ingredient_names = ingredient_query(ingredients)
    if ingredient_names:
        found = _request("/recipes/findByIngredients", {
            "ingredients": ingredient_names,
            "number": limit,
            "ranking": 1,
            "ignorePantry": "true",
        })
        candidates = found if isinstance(found, list) else []
    else:
        found = _request("/recipes/complexSearch", {
            "type": _meal_type(meal_type),
            "number": limit,
            "instructionsRequired": "true",
            "sort": "popularity",
        })
        candidates = found.get("results") if isinstance(found, dict) else []
    candidates = [item for item in (candidates or []) if isinstance(item, dict) and item.get("id")]
    avoided = {str(item).strip().casefold() for item in (avoid or []) if str(item).strip()}
    candidates = [item for item in candidates if str(item.get("title") or "").strip().casefold() not in avoided][:limit]
    if not candidates:
        return []

    by_id = {str(item["id"]): item for item in candidates}
    ids = list(by_id)
    details = _request("/recipes/informationBulk", {
        "ids": ",".join(ids),
        "includeNutrition": "false",
        "addWinePairing": "true",
    }, ttl=_RECIPE_TTL)
    details_by_id = {
        str(item.get("id")): item for item in (details or [])
        if isinstance(item, dict) and item.get("id")
    }
    result = []
    for recipe_id in ids:
        detail = details_by_id.get(recipe_id) or by_id[recipe_id]
        normalized = normalize_recipe(detail, by_id[recipe_id])
        if normalized.get("name"):
            result.append(normalized)
    return result
