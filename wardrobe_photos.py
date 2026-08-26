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


def _purchase_query(item, audience="neutral"):
    name = " ".join(str(item or "").split()).strip().casefold()
    for markers, query in _PURCHASE_QUERIES:
        if any(marker in name for marker in markers):
            break
    else:
        query = "minimal clothing item"
    if audience == "male":
        return f"men {query}"
    if audience == "female":
        return f"women {query}"
    return query


@lru_cache(maxsize=256)
def purchase_photo(item, audience="neutral", variant=0):
    """Возвращает одно фото Pexels или None; повторный запрос кэшируется."""
    name = " ".join(str(item or "").split()).strip()
    if not name:
        return None
    return pexels_photo(
        _purchase_query(name, audience), strict=False, first_result=True,
        result_index=max(0, int(variant)),
    )
