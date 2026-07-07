"""Единый photo-provider для карточек рецептов: Pexels (основной источник) →
Unsplash (fallback) → без фото (плейсхолдера нет — карточка остаётся текстовой).

Схема, которую ожидает этот модуль от рецепта (см. balance._recipe_batch_prompt):
  name, name_en, cuisine, photo_query_en, photo_fallback_queries (list),
  visual_tags (list), negative_visual_tags (list), ingredients.

ВАЖНО (архитектура ленивого подбора): get_dish_photo вызывается ТОЛЬКО в момент
показа конкретной карточки рецепта (см. balance._send_queue_card), НИКОГДА при
генерации очереди из ~10 рецептов (balance._generate_and_store_queue). Батч-
генерация сохраняет только текстовые поля рецепта, включая photo_query_en/
photo_fallback_queries/visual_tags — сам подбор фото (сетевые вызовы к Pexels/
Unsplash/vision) для непоказанных рецептов не выполняется никогда. Это осознанно
и не должно быть "оптимизировано" обратно в батч — иначе один показанный
пользователю рецепт стоит vision-вызовов за всю десятку сразу.

Архитектура get_dish_photo(recipe, meal_type="") -> dict | None:
  1. Кэш по recipe_photo:v2:{normalized_name_en}:{hash(photo_query_en)} —
     положительный результат живёт 30 дней, отрицательный — 24 часа (§ Кэш).
     Хэш от photo_query_en в ключе гарантирует, что смена поискового запроса
     не подставит устаревший отрицательный результат под старым ключом.
  2. Цепочка запросов: photo_query_en, затем каждый photo_fallback_queries по
     порядку. Общий "food"/"breakfast food" НИКОГДА не добавляется как финальный
     фолбэк — если все явные запросы исчерпаны без успеха, результат None.
  3. Собираем кандидатов через Pexels, считаем локальный текстовый score (только
     чтобы убрать явный мусор и отсортировать — не финальное решение) и проверяем
     через vision.validate_dish_photo не более MAX_PEXELS_VISION_CALLS кандидатов
     (early-stop на первом прошедшем).
  4. Если ни один Pexels-кандидат не прошёл — та же цепочка запросов повторяется
     через Unsplash, vision проверяет не более MAX_UNSPLASH_VISION_CALLS кандидатов.
     Суммарно на один показ — не больше MAX_VISION_CALLS_PER_RECIPE vision-вызовов.
  5. Все сетевые попытки (Pexels/Unsplash/vision) укладываются в общий бюджет
     времени PHOTO_LOOKUP_TIMEOUT_SECONDS — по истечении поиск сразу
     останавливается и результат считается «не найдено» (карточка уходит без фото).
  6. Результат (положительный или отрицательный) кэшируется.

Возвращаемый dict: photo_url, thumb_url, photographer, photographer_url,
source ("pexels"|"unsplash"), source_url, query_used, score (локальный текстовый
score), match_score (vision), pexels_photo_id (для source="pexels").

Синхронный модуль — вызывающая сторона сама оборачивает get_dish_photo в
asyncio.to_thread (как раньше с unsplash.get_dish_photo_url).
"""
import hashlib
import logging
import re
import time

import config
import pexels
import secure
import store
import unsplash
import vision

_log = logging.getLogger(__name__)

# ---------- жёсткие лимиты (см. задачу «Исправь архитектуру подбора фото») ----------
MAX_VISION_CALLS_PER_RECIPE = 3
MAX_PEXELS_VISION_CALLS = 2
MAX_UNSPLASH_VISION_CALLS = 1
PHOTO_LOOKUP_TIMEOUT_SECONDS = 6

assert MAX_PEXELS_VISION_CALLS + MAX_UNSPLASH_VISION_CALLS <= MAX_VISION_CALLS_PER_RECIPE

_CACHE_TTL_POSITIVE_SECONDS = 30 * 24 * 60 * 60  # успешное фото — 30 дней
_CACHE_TTL_NEGATIVE_SECONDS = 24 * 60 * 60        # «не найдено» — 24 часа

_PER_QUERY_CANDIDATES = 15
_VISION_MATCH_SCORE_MIN = 78
_LOCAL_SCORE_FALLBACK_MIN = 7

