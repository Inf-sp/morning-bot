"""Фото рекомендованной покупки из Pexels."""

from functools import lru_cache

from travel_photos import pexels_photo


_PURCHASE_QUERIES = (
    (("пиджак", "блейзер"), "oversized blazer fashion outfit"),
    (("лофер",), "leather loafers fashion"),
    (("жилет",), "knitted vest fashion outfit"),
    (("рубаш",), "oversized shirt fashion outfit"),
    (("сумк",), "crossbody bag fashion"),
    (("ремень",), "leather belt fashion"),
    (("джинс",), "wide leg jeans fashion"),
    (("худи",), "hoodie fashion outfit"),
    (("ботин",), "ankle boots fashion"),
    (("кед", "кроссов"), "sneakers fashion outfit"),
    (("пальто",), "coat fashion outfit"),
    (("куртк",), "jacket fashion outfit"),
)


def _purchase_query(item, audience="neutral"):
    name = " ".join(str(item or "").split()).strip().casefold()
    for markers, query in _PURCHASE_QUERIES:
        if any(marker in name for marker in markers):
            break
    else:
        query = "minimal clothing fashion item"
    if audience == "male":
        return f"male fashion model wearing {query}"
    if audience == "female":
        return f"women wearing {query}"
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
