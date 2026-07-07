"""Fast recipe photo lookup through Pexels only.

The provider is intentionally small: at most three sequential Pexels searches
per recipe, `per_page=1`, and the first returned photo wins. There is no
extra validation or secondary photo sources.
"""
import logging
import re
import time

import pexels

_log = logging.getLogger(__name__)

_MAX_QUERIES = 3


def _log_event(event: str, **fields) -> None:
    kv = " ".join(f'{k}="{v}"' if k == "query" else f"{k}={v}" for k, v in fields.items())
    _log.info("%s %s", event, kv)


def _recipe_id(recipe: dict):
    return recipe.get("id") or recipe.get("recipe_id") or recipe.get("name_en") or recipe.get("name") or "-"


def _clean_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip())


def _query_chain(recipe: dict) -> list[str]:
    queries = []

    def add(query: str) -> None:
        query = _clean_query(query)
        if query and query not in queries:
            queries.append(query)

    add(recipe.get("photo_query_en"))
    for query in (recipe.get("photo_fallback_queries") or [])[:2]:
        add(query)
    return queries[:_MAX_QUERIES]


def _selected_photo_url(photo: dict) -> str | None:
    src = photo.get("src") if isinstance(photo, dict) else None
    if not isinstance(src, dict):
        return None
    return src.get("large")


def _mark_found(recipe: dict, photo: dict, query: str, fallback_index: int) -> dict:
    result = {
        "photo_source": "pexels",
        "photo_id": photo.get("id"),
        "photo_url": _selected_photo_url(photo),
        "photo_query_used": query,
        "photo_fallback_index": fallback_index,
        "photo_lookup_status": "found",
    }
    recipe.update(result)
    return result


def _mark_not_found(recipe: dict, query: str | None, fallback_index: int) -> None:
    recipe.update({
        "photo_source": None,
        "photo_id": None,
        "photo_url": None,
        "photo_query_used": query,
        "photo_fallback_index": fallback_index,
        "photo_lookup_status": "not_found",
    })


def _mark_error(recipe: dict, query: str | None, fallback_index: int, reason: str) -> None:
    recipe.update({
        "photo_source": None,
        "photo_id": None,
        "photo_url": None,
        "photo_query_used": query,
        "photo_fallback_index": fallback_index,
        "photo_lookup_status": "error",
        "photo_lookup_error": reason,
    })


def get_dish_photo(recipe: dict, meal_type: str = "") -> dict | None:
    """Return a saved or newly selected Pexels photo for one recipe.

    `meal_type` is kept for caller compatibility and is not used for search.
    """
    status = recipe.get("photo_lookup_status")
    if status == "found" and recipe.get("photo_url"):
        return {
            "photo_source": recipe.get("photo_source") or "pexels",
            "photo_id": recipe.get("photo_id"),
            "photo_url": recipe.get("photo_url"),
            "photo_query_used": recipe.get("photo_query_used"),
            "photo_fallback_index": recipe.get("photo_fallback_index", 0),
            "photo_lookup_status": "found",
        }
    if status == "not_found":
        return None

    queries = _query_chain(recipe)
    recipe_id = _recipe_id(recipe)
    if not queries:
        _mark_not_found(recipe, None, -1)
        _log_event("recipe_photo_not_found", recipe_id=recipe_id, query="", fallback_index=-1,
                   reason="empty_results", elapsed_ms=0)
        return None

    start = time.monotonic()
    last_query = None
    last_index = -1
    for fallback_index, query in enumerate(queries):
        last_query = query
        last_index = fallback_index
        try:
            photos = pexels.search_photos(
                query=query,
                orientation="square",
                size="large",
                locale="en-US",
                per_page=1,
                timeout=3,
            )
        except pexels.PexelsTimeoutError:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            _mark_error(recipe, query, fallback_index, "timeout")
            _log_event("recipe_photo_error", recipe_id=recipe_id, query=query,
                       fallback_index=fallback_index, reason="timeout", elapsed_ms=elapsed_ms)
            return None
        except pexels.PexelsNetworkError:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            _mark_error(recipe, query, fallback_index, "network_error")
            _log_event("recipe_photo_error", recipe_id=recipe_id, query=query,
                       fallback_index=fallback_index, reason="network_error", elapsed_ms=elapsed_ms)
            return None
        except pexels.PexelsApiError:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            _mark_error(recipe, query, fallback_index, "api_error")
            _log_event("recipe_photo_error", recipe_id=recipe_id, query=query,
                       fallback_index=fallback_index, reason="api_error", elapsed_ms=elapsed_ms)
            return None
        except pexels.PexelsInvalidResponseError:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            _mark_error(recipe, query, fallback_index, "invalid_response")
            _log_event("recipe_photo_error", recipe_id=recipe_id, query=query,
                       fallback_index=fallback_index, reason="invalid_response", elapsed_ms=elapsed_ms)
            return None

        if photos:
            selected_photo = photos[0]
            photo_url = _selected_photo_url(selected_photo)
            if not photo_url:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                _mark_error(recipe, query, fallback_index, "invalid_response")
                _log_event("recipe_photo_error", recipe_id=recipe_id, query=query,
                           fallback_index=fallback_index, reason="invalid_response", elapsed_ms=elapsed_ms)
                return None
            result = _mark_found(recipe, selected_photo, query, fallback_index)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            _log_event("recipe_photo_found", recipe_id=recipe_id, query=query,
                       fallback_index=fallback_index, photo_id=result.get("photo_id"),
                       elapsed_ms=elapsed_ms)
            return result

    elapsed_ms = int((time.monotonic() - start) * 1000)
    _mark_not_found(recipe, last_query, last_index)
    _log_event("recipe_photo_not_found", recipe_id=recipe_id, query=last_query or "",
               fallback_index=last_index, reason="empty_results", elapsed_ms=elapsed_ms)
    return None
