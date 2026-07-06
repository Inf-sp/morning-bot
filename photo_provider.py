"""Единый photo-provider для карточек рецептов: Pexels (основной источник) →
Unsplash (fallback) → без фото (плейсхолдера нет по решению спеки — карточка
остаётся текстовой, как и раньше).

Архитектура:
  get_dish_photo(recipe) -> dict | None
    1. Строит fallback-цепочку текстовых запросов из полей рецепта (§ логика 6).
    2. Прогоняет всю цепочку через Pexels; из всех найденных фото выбирает
       лучшее по score(). Если лучший score >= _SCORE_THRESHOLD — готово.
    3. Иначе прогоняет ту же цепочку через Unsplash, снова берёт лучшее по score.
    4. Результат (или отрицательный факт «фото не найдено») кэшируется в БД по
       нормализованному ключу первого (самого точного) запроса цепочки — один
       и тот же рецепт у разных пользователей не тратит лимиты API повторно.

Возвращаемый dict: photo_url, thumb_url, photographer, photographer_url,
source ("pexels"|"unsplash"), source_url, query_used, score.
None — если ни один источник не дал фото с приемлемым score (тогда вызывающий
код отправляет карточку без фото, как и раньше).

Синхронный модуль (requests внутри pexels.py/unsplash.py) — вызывающая сторона
сама оборачивает get_dish_photo в asyncio.to_thread, как и раньше с unsplash.
"""
import logging
import re

import config
import pexels
import store
import unsplash

_log = logging.getLogger(__name__)

# Средний порог (см. интервью со пользователем): отсеивает явный мусор (сырые
# ингредиенты, люди/лица как главный объект, пустая кухня/стол), но не настолько
# строгий, чтобы редкие кухни массово улетали в placeholder/пустоту.
_SCORE_THRESHOLD = 2

_POSITIVE_WORDS = (
    "dish", "food", "meal", "plate", "plated", "served", "serving", "cuisine",
    "cooked", "recipe", "delicious", "gourmet", "bowl of", "dinner", "lunch",
    "breakfast", "table setting",
)
_NEGATIVE_WORDS = (
    "raw", "uncooked", "ingredient", "ingredients", "market", "grocery",
    "person", "people", "hand", "hands", "chef", "cook ", "cooking process",
    "kitchen interior", "restaurant interior", "empty plate", "abstract",
    "portrait", "face", "child", "woman", "man ", "farm", "field",
)


def _normalize_query(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip().lower())
    return text


def _fallback_queries(recipe: dict) -> list:
    """Строит цепочку запросов от самого точного к самому общему (логика §6).

    1. точное блюдо + кухня        -> photo_query (или name + cuisine)
    2. главные ингредиенты + тип   -> main_ingredients_en + dish_type_en
    3. тип блюда + meal type       -> dish_type_en + meal_type_en
    4. кухня + meal type           -> cuisine + meal_type_en
    5. общий food-запрос           -> dish_type_en/meal_type_en/"food"/"dish"
    """
    name = str(recipe.get("name") or "").strip()
    cuisine = str(recipe.get("cuisine") or "").strip()
    photo_query = str(recipe.get("photo_query") or "").strip()
    main_ingredients = str(recipe.get("main_ingredients_en") or "").strip()
    dish_type = str(recipe.get("dish_type_en") or "").strip()
    meal_type = str(recipe.get("meal_type_en") or "").strip()

    queries = []

    def add(q):
        q = _normalize_query(q)
        if q and q not in queries:
            queries.append(q)

    add(photo_query or f"{name} {cuisine} food".strip())
    if main_ingredients or dish_type:
        add(f"{main_ingredients} {dish_type} dish".strip())
    if dish_type or meal_type:
        add(f"{dish_type} {meal_type} food".strip())
    if cuisine or meal_type:
        add(f"{cuisine} {meal_type} food".strip())
    add(f"{dish_type or meal_type or 'homemade'} food dish".strip())

    return queries


def _text_blob(photo: dict, source: str) -> str:
    if source == "pexels":
        return str(photo.get("alt") or "")
    parts = [photo.get("description") or "", photo.get("alt_description") or ""]
    return " ".join(p for p in parts if p)