_POSITIVE_WORDS = (
    "dish", "food", "meal", "plate", "plated", "served", "serving", "cuisine",
    "cooked", "recipe", "delicious", "gourmet", "bowl of", "dinner", "lunch",
    "breakfast", "prepared",
)
_NEGATIVE_WORDS_STRONG = ("raw", "uncooked", "ingredient", "ingredients", "market", "grocery")
_NEGATIVE_WORDS_KITCHEN = ("chef", "cook ", "cooking process", "kitchen interior", "restaurant interior", "kitchen")
_NEGATIVE_WORDS_STAGING = ("empty plate", "table setting", "cutlery only", "drink", "beverage")
_NEGATIVE_WORDS_UNRELATED = ("person", "people", "hand", "hands", "portrait", "face", "abstract", "landscape")

_MIN_PIXELS_FOR_TELEGRAM = 400  # минимальная сторона, чтобы фото не выглядело мыльным в карточке


def _log_event(event: str, **fields) -> None:
    """Структурный лог без секретов/полных персональных данных — только id/score/query."""
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    _log.info("%s %s", event, kv)


def _normalize_name(name_en: str) -> str:
    return re.sub(r"\s+", " ", (name_en or "").strip().lower())


def _cache_key(recipe: dict) -> str | None:
    """recipe_photo:v2:{normalized_name_en}:{hash(photo_query_en)} — хэш запроса
    в ключе гарантирует, что смена photo_query_en не подставит чужой (в т.ч.
    устаревший отрицательный) результат под тем же именем блюда."""
    name_en = _normalize_name(recipe.get("name_en") or "")
    if not name_en:
        return None
    query = _normalize_name(recipe.get("photo_query_en") or "")
    query_hash = hashlib.sha1(query.encode("utf-8")).hexdigest()[:12]
    return f"recipe_photo:v2:{name_en}:{query_hash}"


def _cache_get(key: str):
    cache = store._load(config.DISH_PHOTO_CACHE_KEY)
    entry = cache.get(key)
    if not entry:
        return None
    value = entry.get("value")
    saved_at = entry.get("saved_at") or 0
    ttl = _CACHE_TTL_POSITIVE_SECONDS if value else _CACHE_TTL_NEGATIVE_SECONDS
    if time.time() - saved_at > ttl:
        return None  # протух — считаем как отсутствие записи, перезапросим
    return value


def _cache_set(key: str, value) -> None:
    cache = store._load(config.DISH_PHOTO_CACHE_KEY)
    cache[key] = {"value": value, "saved_at": time.time()}
    store._save(config.DISH_PHOTO_CACHE_KEY, cache)


def _query_chain(recipe: dict) -> list:
    """photo_query_en, затем photo_fallback_queries по порядку. Без синтетического
    общего "food"-фолбэка в конце — если явных запросов не хватило, результат None."""
    queries = []

    def add(q):
        q = re.sub(r"\s+", " ", (q or "").strip())
        if q and q not in queries:
            queries.append(q)

    add(recipe.get("photo_query_en"))
    for q in (recipe.get("photo_fallback_queries") or []):
        add(q)
    return queries


def _text_blob(photo: dict, source: str) -> str:
    if source == "pexels":
        return str(photo.get("alt") or "")
    parts = [photo.get("description") or "", photo.get("alt_description") or ""]
    return " ".join(p for p in parts if p)


def _dimensions(photo: dict, source: str):
    # Оба API отдают width/height на верхнем уровне объекта фото (когда отдают).
    return photo.get("width"), photo.get("height")


def _is_square_ish(width, height) -> bool:
    if not width or not height:
        return False
    ratio = width / height if height else 0
    return 0.85 <= ratio <= 1.18


