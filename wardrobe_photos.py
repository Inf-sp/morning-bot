"""Подтверждённые фото рекомендованной покупки из Shopping и Pexels."""

from functools import lru_cache
import re
import time

import requests

import api_usage
import config
import provider_runtime
from travel_photos import pexels_photo


_PURCHASE_QUERIES = (
    (("пиджак", "блейзер"), "oversized blazer"),
    (("лофер",), "leather loafers"),
    (("жилет",), "knitted vest"),
    (("рубаш",), "oversized shirt"),
    (("сумк",), "crossbody bag"),
    (("ремень",), "leather belt"),
    (("брюк",), "straight leg trousers"),
    (("джинс",), "wide leg jeans"),
    (("худи",), "hoodie"),
    (("ботин",), "ankle boots"),
    (("кед", "кроссов"), "sneakers"),
    (("пальто",), "coat"),
    (("куртк",), "jacket"),
)

_PURCHASE_COLORS = (
    (("нейтраль",), "neutral"),
    (("тёмно-син", "темно-син"), "navy"),
    (("молочн", "кремов"), "cream"),
    (("графитов",), "charcoal"),
    (("оливков",), "olive"),
    (("бордов",), "burgundy"),
    (("сер",), "gray"),
    (("бел",), "white"),
    (("чёрн", "черн"), "black"),
    (("бежев",), "beige"),
    (("коричнев",), "brown"),
    (("син",), "blue"),
    (("зелён", "зелен"), "green"),
)

_TYPE_TOKENS = (
    (("пиджак", "блейзер"), ("blazer", "jacket")),
    (("лофер",), ("loafer", "loafers")),
    (("жилет",), ("vest",)),
    (("рубаш",), ("shirt",)),
    (("сумк",), ("bag",)),
    (("ремень",), ("belt",)),
    (("брюк",), ("trouser", "trousers", "pants")),
    (("джинс",), ("jeans", "denim")),
    (("худи",), ("hoodie", "sweatshirt")),
    (("ботин",), ("boot", "boots")),
    (("кед", "кроссов"), ("sneaker", "sneakers")),
    (("пальто",), ("coat",)),
    (("куртк", "ветровк"), ("jacket", "windbreaker")),
)

_COLOR_TOKENS = {
    "neutral": (
        "neutral", "beige", "tan", "gray", "grey", "black", "navy",
        "brown", "cream", "white",
    ),
    "navy": ("navy", "dark blue"),
    "cream": ("cream", "off-white", "ivory"),
    "charcoal": ("charcoal", "dark gray", "dark grey"),
    "olive": ("olive",),
    "burgundy": ("burgundy", "wine red"),
    "gray": ("gray", "grey"),
    "white": ("white",),
    "black": ("black",),
    "beige": ("beige", "tan"),
    "brown": ("brown",),
    "blue": ("blue",),
    "green": ("green",),
}

_POPULAR_BRANDS = {
    "uniqlo": "Uniqlo", "cos": "COS", "arket": "Arket", "weekday": "Weekday",
    "zara": "Zara", "mango": "Mango", "h&m": "H&M", "massimo dutti": "Massimo Dutti",
    "selected homme": "Selected Homme", "levi's": "Levi’s", "levis": "Levi’s",
    "nike": "Nike", "adidas": "Adidas", "new balance": "New Balance",
}
_EXPENSIVE_BRANDS = (
    "gucci", "prada", "balenciaga", "bottega veneta", "saint laurent",
    "louis vuitton", "burberry", "moncler", "tom ford", "brunello cucinelli",
)
_MAX_PRODUCT_PRICE_EUR = 200


def _english_color(name):
    for markers, color in _PURCHASE_COLORS:
        if any(marker in name for marker in markers):
            return color
    return ""


def _purchase_query(item, audience="neutral"):
    name = " ".join(str(item or "").split()).strip().casefold()
    for markers, query in _PURCHASE_QUERIES:
        if any(marker in name for marker in markers):
            break
    else:
        query = "minimal clothing item"
    color = _english_color(name)
    if color:
        query = f"{color} {query}"
    if audience == "male":
        return f"men {query}"
    if audience == "female":
        return f"women {query}"
    return query


def _photo_matches_item(item, photo):
    """Консервативно подтверждает тип и цвет вещи по описанию Pexels."""
    name = " ".join(str(item or "").split()).strip().casefold()
    alt = " ".join(str((photo or {}).get("alt") or "").split()).strip().casefold()
    if not name or not alt:
        return False
    expected_types = ()
    for markers, tokens in _TYPE_TOKENS:
        if any(marker in name for marker in markers):
            expected_types = tokens
            break
    if not expected_types or not any(token in alt for token in expected_types):
        return False
    color = _english_color(name)
    return not color or any(token in alt for token in _COLOR_TOKENS.get(color, (color,)))