def _score_photo(photo: dict, source: str, recipe: dict, query: str) -> int:
    """Текстовый scoring по описанию/alt фото — нет доступа к image-recognition,
    поэтому сигналы №2/3 из логики §10 (совпадение с названием/ингредиентами,
    «похоже на готовое блюдо») и штрафы за людей/сырые продукты/абстракцию
    оцениваются по ключевым словам в alt/description, а не по пикселям."""
    blob = _text_blob(photo, source).lower()
    if not blob:
        return 0  # нет описания — ни подтвердить, ни опровергнуть релевантность

    score = 0
    name_words = [w for w in re.findall(r"[a-zA-Zа-яА-Я]+", recipe.get("name") or "") if len(w) > 3]
    query_words = [w for w in query.split() if len(w) > 2]
    ingredient_words = [
        w.strip() for w in str(recipe.get("main_ingredients_en") or "").split(",") if w.strip()
    ]

    if any(w.lower() in blob for w in query_words):
        score += 1
    if any(w.lower() in blob for w in ingredient_words):
        score += 1
    if any(w.lower() in blob for w in name_words):
        score += 1
    if any(w in blob for w in _POSITIVE_WORDS):
        score += 2

    for w in _NEGATIVE_WORDS:
        if w in blob:
            score -= 2

    return score


def _best_photo(photos_by_query: list, recipe: dict, source: str):
    """photos_by_query — список (query, [raw_photo, ...]); возвращает (best_photo, query, score) или None."""
    best = None
    for query, photos in photos_by_query:
        for photo in photos:
            s = _score_photo(photo, source, recipe, query)
            if best is None or s > best[2]:
                best = (photo, query, s)
    return best


def _to_result(photo: dict, source: str, query: str, score: int) -> dict:
    if source == "pexels":
        src = photo.get("src") or {}
        return {
            "photo_url": src.get("large") or src.get("medium") or src.get("original"),
            "thumb_url": src.get("small") or src.get("tiny"),
            "photographer": photo.get("photographer") or "",
            "photographer_url": photo.get("photographer_url") or "",
            "source": "pexels",
            "source_url": photo.get("url") or "",
            "query_used": query,
            "score": score,
        }
    urls = photo.get("urls") or {}
    user = photo.get("user") or {}
    links = photo.get("links") or {}
    return {
        "photo_url": urls.get("regular") or urls.get("small"),
        "thumb_url": urls.get("thumb") or urls.get("small"),
        "photographer": user.get("name") or "",
        "photographer_url": (user.get("links") or {}).get("html") or "",
        "source": "unsplash",
        "source_url": links.get("html") or "",
        "query_used": query,
        "score": score,
    }


def _cache_key(recipe: dict, queries: list) -> str:
    """Кэшируем по recipe_slug, если он есть (устойчивее к вариациям текста),
    иначе — по нормализованному самому точному запросу цепочки."""
    slug = str(recipe.get("recipe_slug") or "").strip().lower()
    if slug:
        return f"slug:{slug}"
    return f"query:{queries[0] if queries else ''}"


def _cache_get(key: str):
    cache = store._load(config.DISH_PHOTO_CACHE_KEY)
    return cache.get(key)


def _cache_set(key: str, value) -> None:
    cache = store._load(config.DISH_PHOTO_CACHE_KEY)
    cache[key] = value
    store._save(config.DISH_PHOTO_CACHE_KEY, cache)


def get_dish_photo(recipe: dict) -> dict | None:
    """Подбирает фото готового блюда для карточки рецепта (см. модуль docstring).

    recipe — dict рецепта из батч-генерации (name, cuisine, photo_query,
    main_ingredients_en, dish_type_en, meal_type_en, ...).
    Возвращает dict с photo_url/thumb_url/photographer/... или None.
    """
    queries = _fallback_queries(recipe)
    if not queries:
        return None

    key = _cache_key(recipe, queries)
    cached = _cache_get(key)
    if cached is not None:
        return cached or None  # кэш хранит и «не найдено» (False) — не бьём API повторно

    pexels_hits = [(q, pexels.search_photos(q)) for q in queries]
    pexels_hits = [(q, ph) for q, ph in pexels_hits if ph]
    best = _best_photo(pexels_hits, recipe, "pexels")

    if not best or best[2] < _SCORE_THRESHOLD:
        unsplash_hits = [(q, unsplash.search_photos(q)) for q in queries]
        unsplash_hits = [(q, ph) for q, ph in unsplash_hits if ph]
        unsplash_best = _best_photo(unsplash_hits, recipe, "unsplash")
        if unsplash_best and (not best or unsplash_best[2] > best[2]):
            best, source = unsplash_best, "unsplash"
        elif best:
            source = "pexels"
        else:
            source = None
    else:
        source = "pexels"

    if not best or best[2] < _SCORE_THRESHOLD or source is None:
        _cache_set(key, False)
        return None

    photo, query, score = best
    result = _to_result(photo, source, query, score)
    if not result.get("photo_url"):
        _cache_set(key, False)
        return None
    _cache_set(key, result)
    return result