def _score_candidate(photo: dict, source: str, recipe: dict) -> int:
    """Локальный scoring — ТОЛЬКО чтобы убрать явный мусор и отсортировать
    кандидатов перед vision (§ Локальный scoring). Финальное решение принимает
    vision.validate_dish_photo, не этот score — поэтому здесь нет отдельного
    порога-отсечки, только сортировка и лёгкая приоритизация."""
    blob = _text_blob(photo, source).lower()
    name_en = str(recipe.get("name_en") or recipe.get("name") or "").lower()
    ingredient_words = [
        w.strip().lower() for w in str(recipe.get("main_ingredients_en") or recipe.get("ingredients") or "").split(",")
        if w.strip()
    ]
    visual_tags = [str(t).strip().lower() for t in (recipe.get("visual_tags") or []) if str(t).strip()]

    score = 0
    has_strong_negative = False
    if blob:
        if name_en and name_en in blob:
            score += 5
        if any(w and w in blob for w in ingredient_words + visual_tags):
            score += 4
        if any(w in blob for w in _POSITIVE_WORDS):
            score += 3

        if any(w in blob for w in _NEGATIVE_WORDS_STRONG):
            score -= 5
            has_strong_negative = True
        if any(w in blob for w in _NEGATIVE_WORDS_KITCHEN):
            score -= 4
            has_strong_negative = True
        if any(w in blob for w in _NEGATIVE_WORDS_STAGING):
            score -= 4
            has_strong_negative = True
        if any(w in blob for w in _NEGATIVE_WORDS_UNRELATED) and not any(w in blob for w in _POSITIVE_WORDS):
            score -= 8  # похоже, что это вообще не фото готовой еды
            has_strong_negative = True

    # Технические баллы за размер/ориентацию значимы только когда нет явного
    # признака «не то фото» — иначе крупное квадратное фото сырых овощей могло бы
    # компенсировать штраф техническим качеством.
    if not has_strong_negative:
        width, height = _dimensions(photo, source)
        if _is_square_ish(width, height):
            score += 2
        if width and height and min(width, height) >= _MIN_PIXELS_FOR_TELEGRAM:
            score += 2

    # Отсутствие названия блюда в metadata — НЕ штраф: Pexels/Unsplash часто
    # отдают пустой или неполный alt/description даже для отличных фото готовой
    # еды (§ Локальный scoring). Отсутствие текста просто не даёт бонусных баллов.
    return score


def _collect_sorted_candidates(search_fn, queries: list, recipe: dict, source: str, deadline: float) -> list:
    """Прогоняет цепочку запросов через search_fn, считает score для каждого
    кандидата и возвращает ВСЕХ кандидатов, отсортированных по убыванию score
    (обрезка до нужного количества — на стороне вызывающего, зависит от лимита
    vision-вызовов конкретного источника). Останавливается, если истёк общий
    бюджет времени deadline (time.monotonic())."""
    scored = []
    for query in queries:
        if time.monotonic() >= deadline:
            break
        try:
            candidates = search_fn(query, per_page=_PER_QUERY_CANDIDATES)
        except Exception as e:
            _log.warning("photo source search error (%s): %s", source, secure.redact(str(e)))
            candidates = []
        for photo in candidates:
            s = _score_candidate(photo, source, recipe)
            _log_event("recipe_photo_candidate_scored", source=source, query=query, score=s,
                       photo_id=photo.get("id"))
            scored.append((photo, query, s))
    scored.sort(key=lambda t: t[2], reverse=True)
    return scored


def _photo_url_for_vision(photo: dict, source: str) -> str | None:
    if source == "pexels":
        src = photo.get("src") or {}
        return src.get("large") or src.get("medium") or src.get("original")
    urls = photo.get("urls") or {}
    return urls.get("regular") or urls.get("small")


def _to_result(photo: dict, source: str, query: str, score: int, vision_result: dict) -> dict:
    photo_url = _photo_url_for_vision(photo, source)
    if source == "pexels":
        return {
            "photo_url": photo_url,
            "thumb_url": (photo.get("src") or {}).get("small") or (photo.get("src") or {}).get("tiny"),
            "photographer": photo.get("photographer") or "",
            "photographer_url": photo.get("photographer_url") or "",
            "source": "pexels",
            "source_url": photo.get("url") or "",
            "pexels_photo_id": photo.get("id"),
            "query_used": query,
            "score": score,
            "match_score": vision_result.get("match_score"),
        }
    urls = photo.get("urls") or {}
    user = photo.get("user") or {}
    links = photo.get("links") or {}
    return {
        "photo_url": photo_url,
        "thumb_url": urls.get("thumb") or urls.get("small"),
        "photographer": user.get("name") or "",
        "photographer_url": (user.get("links") or {}).get("html") or "",
        "source": "unsplash",
        "source_url": links.get("html") or "",
        "pexels_photo_id": None,
        "query_used": query,
        "score": score,
        "match_score": vision_result.get("match_score"),
    }


def _vision_passes(vision_result: dict | None) -> bool:
    """§ Vision-условие: contains_main_ingredients — доп. сигнал, НЕ блокирующее
    условие (профессиональные food-фото часто не показывают все ингредиенты)."""
    if not vision_result:
        return False
    return (
        vision_result.get("match_score", 0) >= _VISION_MATCH_SCORE_MIN
        and vision_result.get("is_prepared_dish") is True
        and vision_result.get("has_unrelated_subject") is False
    )