def _popular_brand(row):
    text = " ".join(str((row or {}).get(field) or "") for field in ("title", "source")).casefold()
    if any(brand in text for brand in _EXPENSIVE_BRANDS):
        return ""
    return next((label for marker, label in _POPULAR_BRANDS.items() if marker in text), "")


def _affordable(row):
    extracted = (row or {}).get("extracted_price")
    if isinstance(extracted, (int, float)):
        return float(extracted) <= _MAX_PRODUCT_PRICE_EUR
    price = str(extracted or (row or {}).get("price") or "")
    match = re.search(r"\d[\d.,]*", price.replace(" ", ""))
    if not match:
        return True
    normalized = match.group(0)
    if "," in normalized and "." not in normalized:
        tail = normalized.rsplit(",", 1)[1]
        normalized = normalized.replace(",", "" if len(tail) == 3 else ".")
    elif "." in normalized and "," not in normalized:
        tail = normalized.rsplit(".", 1)[1]
        if len(tail) == 3:
            normalized = normalized.replace(".", "")
    elif "," in normalized and "." in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    try:
        return float(normalized) <= _MAX_PRODUCT_PRICE_EUR
    except ValueError:
        return True


def _high_quality(photo):
    """Отклоняет известные маленькие изображения; неизвестный размер проверит Telegram."""
    try:
        width = int((photo or {}).get("width") or 0)
        height = int((photo or {}).get("height") or 0)
    except (TypeError, ValueError):
        return False
    if not width and not height:
        return True
    return width >= 1000 and height >= 1000


def _serpapi_purchase_photo(item, audience="neutral"):
    """Возвращает первый подтверждённый товар Google Shopping для Нидерландов."""
    if not config.SERP_API_KEY:
        return None
    query = _purchase_query(item, audience)
    started = time.monotonic()
    try:
        response = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_shopping",
                "q": query,
                "gl": "nl",
                "hl": "en",
                "api_key": config.SERP_API_KEY,
            },
            timeout=15,
        )
        ok = response.status_code == 200
        latency_ms = int((time.monotonic() - started) * 1000)
        api_usage.record_request(
            "serpapi", ok=ok, status_code=response.status_code,
            error="" if ok else f"HTTP {response.status_code}",
            headers=response.headers, latency_ms=latency_ms,
        )
        provider_runtime.record_result(
            "serpapi", ok, status_code=response.status_code,
            error="" if ok else f"HTTP {response.status_code}",
            headers=response.headers, latency_ms=latency_ms,
        )
        if not ok:
            return None
        for row in response.json().get("shopping_results") or []:
            if not isinstance(row, dict):
                continue
            title = " ".join(str(row.get("title") or "").split()).strip()
            url = str(row.get("serpapi_thumbnail") or row.get("thumbnail") or "").strip()
            brand = _popular_brand(row)
            photo_meta = {
                "alt": title,
                "width": row.get("thumbnail_width") or row.get("image_width"),
                "height": row.get("thumbnail_height") or row.get("image_height"),
            }
            if (not title or not url or not brand or not _affordable(row)
                    or not _photo_matches_item(item, photo_meta) or not _high_quality(photo_meta)):
                continue
            return {
                "provider": "serpapi",
                "id": str(row.get("product_id") or row.get("position") or ""),
                "url": url,
                "page_url": str(row.get("product_link") or row.get("link") or ""),
                "alt": title,
                "brand": brand,
                "source": str(row.get("source") or ""),
                "query": query,
            }
    except Exception as exc:
        api_usage.record_request("serpapi", ok=False, error=type(exc).__name__)
        provider_runtime.record_result("serpapi", False, error=type(exc).__name__)
    return None


@lru_cache(maxsize=256)
def purchase_photo(item, audience="neutral", variant=0):
    """Возвращает подтверждённое фото товара; повторный запрос кэшируется."""
    name = " ".join(str(item or "").split()).strip()
    if not name:
        return None
    shopping = _serpapi_purchase_photo(name, audience)
    if shopping:
        return shopping
    fallback = pexels_photo(
        _purchase_query(name, audience), strict=False, first_result=True,
        result_index=max(0, int(variant)),
        result_validator=lambda photo: (
            _photo_matches_item(name, photo) and _high_quality(photo)
        ),
    )
    if fallback and config.SERP_API_KEY:
        provider_runtime.activate_fallback("serpapi", "pexels", reason="request")
    return fallback
