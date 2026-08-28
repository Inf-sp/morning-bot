"""Фото рекомендованной покупки из Pexels."""

from functools import lru_cache

from travel_photos import pexels_photo


_PURCHASE_QUERIES = (
    (("пиджак", "блейзер"), "oversized blazer"),
    (("лофер",), "leather loafers"),
    (("жилет",), "knitted vest"),
    (("рубаш",), "oversized shirt"),
    (("сумк",), "crossbody bag"),
    (("ремень",), "leather belt"),
    (("джинс",), "wide leg jeans"),
    (("худи",), "hoodie"),
    (("ботин",), "ankle boots"),
    (("кед", "кроссов"), "sneakers"),
    (("пальто",), "coat"),
    (("куртк",), "jacket"),
)

_PURCHASE_COLORS = (
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
    (("джинс",), ("jeans", "denim")),
    (("худи",), ("hoodie", "sweatshirt")),
    (("ботин",), ("boot", "boots")),
    (("кед", "кроссов"), ("sneaker", "sneakers")),
    (("пальто",), ("coat",)),
    (("куртк", "ветровк"), ("jacket", "windbreaker")),
)

_COLOR_TOKENS = {
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


@lru_cache(maxsize=256)
def purchase_photo(item, audience="neutral", variant=0):
    """Возвращает одно фото Pexels или None; повторный запрос кэшируется."""
    name = " ".join(str(item or "").split()).strip()
    if not name:
        return None
    return pexels_photo(
        _purchase_query(name, audience), strict=False, first_result=True,
        result_index=max(0, int(variant)),
        result_validator=lambda photo: _photo_matches_item(name, photo),
    )