def _find_via_source(search_fn, queries: list, recipe: dict, source: str, meal_type: str,
                      name_en: str, max_vision_calls: int, deadline: float, vision_calls_used: int):
    """Собирает кандидатов по source, прогоняет vision early-stop, не более
    max_vision_calls вызовов и не выходя за общий deadline. vision_calls_used —
    сколько vision-вызовов уже потрачено на других источниках этого показа (для
    соблюдения общего MAX_VISION_CALLS_PER_RECIPE).

    Возвращает (result_dict_или_None, потраченные_vision_вызовы_на_этом_источнике)."""
    candidates = _collect_sorted_candidates(search_fn, queries, recipe, source, deadline)
    calls_made = 0
    remaining_total = MAX_VISION_CALLS_PER_RECIPE - vision_calls_used
    budget = min(max_vision_calls, remaining_total)

    # OpenAI vision отключён: сохраняем фото-карточки через строгий локальный score.
    # Берём только лучший кандидат с явными food-сигналами, без сильных negative-сигналов.
    if candidates and not vision.is_enabled():
        photo, query, score = candidates[0]
        image_url = _photo_url_for_vision(photo, source)
        if image_url and score >= _LOCAL_SCORE_FALLBACK_MIN:
            _log_event("recipe_photo_selected_local", source=source, name_en=name_en,
                       query=query, score=score)
            return _to_result(photo, source, query, score, {"match_score": None}), calls_made

    for photo, query, score in candidates:
        if calls_made >= budget:
            break
        if time.monotonic() >= deadline:
            _log_event("recipe_photo_not_found", name_en=name_en, reason="timeout", source=source)
            break
        image_url = _photo_url_for_vision(photo, source)
        if not image_url:
            continue
        vision_result = vision.validate_dish_photo(image_url, recipe, meal_type)
        calls_made += 1
        if _vision_passes(vision_result):
            _log_event("recipe_photo_selected", source=source, name_en=name_en, query=query,
                       score=score, match_score=vision_result.get("match_score"))
            return _to_result(photo, source, query, score, vision_result), calls_made
        _log_event("recipe_photo_vision_rejected", source=source, name_en=name_en, query=query,
                   score=score, match_score=(vision_result or {}).get("match_score"))
    return None, calls_made


def get_dish_photo(recipe: dict, meal_type: str = "") -> dict | None:
    """Подбирает и валидирует фото готового блюда для карточки ОДНОГО показываемого
    рецепта (см. предупреждение в докстринге модуля — никогда не звать это на всю
    очередь целиком).

    recipe — dict рецепта из батч-генерации (name, name_en, cuisine,
    photo_query_en, photo_fallback_queries, visual_tags, negative_visual_tags, ...).
    meal_type — "breakfast"|"lunch"|"dinner"|"fridge", передаётся в vision-промпт;
    правила поиска и проверки одинаковы для всех четырёх категорий.
    Возвращает dict с photo_url/... или None, если валидное фото не найдено.
    """
    name_en = str(recipe.get("name_en") or recipe.get("name") or "").strip()
    key = _cache_key(recipe)
    if key:
        cached = _cache_get(key)
        if cached is not None:
            return cached or None  # кэш хранит и «не найдено» (False) — не бьём API повторно

    queries = _query_chain(recipe)
    if not queries:
        _log_event("recipe_photo_not_found", name_en=name_en, reason="no_queries")
        if key:
            _cache_set(key, False)
        return None

    _log_event("recipe_photo_search_started", name_en=name_en, queries=len(queries))
    deadline = time.monotonic() + PHOTO_LOOKUP_TIMEOUT_SECONDS

    result, used = _find_via_source(pexels.search_photos, queries, recipe, "pexels", meal_type,
                                     name_en, MAX_PEXELS_VISION_CALLS, deadline, vision_calls_used=0)
    if result is None and time.monotonic() < deadline:
        result, _ = _find_via_source(unsplash.search_photos, queries, recipe, "unsplash", meal_type,
                                      name_en, MAX_UNSPLASH_VISION_CALLS, deadline, vision_calls_used=used)

    if key:
        _cache_set(key, result if result else False)

    if result is None:
        _log_event("recipe_photo_not_found", name_en=name_en, reason="no_candidate_passed")
        return None
    return result
